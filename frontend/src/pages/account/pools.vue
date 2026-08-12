<template>
  <div class="pool-page">
    <section class="capacity-band">
      <div v-for="plan in plans.filter(item => !item.is_archived)" :key="plan.id" class="capacity-item">
        <div class="capacity-title">
          <strong>{{ plan.name }}</strong>
          <t-tag :theme="plan.capacity.is_full ? 'danger' : 'success'" variant="light">
            {{ plan.capacity.is_full ? '号池已满' : '容量正常' }}
          </t-tag>
        </div>
        <div class="capacity-values">
          <div><span>号池席位</span><strong>{{ plan.capacity.used }} / {{ plan.capacity.total }}</strong></div>
          <div><span>套餐人数</span><strong>{{ plan.capacity.plan_used }} / {{ plan.user_limit || '不限' }}</strong></div>
          <div><span>可新增</span><strong>{{ plan.capacity.available }}</strong></div>
        </div>
        <div class="pool-list">{{ (plan.pool_names || []).join(' · ') }}</div>
      </div>
    </section>

    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>账号策略</h2>
          <p>账号只能加入一个商业号池；Plus、Pro、Team、Business 均可配置，每个账号独立设置承载人数</p>
        </div>
        <t-button theme="primary" @click="openDialog()">
          <template #icon><t-icon name="add" /></template>
          添加账号策略
        </t-button>
      </div>
      <div class="toolbar">
        <t-input v-model="query" clearable placeholder="搜索上游账号" />
        <t-select v-model="tierFilter" clearable placeholder="全部等级">
          <t-option value="STANDARD" label="普通池" />
          <t-option value="PREMIUM" label="高级池" />
        </t-select>
      </div>
      <t-loading :loading="loading">
        <div class="policy-table-scroll">
          <t-table :data="filteredPolicies" :columns="columns" row-key="id">
            <template #tier="{ row }">{{ row.tier === 'PREMIUM' ? '高级' : '普通' }}</template>
            <template #bindings="{ row }">{{ row.active_bindings }} / {{ row.binding_limit }}</template>
            <template #health="{ row }">
              <t-tag :theme="statusTheme(row.health_status) as any" variant="light">{{ healthLabel(row.health_status) }}</t-tag>
            </template>
            <template #last_check="{ row }">{{ formatDateTime(row.last_health_check_at) }}</template>
            <template #op="{ row }">
              <t-space size="small">
                <t-link theme="primary" @click="openUsage(row)">查看使用</t-link>
                <t-link theme="primary" @click="openDialog(row)">编辑</t-link>
                <t-popconfirm content="停用后新用户不会再分配到该账号" @confirm="disablePolicy(row)">
                  <t-link theme="danger">停用</t-link>
                </t-popconfirm>
              </t-space>
            </template>
          </t-table>
        </div>
      </t-loading>
    </section>

    <t-dialog
      :visible="dialogVisible"
      :header="form.id ? '编辑账号策略' : '添加账号策略'"
      :confirm-btn="{ loading: submitting }"
      width="540px"
      @confirm="savePolicy"
      @close="dialogVisible = false"
    >
      <t-form :data="form" label-width="100px">
        <t-form-item label="上游账号">
          <t-select v-model="form.account_id" filterable :disabled="Boolean(form.id)">
            <t-option v-for="account in accounts" :key="account.id" :value="account.id" :label="`${account.chatgpt_username} · ${account.plan_type}`" />
          </t-select>
        </t-form-item>
        <t-form-item label="所属号池">
          <t-select v-model="form.pool_id">
            <t-option v-for="pool in pools" :key="pool.id" :value="pool.id" :label="pool.car_name" />
          </t-select>
        </t-form-item>
        <t-form-item label="套餐等级">
          <t-radio-group v-model="form.tier">
            <t-radio value="STANDARD">普通</t-radio>
            <t-radio value="PREMIUM">高级</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="绑定上限">
          <t-input-number v-model="form.binding_limit" :min="1" />
        </t-form-item>
        <t-form-item label="健康状态">
          <t-select v-model="form.health_status">
            <t-option value="HEALTHY" label="健康" />
            <t-option value="DEGRADED" label="异常" />
            <t-option value="DISABLED" label="停用" />
          </t-select>
        </t-form-item>
        <t-form-item label="允许分配"><t-switch v-model="form.enabled" /></t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog
      v-model:visible="usageVisible"
      :header="usagePolicy ? `${usagePolicy.account_name} · 使用情况` : '账号使用情况'"
      width="min(860px, calc(100vw - 24px))"
      :footer="false"
      @close="closeUsage"
    >
      <t-loading :loading="usageLoading">
        <div v-if="usagePolicy" class="usage-dialog">
          <div class="usage-summary">
            <div><span>所属号池</span><strong>{{ usagePolicy.pool_name }}</strong></div>
            <div><span>当前使用</span><strong>{{ usagePolicy.active_bindings }} 人</strong></div>
            <div><span>绑定上限</span><strong>{{ usagePolicy.binding_limit }} 人</strong></div>
          </div>

          <div class="desktop-usage-table">
            <t-table :data="usageUsers" :columns="usageColumns" row-key="id" size="small">
              <template #email="{ row }">
                <div class="email-usage-cell">
                  <span class="breakable">{{ row.email || '未绑定' }}</span>
                  <t-tag v-if="row.email" :theme="row.email_verified ? 'success' : 'warning'" size="small" variant="light">
                    {{ row.email_verified ? '已验证' : '待验证' }}
                  </t-tag>
                </div>
              </template>
              <template #status="{ row }">
                <t-tag :theme="statusTheme(row.subscription_status) as any" variant="light">
                  {{ subscriptionStatusLabel(row.subscription_status) }}
                </t-tag>
              </template>
              <template #ends_at="{ row }">{{ formatDateTime(row.subscription_ends_at) }}</template>
              <template #assigned_at="{ row }">{{ formatDateTime(row.assigned_at) }}</template>
              <template #last_used_at="{ row }">{{ formatDateTime(row.last_used_at) }}</template>
            </t-table>
          </div>

          <div class="mobile-usage-list">
            <article v-for="row in usageUsers" :key="row.id" class="usage-user-card">
              <div class="usage-user-heading">
                <strong>{{ row.username }}</strong>
                <t-tag :theme="statusTheme(row.subscription_status) as any" variant="light">
                  {{ subscriptionStatusLabel(row.subscription_status) }}
                </t-tag>
              </div>
              <dl>
                <div><dt>邮箱</dt><dd>{{ row.email || '未绑定' }}<span v-if="row.email"> · {{ row.email_verified ? '已验证' : '待验证' }}</span></dd></div>
                <div><dt>套餐</dt><dd>{{ row.plan_name }}</dd></div>
                <div><dt>到期</dt><dd>{{ formatDateTime(row.subscription_ends_at) }}</dd></div>
                <div><dt>分配时间</dt><dd>{{ formatDateTime(row.assigned_at) }}</dd></div>
                <div><dt>最近使用</dt><dd>{{ formatDateTime(row.last_used_at) }}</dd></div>
              </dl>
            </article>
          </div>

          <div v-if="!usageLoading && !usageUsers.length" class="usage-empty">当前没有有效绑定用户</div>
          <p class="usage-security-note">仅显示用户分配信息，不展示账号 Token、Cookie 或代理凭据。</p>
        </div>
      </t-loading>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime, statusTheme } from '@/utils/billing'

