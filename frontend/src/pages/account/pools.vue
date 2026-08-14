<template>
  <div class="pool-page">
    <section class="capacity-band">
      <div v-for="plan in activePlans" :key="plan.id" class="capacity-item">
        <div class="capacity-title">
          <strong>{{ plan.name }}</strong>
          <t-tag :theme="plan.capacity.is_full ? 'danger' : 'success'" variant="light">
            {{ plan.capacity.is_full ? '号池已满' : '容量正常' }}
          </t-tag>
        </div>
        <div class="capacity-values">
          <div><span>号池容量</span><strong>{{ plan.capacity.used }} / {{ plan.capacity.total }}</strong></div>
          <div><span>套餐人数</span><strong>{{ plan.capacity.plan_used }} / {{ plan.user_limit || '不限' }}</strong></div>
          <div><span>可新增</span><strong>{{ plan.capacity.available }}</strong></div>
        </div>
        <div class="pool-list">{{ (plan.pool_names || []).join(' · ') }}</div>
      </div>
    </section>

    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>商业号池</h2>
          <p>一行管理一个号池，可一次关联多个账号；账号人数、健康状态和使用用户在展开详情中维护</p>
        </div>
        <t-button theme="primary" @click="openCreatePoolDialog">
          <template #icon><local-icon name="add" /></template>
          新建套餐号池
        </t-button>
      </div>
      <div class="toolbar">
        <t-input v-model="query" clearable placeholder="搜索号池或上游账号" />
        <t-select v-model="tierFilter" clearable placeholder="全部等级">
          <t-option value="STANDARD" label="普通池" />
          <t-option value="PREMIUM" label="高级池" />
        </t-select>
      </div>
      <t-loading :loading="loading">
        <div class="pool-table-scroll">
          <t-table
            :data="filteredPools"
            :columns="poolColumns"
            row-key="id"
            v-model:expanded-row-keys="expandedPoolKeys"
          >
            <template #tier="{ row }">
              <t-tag :theme="row.tier === 'PREMIUM' ? 'warning' : 'primary'" variant="light">
                {{ tierLabel(row.tier) }}
              </t-tag>
            </template>
            <template #accounts="{ row }">
              <div class="account-summary">
                <strong>{{ row.account_count }}</strong>
                <span>个账号，{{ row.healthy_account_count }} 个健康</span>
              </div>
            </template>
            <template #capacity="{ row }">
              {{ row.active_bindings }} / {{ row.total_capacity }}
            </template>
            <template #status="{ row }">
              <t-tag :theme="poolStatusTheme(row)" variant="light">{{ poolStatusLabel(row) }}</t-tag>
            </template>
            <template #op="{ row }">
              <t-space size="small">
                <t-link theme="primary" @click="openPoolDialog(row)">编辑账号</t-link>
                <t-link theme="primary" @click="togglePool(row)">
                  {{ expandedPoolKeys.includes(row.id) ? '收起详情' : '查看详情' }}
                </t-link>
              </t-space>
            </template>
            <template #expandedRow="{ row }">
              <div class="expanded-pool">
                <div v-if="row.policies.length" class="account-detail-grid">
                  <article v-for="policy in row.policies" :key="policy.id" class="account-detail">
                    <div class="account-detail__heading">
                      <div>
                        <strong>{{ policy.account_name }}</strong>
                        <span>{{ row.car_name }}</span>
                      </div>
                      <t-tag :theme="statusTheme(policy.health_status) as any" variant="light">
                        {{ healthLabel(policy.health_status) }}
                      </t-tag>
                    </div>
                    <dl>
                      <div><dt>当前人数</dt><dd>{{ policy.active_bindings }} / {{ policy.binding_limit }} 人</dd></div>
                      <div><dt>允许分配</dt><dd>{{ policy.enabled ? '是' : '否' }}</dd></div>
                      <div><dt>最近检查</dt><dd>{{ formatDateTime(policy.last_health_check_at) }}</dd></div>
                    </dl>
                    <div class="account-detail__actions">
                      <t-button size="small" variant="outline" @click="openUsage(policy)">查看用户</t-button>
                      <t-button size="small" variant="outline" @click="openPolicyDialog(policy)">单独设置</t-button>
                      <t-popconfirm content="停用后新用户不会再分配到该账号" @confirm="disablePolicy(policy)">
                        <t-button size="small" theme="danger" variant="text">停用</t-button>
                      </t-popconfirm>
                      <t-popconfirm
                        :content="deleteAccountConfirmText(policy)"
                        @confirm="deleteAccount(policy)"
                      >
                        <t-button size="small" theme="danger" variant="text">删除账号</t-button>
                      </t-popconfirm>
                    </div>
                  </article>
                </div>
                <div v-else class="empty-pool">
                  当前号池还没有账号
                  <t-button size="small" theme="primary" variant="text" @click="openPoolDialog(row)">立即添加</t-button>
                </div>
              </div>
            </template>
          </t-table>
        </div>
      </t-loading>
    </section>

    <t-dialog
      :visible="poolDialogVisible"
      :header="poolForm.is_new ? '新建套餐号池' : `编辑 ${poolForm.pool_name}`"
      :confirm-btn="{ loading: submitting }"
      width="min(680px, calc(100vw - 24px))"
      @confirm="savePoolAccounts"
      @close="poolDialogVisible = false"
    >
      <t-form :data="poolForm" label-width="112px">
        <t-form-item label="号池名称">
          <t-input v-model="poolForm.pool_name" placeholder="例如 Plus 普通号池" />
        </t-form-item>
        <t-form-item label="套餐等级">
          <t-radio-group v-model="poolForm.tier">
            <t-radio value="STANDARD">普通</t-radio>
            <t-radio value="PREMIUM">高级</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="关联账号">
          <t-select
            v-model="poolForm.account_ids"
            multiple
            filterable
            :min-collapsed-num="3"
            placeholder="可一次选择多个 Plus、Pro、Team 或 Business 账号"
          >
            <t-option
              v-for="account in selectableAccounts"
              :key="account.id"
              :value="account.id"
              :label="`${account.chatgpt_username} · ${account.plan_type}${account.pool_name ? ` · 已在 ${account.pool_name}` : ''}`"
              :disabled="Boolean(account.pool_id && account.pool_id !== poolForm.pool_id)"
            />
          </t-select>
          <template #help>
            <span class="form-help">同一账号只能属于一个商业号池；已在其他号池的账号需先从原号池移出。</span>
          </template>
        </t-form-item>
        <t-form-item label="新账号默认上限">
          <t-input-number v-model="poolForm.default_binding_limit" :min="1" />
          <template #help>
            <span class="form-help">默认只应用到本次新加入的账号，已有账号保留各自上限。</span>
          </template>
        </t-form-item>
        <t-form-item label="统一更新上限">
          <t-switch v-model="poolForm.apply_binding_limit" />
          <template #help>
            <span class="form-help">开启后会把本号池所有已选账号统一设为上面的上限；不能低于当前实际使用人数。</span>
          </template>
        </t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog
      :visible="policyDialogVisible"
      header="单账号设置"
      :confirm-btn="{ loading: submitting }"
      width="min(540px, calc(100vw - 24px))"
      @confirm="savePolicy"
      @close="policyDialogVisible = false"
    >
      <t-form :data="policyForm" label-width="100px">
        <t-form-item label="上游账号"><t-input :value="policyForm.account_name" disabled /></t-form-item>
        <t-form-item label="所属号池"><t-input :value="policyForm.pool_name" disabled /></t-form-item>
        <t-form-item label="承载上限"><t-input-number v-model="policyForm.binding_limit" :min="Math.max(1, policyForm.active_bindings || 0)" /></t-form-item>
        <t-form-item label="健康状态">
          <t-select v-model="policyForm.health_status">
            <t-option value="HEALTHY" label="健康" />
            <t-option value="DEGRADED" label="异常" />
            <t-option value="DISABLED" label="停用" />
          </t-select>
        </t-form-item>
        <t-form-item label="允许分配"><t-switch v-model="policyForm.enabled" /></t-form-item>
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
            <div><span>承载上限</span><strong>{{ usagePolicy.binding_limit }} 人</strong></div>
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
              <template #status="{ row }"><t-tag :theme="statusTheme(row.subscription_status) as any" variant="light">{{ subscriptionStatusLabel(row.subscription_status) }}</t-tag></template>
              <template #ends_at="{ row }">{{ formatDateTime(row.subscription_ends_at) }}</template>
              <template #assigned_at="{ row }">{{ formatDateTime(row.assigned_at) }}</template>
              <template #last_used_at="{ row }">{{ formatDateTime(row.last_used_at) }}</template>
            </t-table>
          </div>

          <div class="mobile-usage-list">
            <article v-for="row in usageUsers" :key="row.id" class="usage-user-card">
              <div class="usage-user-heading"><strong>{{ row.username }}</strong><t-tag :theme="statusTheme(row.subscription_status) as any" variant="light">{{ subscriptionStatusLabel(row.subscription_status) }}</t-tag></div>
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
const poolDialogVisible = ref(false)
const policyDialogVisible = ref(false)
const usageVisible = ref(false)
const usageLoading = ref(false)
const commercialPools = ref<any[]>([])
const accounts = ref<any[]>([])
const plans = ref<any[]>([])
const query = ref('')
const tierFilter = ref('')
const expandedPoolKeys = ref<Array<number | string>>([])
const usagePolicy = ref<any>(null)
const usageUsers = ref<any[]>([])

