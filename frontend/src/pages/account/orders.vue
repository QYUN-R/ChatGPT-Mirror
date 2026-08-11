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
      <t-select v-model="statusFilter" clearable placeholder="全部状态" @change="applyFilter">
        <t-option value="PENDING" label="待支付" />
        <t-option value="PAID" label="已支付" />
        <t-option value="CLOSED" label="已关闭" />
        <t-option value="REFUNDED" label="已退款" />
      </t-select>
    </div>
    <t-loading :loading="loading">
      <t-table :data="orders" :columns="columns" row-key="id" :pagination="pagination" @page-change="onPageChange">
        <template #price="{ row }">{{ formatMoney(row.price_cents, row.currency) }}</template>
        <template #status="{ row }">
          <t-tag :theme="statusTheme(row.status) as any" variant="light">{{ statusLabel(row.status) }}</t-tag>
        </template>
        <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
        <template #paid_at="{ row }">{{ formatDateTime(row.paid_at) }}</template>
        <template #op="{ row }">
          <t-space size="small">
            <t-popconfirm v-if="row.status === 'PENDING'" content="确认已线下收款并开通套餐？" @confirm="action(row, 'mark_paid')">
              <t-link theme="primary">确认收款</t-link>
            </t-popconfirm>
            <t-popconfirm v-if="row.status === 'PENDING'" content="关闭后将释放预留席位" @confirm="action(row, 'close')">
              <t-link theme="danger">关闭</t-link>
            </t-popconfirm>
            <t-popconfirm v-if="row.status === 'PAID'" content="退款会立即暂停当前套餐权益" @confirm="action(row, 'refund')">
              <t-link theme="danger">退款</t-link>
            </t-popconfirm>
            <span v-if="!['PENDING', 'PAID'].includes(row.status)" class="muted">无可用操作</span>
          </t-space>
        </template>
      </t-table>
    </t-loading>
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
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const columns = [
  { colKey: 'order_no', title: '订单号', width: 190 },
  { colKey: 'username', title: '用户', width: 150 },
  { colKey: 'plan_name', title: '套餐', width: 120 },
  { colKey: 'order_type', title: '类型', width: 90 },
  { colKey: 'price', title: '金额', cell: 'price', width: 110 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 },
  { colKey: 'created_at', title: '创建时间', cell: 'created_at', width: 170 },
  { colKey: 'paid_at', title: '支付时间', cell: 'paid_at', width: 170 },
  { colKey: 'op', title: '操作', cell: 'op', width: 160, fixed: 'right' }
]

const loadData = async () => {
  loading.value = true
  const params = new URLSearchParams({ page: String(pagination.current), page_size: String(pagination.pageSize) })
  if (statusFilter.value) params.set('status', statusFilter.value)
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
    MessagePlugin.success(actionName === 'mark_paid' ? '订单已确认并开通' : actionName === 'refund' ? '订单已退款' : '订单已关闭')
    loadData()
  }
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
.toolbar { display: flex; justify-content: flex-end; margin-bottom: 16px; }
.toolbar .t-select { width: 160px; }
.muted { color: var(--app-text-muted); font-size: 13px; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } .toolbar { justify-content: flex-start; } }
</style>