const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const usageVisible = ref(false)
const usageLoading = ref(false)
const policies = ref<any[]>([])
const pools = ref<any[]>([])
const accounts = ref<any[]>([])
const plans = ref<any[]>([])
const query = ref('')
const tierFilter = ref('')
const usagePolicy = ref<any>(null)
const usageUsers = ref<any[]>([])

const columns = [
  { colKey: 'account_name', title: '上游账号', minWidth: 210 },
  { colKey: 'pool_name', title: '所属号池', width: 160 },
  { colKey: 'tier', title: '等级', cell: 'tier', width: 80 },
  { colKey: 'bindings', title: '绑定人数', cell: 'bindings', width: 100 },
  { colKey: 'health', title: '健康状态', cell: 'health', width: 100 },
  { colKey: 'last_check', title: '最近检查', cell: 'last_check', width: 170 },
  { colKey: 'op', title: '操作', cell: 'op', width: 190 }
]

const usageColumns = [
  { colKey: 'username', title: '用户名', minWidth: 130 },
  { colKey: 'email', title: '验证邮箱', cell: 'email', minWidth: 190 },
  { colKey: 'plan_name', title: '套餐', width: 120 },
  { colKey: 'status', title: '状态', cell: 'status', width: 90 },
  { colKey: 'ends_at', title: '到期时间', cell: 'ends_at', width: 150 },
  { colKey: 'assigned_at', title: '分配时间', cell: 'assigned_at', width: 150 },
  { colKey: 'last_used_at', title: '最近使用', cell: 'last_used_at', width: 150 }
]

const form = reactive<any>({ id: 0, account_id: null, pool_id: null, tier: 'STANDARD', binding_limit: 5, enabled: true, health_status: 'HEALTHY' })
const filteredPolicies = computed(() => policies.value.filter(item => {
  const matchesQuery = !query.value || item.account_name.toLowerCase().includes(query.value.toLowerCase())
  const matchesTier = !tierFilter.value || item.tier === tierFilter.value
  return matchesQuery && matchesTier
}))

const healthLabel = (value: string) => ({ HEALTHY: '健康', DEGRADED: '异常', DISABLED: '停用' }[value] || value)
const subscriptionStatusLabel = (value: string) => ({ ACTIVE: '生效中', SUSPENDED: '已暂停', EXPIRED: '已到期', PENDING: '待生效' }[value] || value)

const loadData = async () => {
  loading.value = true
  const [poolData, planData] = await Promise.all([request('/0x/admin/pools'), request('/0x/admin/plans')])
  policies.value = poolData?.policies || []
  pools.value = poolData?.pools || []
  accounts.value = poolData?.accounts || []
  plans.value = planData?.plans || []
  loading.value = false
}

