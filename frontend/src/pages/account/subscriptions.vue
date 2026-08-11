<template>
  <section class="admin-section">
    <div class="section-heading">
      <div>
        <h2>用户订阅</h2>
        <p>手动开通、续期、暂停和同池账号迁移</p>
      </div>
      <t-button theme="primary" @click="openGrantDialog">
        <template #icon><t-icon name="add" /></template>
        手动开通
      </t-button>
    </div>
    <div class="toolbar">
      <t-select v-model="statusFilter" clearable placeholder="全部状态" @change="loadData">
        <t-option value="ACTIVE" label="生效中" />
        <t-option value="EXPIRED" label="已到期" />
        <t-option value="SUSPENDED" label="已暂停" />
        <t-option value="REFUNDED" label="已退款" />
      </t-select>
      <t-button variant="outline" @click="loadData">刷新</t-button>
    </div>
    <t-loading :loading="loading">
      <t-table :data="subscriptions" :columns="columns" row-key="id" :pagination="pagination" @page-change="onPageChange">
        <template #status="{ row }">
          <t-tag :theme="statusTheme(row.status) as any" variant="light">{{ statusLabel(row.status) }}</t-tag>
        </template>
        <template #ends_at="{ row }">{{ formatDateTime(row.ends_at) }}</template>
        <template #assignment="{ row }">
          <span v-if="row.assignment">{{ row.assignment.account_name || '已固定绑定' }}</span>
          <span v-else class="muted">尚未首次使用</span>
        </template>
        <template #scheduled="{ row }">{{ row.scheduled_plan_name || '-' }}</template>
        <template #op="{ row }">
          <t-space size="small">
            <t-link theme="primary" @click="openGrantDialog(row)">续期</t-link>
            <t-link theme="primary" :disabled="row.status !== 'ACTIVE'" @click="migrate(row)">迁移账号</t-link>
            <t-popconfirm content="暂停后用户将立即无法调用服务" @confirm="suspend(row)">
              <t-link theme="danger" :disabled="row.status !== 'ACTIVE'">暂停</t-link>
            </t-popconfirm>
          </t-space>
        </template>
      </t-table>
    </t-loading>

    <t-dialog
      :visible="grantDialog"
      :header="grantForm.subscription_id ? '续期套餐' : '手动开通套餐'"
      :confirm-btn="{ loading: submitting }"
      width="540px"
      @confirm="grant"
      @close="grantDialog = false"
    >
      <t-form :data="grantForm" label-width="96px">
        <t-form-item label="用户">
          <t-select v-model="grantForm.user_id" filterable :disabled="Boolean(grantForm.subscription_id)">
            <t-option v-for="user in users" :key="user.id" :value="user.id" :label="user.username" />
          </t-select>
        </t-form-item>
        <t-form-item label="套餐价格">
          <t-select v-model="grantForm.offer_id">
            <t-option v-for="offer in offers" :key="offer.id" :value="offer.id" :label="`${offer.plan_name} · ${offer.name} · ${formatMoney(offer.price_cents)}`" />
          </t-select>
        </t-form-item>
        <t-form-item label="操作备注"><t-textarea v-model="grantForm.note" :autosize="{ minRows: 3, maxRows: 5 }" /></t-form-item>
      </t-form>
    </t-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime, formatMoney, statusLabel, statusTheme } from '@/utils/billing'

const loading = ref(false)
const submitting = ref(false)
const grantDialog = ref(false)
const subscriptions = ref<any[]>([])
const users = ref<any[]>([])
const plans = ref<any[]>([])
const statusFilter = ref('')
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })

const columns = [
  { colKey: 'username', title: '用户', width: 160 },
  { colKey: 'plan.name', title: '当前套餐', width: 130 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 },
  { colKey: 'ends_at', title: '到期时间', cell: 'ends_at', width: 170 },
  { colKey: 'assignment', title: '固定账号', cell: 'assignment', minWidth: 190 },
  { colKey: 'scheduled', title: '待切换套餐', cell: 'scheduled', width: 130 },
  { colKey: 'op', title: '操作', cell: 'op', width: 230, fixed: 'right' }
]

const offers = computed(() => plans.value.flatMap(plan => (plan.offers || [])
  .filter((offer: any) => !offer.is_archived && !offer.is_draft && offer.is_purchase_enabled)
  .map((offer: any) => ({ ...offer, plan_id: plan.id, plan_name: plan.name }))))
const grantForm = reactive<any>({ subscription_id: 0, user_id: null, offer_id: null, note: '' })

const loadData = async () => {
  loading.value = true
  const params = new URLSearchParams({ page: String(pagination.current), page_size: String(pagination.pageSize) })
  if (statusFilter.value) params.set('status', statusFilter.value)
  const data = await request(`/0x/admin/subscriptions?${params.toString()}`)
  subscriptions.value = data?.results || []
  pagination.total = data?.count || 0
  loading.value = false
}

const loadOptions = async () => {
  const [userData, planData] = await Promise.all([
    request('/0x/user?page_size=100'),
    request('/0x/admin/plans')
  ])
  users.value = userData?.results || []
  plans.value = planData?.plans || []
}

const openGrantDialog = (row?: any) => {
  const matchingOffer = offers.value.find(item => item.plan_id === row?.plan?.id)
  Object.assign(grantForm, row ? {
    subscription_id: row.id,
    user_id: users.value.find(item => item.username === row.username)?.id || null,
    offer_id: matchingOffer?.id || null,
    note: ''
  } : { subscription_id: 0, user_id: users.value[0]?.id || null, offer_id: offers.value[0]?.id || null, note: '' })
  grantDialog.value = true
}

const grant = async () => {
  submitting.value = true
  const data = await request('/0x/admin/subscriptions', 'POST', {
    action: grantForm.subscription_id ? 'renew' : 'grant',
    user_id: grantForm.user_id,
    offer_id: grantForm.offer_id,
    note: grantForm.note
  })
  submitting.value = false
  if (!data) return
  grantDialog.value = false
  MessagePlugin.success(grantForm.subscription_id ? '续期完成' : '套餐已开通')
  loadData()
}

const suspend = async (row: any) => {
  const data = await request('/0x/admin/subscriptions', 'POST', { action: 'suspend', subscription_id: row.id })
  if (data) {
    MessagePlugin.success('订阅已暂停')
    loadData()
  }
}

const migrate = async (row: any) => {
  const data = await request('/0x/admin/subscriptions', 'POST', { action: 'migrate', subscription_id: row.id })
  if (data) {
    MessagePlugin.success(`已迁移到 ${data.account_name}`)
    loadData()
  }
}

const onPageChange = (info: any) => {
  pagination.current = info.current
  pagination.pageSize = info.pageSize
  loadData()
}

onMounted(async () => {
  await loadOptions()
  await loadData()
})
</script>

<style scoped>
.admin-section { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.toolbar { display: flex; justify-content: flex-end; gap: 10px; margin-bottom: 16px; }
.toolbar .t-select { width: 160px; }
.muted { color: var(--app-text-muted); font-size: 13px; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } .toolbar { justify-content: flex-start; } }
</style>