const poolColumns = [
  { colKey: 'car_name', title: '号池名称', minWidth: 190 },
  { colKey: 'tier', title: '等级', cell: 'tier', width: 90 },
  { colKey: 'accounts', title: '账号', cell: 'accounts', width: 180 },
  { colKey: 'capacity', title: '已用 / 总容量', cell: 'capacity', width: 130 },
  { colKey: 'status', title: '状态', cell: 'status', width: 110 },
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

const poolForm = reactive<any>({ is_new: false, pool_id: null, pool_name: '', remark: '', tier: 'STANDARD', account_ids: [], default_binding_limit: 5, apply_binding_limit: false })
const policyForm = reactive<any>({ id: 0, account_id: null, account_name: '', pool_id: null, pool_name: '', tier: 'STANDARD', binding_limit: 5, active_bindings: 0, enabled: true, health_status: 'HEALTHY' })

const activePlans = computed(() => plans.value.filter(item => !item.is_archived))
const filteredPools = computed(() => commercialPools.value.filter(pool => {
  const keyword = query.value.trim().toLowerCase()
  const matchesQuery = !keyword
    || pool.car_name.toLowerCase().includes(keyword)
    || pool.policies.some((policy: any) => policy.account_name.toLowerCase().includes(keyword))
  const matchesTier = !tierFilter.value || pool.tier === tierFilter.value
  return matchesQuery && matchesTier
}))

const accountPoolMap = computed(() => {
  const result: Record<number, { pool_id: number, pool_name: string }> = {}
  commercialPools.value.forEach(pool => {
    pool.policies.forEach((policy: any) => {
      result[policy.account_id] = { pool_id: pool.id, pool_name: pool.car_name }
    })
  })
  return result
})

const selectableAccounts = computed(() => accounts.value
  .filter(account => ['plus', 'pro', 'team', 'business'].some(marker => String(account.plan_type || '').toLowerCase().includes(marker)))
  .map(account => ({ ...account, ...(accountPoolMap.value[account.id] || {}) })))

const tierLabel = (value: string) => value === 'PREMIUM' ? '高级' : '普通'
const healthLabel = (value: string) => ({ HEALTHY: '健康', DEGRADED: '异常', DISABLED: '停用' }[value] || value)
const subscriptionStatusLabel = (value: string) => ({ ACTIVE: '生效中', SUSPENDED: '已暂停', EXPIRED: '已到期', PENDING: '待生效' }[value] || value)
const poolStatusLabel = (row: any) => row.account_count === 0 ? '待添加账号' : row.healthy_account_count === 0 ? '暂无健康账号' : row.active_bindings >= row.total_capacity ? '容量已满' : '容量正常'
const poolStatusTheme = (row: any) => row.account_count === 0 ? 'warning' : row.healthy_account_count === 0 || row.active_bindings >= row.total_capacity ? 'danger' : 'success'
const deleteAccountConfirmText = (row: any) => {
  if (row.deletion_requires_migration) {
    return `该健康账号当前有 ${row.active_bindings} 人使用，确认后先迁移全部用户；容量不足时不会删除。`
  }
  if (row.active_bindings) {
    return `该账号已失效或停用，将直接释放 ${row.active_bindings} 个使用记录并删除。`
  }
  return '删除后会清除 Token、Cookie 和代理绑定，确定继续吗？'
}

const loadData = async () => {
  loading.value = true
  try {
    const [poolData, planData] = await Promise.all([request('/0x/admin/pools'), request('/0x/admin/plans')])
    commercialPools.value = poolData?.commercial_pools || []
    accounts.value = poolData?.accounts || []
    plans.value = planData?.plans || []
  } finally {
    loading.value = false
  }
}

const togglePool = (row: any) => {
  expandedPoolKeys.value = expandedPoolKeys.value.includes(row.id)
    ? expandedPoolKeys.value.filter(item => item !== row.id)
    : [...expandedPoolKeys.value, row.id]
}

const openPoolDialog = (row: any) => {
  Object.assign(poolForm, {
    is_new: false,
    pool_id: row.id,
    pool_name: row.car_name,
    remark: row.remark || '',
    tier: row.tier,
    account_ids: [...row.account_ids],
    default_binding_limit: row.policies[0]?.binding_limit || 5,
    apply_binding_limit: false
  })
  poolDialogVisible.value = true
}

const openCreatePoolDialog = () => {
  Object.assign(poolForm, {
    is_new: true,
    pool_id: null,
    pool_name: '',
    remark: '',
    tier: 'STANDARD',
    account_ids: [],
    default_binding_limit: 5,
    apply_binding_limit: true
  })
  poolDialogVisible.value = true
}

const savePoolAccounts = async () => {
  submitting.value = true
  try {
    const data = await request('/0x/admin/pools', 'POST', {
      action: poolForm.is_new ? 'create_pool' : 'sync_pool_accounts',
      ...poolForm
    })
    if (!data) return
    poolDialogVisible.value = false
    MessagePlugin.success(data.message || '商业号池账号已保存')
    await loadData()
  } finally {
    submitting.value = false
  }
}

const openPolicyDialog = (row: any) => {
  Object.assign(policyForm, { ...row })
  policyDialogVisible.value = true
}

const savePolicy = async () => {
  submitting.value = true
  try {
    const data = await request('/0x/admin/pools', 'POST', { action: 'save_policy', ...policyForm })
    if (!data) return
    policyDialogVisible.value = false
    MessagePlugin.success('账号设置已保存')
    await loadData()
  } finally {
    submitting.value = false
  }
}

const disablePolicy = async (row: any) => {
  const data = await request('/0x/admin/pools', 'POST', { action: 'disable_policy', id: row.id })
  if (data) {
    MessagePlugin.success('账号策略已停用')
    await loadData()
  }
}

const deleteAccount = async (row: any) => {
  const data = await request('/0x/chatgpt', 'DELETE', {
    chatgpt_username: row.account_name,
    migrate_users: Boolean(row.deletion_requires_migration)
  })
  if (data) {
    const migrated = Number(data.migrated_users || 0)
    const released = Number(data.released_users || 0)
    MessagePlugin.success(migrated ? `已迁移 ${migrated} 人并删除账号` : released ? `已释放 ${released} 个使用记录并删除账号` : '上游账号已删除，凭据已清除')
    await loadData()
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
.section-heading h2 { margin: 0; font-size: 18px; font-weight: 600; }
.section-heading p { margin: 5px 0 0; color: var(--app-text-muted); font-size: 13px; }
.toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) 160px; gap: 10px; margin-bottom: 16px; }
.pool-table-scroll { width: 100%; min-width: 0; max-width: 100%; overflow-x: auto; overscroll-behavior-inline: contain; }
.pool-table-scroll :deep(.t-table) { min-width: 850px; }
.account-summary { display: flex; align-items: baseline; gap: 5px; }
.account-summary strong { font-size: 17px; font-weight: 600; }
.account-summary span { color: var(--app-text-muted); font-size: 12px; }
.expanded-pool { padding: 16px 18px 20px; background: #f7f7f5; }
.account-detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.account-detail { min-width: 0; padding: 15px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 7px; }
.account-detail__heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.account-detail__heading strong, .account-detail__heading span { display: block; }
.account-detail__heading strong { overflow-wrap: anywhere; font-size: 14px; font-weight: 600; }
.account-detail__heading span { margin-top: 3px; color: var(--app-text-muted); font-size: 11px; }
.account-detail dl { display: grid; gap: 7px; margin: 14px 0 0; }
.account-detail dl div { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 8px; font-size: 12px; }
.account-detail dt { color: var(--app-text-muted); }
.account-detail dd { min-width: 0; margin: 0; text-align: right; overflow-wrap: anywhere; }
.account-detail__actions { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; margin-top: 14px; }
.empty-pool { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 90px; color: var(--app-text-muted); }
.form-help { color: var(--app-text-muted); font-size: 12px; line-height: 1.6; }
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
  .toolbar { grid-template-columns: 1fr; }
  .account-detail-grid { grid-template-columns: 1fr; }
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
