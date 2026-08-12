<template>
  <section class="admin-section">
    <div class="section-heading">
      <div>
        <h2>订单</h2>
        <p>订单与支付流水只做状态变更，不物理删除</p>
      </div>
      <t-button variant="outline" @click="loadData">
        <template #icon><t-icon name="refresh" /></template>
        刷新
      </t-button>
    </div>
    <div class="toolbar">
      <t-input v-model="keyword" clearable placeholder="订单号或用户名" @enter="applyFilter" />
      <t-select v-model="statusFilter" clearable placeholder="全部状态" @change="applyFilter">
        <t-option value="PENDING" label="待支付" />
        <t-option value="PAID" label="已支付" />
        <t-option value="CLOSED" label="已关闭" />
        <t-option value="REFUNDED" label="已退款" />
      </t-select>
      <t-select v-model="providerFilter" clearable placeholder="全部渠道" @change="applyFilter">
        <t-option value="alipay" label="支付宝" />
        <t-option value="manual" label="人工" />
        <t-option value="mock" label="模拟" />
        <t-option value="redemption_code" label="卡密兑换" />
      </t-select>
      <t-button variant="outline" @click="applyFilter">查询</t-button>
    </div>
    <t-loading :loading="loading">
      <t-table :data="orders" :columns="columns" row-key="id" :pagination="pagination" @page-change="onPageChange">
        <template #price="{ row }">{{ formatMoney(row.price_cents, row.currency) }}</template>
        <template #provider="{ row }">{{ providerLabel(row.provider) }}</template>
        <template #status="{ row }">
          <t-tag :theme="statusTheme(row.status) as any" variant="light">{{ statusLabel(row.status) }}</t-tag>
        </template>
        <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
        <template #paid_at="{ row }">{{ formatDateTime(row.paid_at) }}</template>
        <template #op="{ row }">
          <t-space size="small">
            <t-popconfirm v-if="row.status === 'PENDING' && !['alipay', 'redemption_code'].includes(row.provider)" content="确认已线下收款并开通套餐？" @confirm="action(row, 'mark_paid')">
              <t-link theme="primary">确认收款</t-link>
            </t-popconfirm>
            <t-link v-if="row.status === 'PENDING' && row.provider === 'alipay'" theme="primary" @click="action(row, 'sync')">同步支付</t-link>
            <t-popconfirm v-if="row.status === 'PENDING'" content="关闭后将释放预留席位" @confirm="action(row, 'close')">
              <t-link theme="danger">关闭</t-link>
            </t-popconfirm>
            <t-popconfirm v-if="row.status === 'PAID' && !['alipay', 'redemption_code'].includes(row.provider)" content="退款会立即暂停当前套餐权益" @confirm="action(row, 'refund')">
              <t-link theme="danger">退款</t-link>
            </t-popconfirm>
            <t-link theme="default" @click="openDetail(row)">支付详情</t-link>
          </t-space>
        </template>
      </t-table>
    </t-loading>
    <t-dialog v-model:visible="detailVisible" header="订单与支付流水" width="760px" :footer="false">
      <div v-if="detailOrder" class="payment-detail">
        <div class="detail-summary">
          <span>订单号：{{ detailOrder.order_no }}</span>
          <span>渠道：{{ providerLabel(detailOrder.provider) }}</span>
          <span>支付流水号：{{ detailOrder.provider_order_id || '-' }}</span>
        </div>
        <t-table :data="detailOrder.transactions || []" :columns="transactionColumns" row-key="id" size="small">
          <template #amount="{ row }">{{ formatMoney(row.amount_cents, row.currency) }}</template>
          <template #verified="{ row }"><t-tag :theme="row.signature_verified ? 'success' : 'danger'" variant="light">{{ row.signature_verified ? '已验签' : '未验签' }}</t-tag></template>
          <template #accepted="{ row }"><t-tag :theme="row.accepted ? 'success' : 'warning'" variant="light">{{ row.accepted ? '已处理' : '已拒绝' }}</t-tag></template>
          <template #occurred_at="{ row }">{{ formatDateTime(row.occurred_at) }}</template>
        </t-table>
      </div>
    </t-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime, formatMoney, statusLabel, statusTheme } from '@/utils/billing'

const loading = ref(false)
const orders = ref<any[]>([])
const statusFilter = ref('')
const providerFilter = ref('')
const keyword = ref('')
const detailVisible = ref(false)
const detailOrder = ref<any>(null)
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const columns = [
  { colKey: 'order_no', title: '订单号', width: 190 },
  { colKey: 'username', title: '用户', width: 150 },
  { colKey: 'plan_name', title: '套餐', width: 120 },
  { colKey: 'order_type', title: '类型', width: 90 },
  { colKey: 'provider', title: '渠道', cell: 'provider', width: 90 },
  { colKey: 'price', title: '金额', cell: 'price', width: 110 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 },
  { colKey: 'created_at', title: '创建时间', cell: 'created_at', width: 170 },
  { colKey: 'paid_at', title: '支付时间', cell: 'paid_at', width: 170 },
  { colKey: 'op', title: '操作', cell: 'op', width: 250, fixed: 'right' }
]
const transactionColumns = [
  { colKey: 'provider_transaction_id', title: '支付流水号', minWidth: 180 },
  { colKey: 'event_type', title: '事件', width: 150 },
  { colKey: 'amount', title: '金额', cell: 'amount', width: 100 },
  { colKey: 'verified', title: '验签', cell: 'verified', width: 90 },
  { colKey: 'accepted', title: '处理', cell: 'accepted', width: 90 },
  { colKey: 'occurred_at', title: '时间', cell: 'occurred_at', width: 170 }
]

const providerLabel = (provider: string) => provider === 'alipay' ? '支付宝' : provider === 'manual' ? '人工' : provider === 'mock' ? '模拟' : provider === 'redemption_code' ? '卡密兑换' : provider

const loadData = async () => {
  loading.value = true
  const params = new URLSearchParams({ page: String(pagination.current), page_size: String(pagination.pageSize) })
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (providerFilter.value) params.set('provider', providerFilter.value)
  if (keyword.value.trim()) params.set('q', keyword.value.trim())
  const data = await request(`/0x/admin/orders?${params.toString()}`)
  orders.value = data?.results || []
  pagination.total = data?.count || 0
  loading.value = false
}

const applyFilter = () => {
  pagination.current = 1
  loadData()
}

const action = async (row: any, actionName: string) => {
  const data = await request('/0x/admin/orders', 'POST', { order_id: row.id, action: actionName })
  if (data) {
    MessagePlugin.success(actionName === 'mark_paid' ? '订单已确认并开通' : actionName === 'refund' ? '订单已退款' : actionName === 'sync' ? '已同步支付宝订单状态' : '订单已关闭')
    loadData()
  }
}

const openDetail = (row: any) => {
  detailOrder.value = row
  detailVisible.value = true
}

const onPageChange = (info: any) => {
  pagination.current = info.current
  pagination.pageSize = info.pageSize
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.admin-section { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-bottom: 16px; }
.toolbar .t-select { width: 150px; }
.toolbar .t-input { width: 220px; }
.payment-detail { display: grid; gap: 16px; }
.detail-summary { display: grid; gap: 7px; color: var(--app-text-muted); font-size: 13px; }
.muted { color: var(--app-text-muted); font-size: 13px; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } .toolbar { align-items: stretch; flex-direction: column; } .toolbar .t-select, .toolbar .t-input { width: 100%; } }
</style>