const openDialog = (row?: any) => {
  Object.assign(form, row ? { ...row } : {
    id: 0, account_id: accounts.value[0]?.id || null, pool_id: pools.value[0]?.id || null,
    tier: 'STANDARD', binding_limit: 5, enabled: true, health_status: 'HEALTHY'
  })
  dialogVisible.value = true
}

const savePolicy = async () => {
  submitting.value = true
  const data = await request('/0x/admin/pools', 'POST', { action: 'save_policy', ...form })
  submitting.value = false
  if (!data) return
  dialogVisible.value = false
  MessagePlugin.success('账号策略已保存')
  loadData()
}

const disablePolicy = async (row: any) => {
  const data = await request('/0x/admin/pools', 'POST', { action: 'disable_policy', id: row.id })
  if (data) {
    MessagePlugin.success('账号策略已停用')
    loadData()
  }
}

const openUsage = async (row: any) => {
  usagePolicy.value = { ...row }
  usageUsers.value = []
  usageVisible.value = true
  usageLoading.value = true
  try {
    const data = await request(`/0x/admin/pools/${row.id}/usage`)
    if (!data) {
      usageVisible.value = false
      return
    }
    usagePolicy.value = data.policy || row
    usageUsers.value = data.users || []
  } finally {
    usageLoading.value = false
  }
}

const closeUsage = () => {
  usageVisible.value = false
  usagePolicy.value = null
  usageUsers.value = []
}

onMounted(loadData)
</script>

<style scoped>
.pool-page { display: grid; gap: 20px; min-width: 0; }
.capacity-band { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); min-width: 0; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.capacity-item { min-width: 0; padding: 20px 22px; border-right: 1px solid #e7e7e3; }
.capacity-item:last-child { border-right: 0; }
.capacity-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.capacity-title strong { font-size: 16px; font-weight: 600; }
.capacity-values { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 20px; }
.capacity-values span { display: block; color: var(--app-text-muted); font-size: 12px; }
.capacity-values strong { display: block; margin-top: 7px; font-size: 24px; font-weight: 600; }
.pool-list { margin-top: 14px; color: var(--app-text-muted); font-size: 12px; }
.admin-section { min-width: 0; padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) 160px; gap: 10px; margin-bottom: 16px; }
.policy-table-scroll { width: 100%; min-width: 0; max-width: 100%; overflow-x: auto; overscroll-behavior-inline: contain; }
.policy-table-scroll :deep(.t-table) { min-width: 1010px; }
.usage-dialog { display: grid; gap: 16px; min-width: 0; }
.usage-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--app-border); border-radius: 7px; }
.usage-summary > div { min-width: 0; padding: 14px 16px; border-right: 1px solid var(--app-border); }
.usage-summary > div:last-child { border-right: 0; }
.usage-summary span { display: block; color: var(--app-text-muted); font-size: 12px; }
.usage-summary strong { display: block; margin-top: 5px; overflow-wrap: anywhere; font-size: 15px; font-weight: 600; }
.breakable { overflow-wrap: anywhere; }
.email-usage-cell { display: grid; justify-items: start; gap: 5px; min-width: 0; }
.mobile-usage-list { display: none; }
.usage-empty { padding: 28px 16px; color: var(--app-text-muted); text-align: center; background: #f7f7f5; border: 1px dashed var(--app-border-strong); border-radius: 7px; }
.usage-security-note { margin: 0; color: var(--app-text-muted); font-size: 12px; line-height: 1.5; }
@media (max-width: 760px) {
  .capacity-band { grid-template-columns: 1fr; }
  .capacity-item { border-right: 0; border-bottom: 1px solid #e7e7e3; }
  .capacity-item:last-child { border-bottom: 0; }
  .admin-section { padding: 18px 14px; }
  .section-heading { align-items: flex-start; flex-direction: column; }
  .toolbar { grid-template-columns: 1fr; }
  .usage-summary { grid-template-columns: 1fr; }
  .usage-summary > div { border-right: 0; border-bottom: 1px solid var(--app-border); }
  .usage-summary > div:last-child { border-bottom: 0; }
  .desktop-usage-table { display: none; }
  .mobile-usage-list { display: grid; gap: 10px; max-height: calc(100dvh - 330px); overflow: auto; }
  .usage-user-card { display: grid; gap: 12px; min-width: 0; padding: 14px; background: #f7f7f5; border: 1px solid var(--app-border); border-radius: 7px; }
  .usage-user-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .usage-user-heading strong { min-width: 0; overflow-wrap: anywhere; font-size: 14px; font-weight: 600; }
  .usage-user-card dl { display: grid; gap: 8px; margin: 0; }
  .usage-user-card dl div { display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 8px; }
  .usage-user-card dt { color: var(--app-text-muted); font-size: 12px; }
  .usage-user-card dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font-size: 12px; text-align: right; }
}
</style>
