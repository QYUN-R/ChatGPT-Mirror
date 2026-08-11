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
          <div><span>总席位</span><strong>{{ plan.capacity.total }}</strong></div>
          <div><span>已使用</span><strong>{{ plan.capacity.used }}</strong></div>
          <div><span>可用</span><strong>{{ plan.capacity.available }}</strong></div>
        </div>
      </div>
    </section>

    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>账号策略</h2>
          <p>账号只能加入一个商业号池，高级池人数固定为 3</p>
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
        <t-table :data="filteredPolicies" :columns="columns" row-key="id">
          <template #tier="{ row }">{{ row.tier === 'PREMIUM' ? '高级' : '普通' }}</template>
          <template #bindings="{ row }">{{ row.active_bindings }} / {{ row.binding_limit }}</template>
          <template #health="{ row }">
            <t-tag :theme="statusTheme(row.health_status) as any" variant="light">{{ healthLabel(row.health_status) }}</t-tag>
          </template>
          <template #last_check="{ row }">{{ formatDateTime(row.last_health_check_at) }}</template>
          <template #op="{ row }">
            <t-space size="small">
              <t-link theme="primary" @click="openDialog(row)">编辑</t-link>
              <t-popconfirm content="停用后新用户不会再分配到该账号" @confirm="disablePolicy(row)">
                <t-link theme="danger">停用</t-link>
              </t-popconfirm>
            </t-space>
          </template>
        </t-table>
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
          <t-radio-group v-model="form.tier" @change="handleTierChange">
            <t-radio value="STANDARD">普通</t-radio>
            <t-radio value="PREMIUM">高级</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="绑定上限">
          <t-input-number v-model="form.binding_limit" :min="3" :max="form.tier === 'PREMIUM' ? 3 : 8" :disabled="form.tier === 'PREMIUM'" />
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
const policies = ref<any[]>([])
const pools = ref<any[]>([])
const accounts = ref<any[]>([])
const plans = ref<any[]>([])
const query = ref('')
const tierFilter = ref('')

const columns = [
  { colKey: 'account_name', title: '上游账号', minWidth: 210 },
  { colKey: 'pool_name', title: '所属号池', width: 160 },
  { colKey: 'tier', title: '等级', cell: 'tier', width: 80 },
  { colKey: 'bindings', title: '绑定人数', cell: 'bindings', width: 100 },
  { colKey: 'health', title: '健康状态', cell: 'health', width: 100 },
  { colKey: 'last_check', title: '最近检查', cell: 'last_check', width: 170 },
  { colKey: 'op', title: '操作', cell: 'op', width: 130 }
]

const form = reactive<any>({ id: 0, account_id: null, pool_id: null, tier: 'STANDARD', binding_limit: 5, enabled: true, health_status: 'HEALTHY' })
const filteredPolicies = computed(() => policies.value.filter(item => {
  const matchesQuery = !query.value || item.account_name.toLowerCase().includes(query.value.toLowerCase())
  const matchesTier = !tierFilter.value || item.tier === tierFilter.value
  return matchesQuery && matchesTier
}))

const healthLabel = (value: string) => ({ HEALTHY: '健康', DEGRADED: '异常', DISABLED: '停用' }[value] || value)

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

const handleTierChange = (value: string) => {
  if (value === 'PREMIUM') form.binding_limit = 3
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

onMounted(loadData)
</script>

<style scoped>
.pool-page { display: grid; gap: 20px; }
.capacity-band { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.capacity-item { padding: 20px 22px; border-right: 1px solid #e7e7e3; }
.capacity-item:last-child { border-right: 0; }
.capacity-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.capacity-title strong { font-size: 16px; font-weight: 600; }
.capacity-values { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 20px; }
.capacity-values span { display: block; color: var(--app-text-muted); font-size: 12px; }
.capacity-values strong { display: block; margin-top: 7px; font-size: 24px; font-weight: 600; }
.admin-section { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) 160px; gap: 10px; margin-bottom: 16px; }
@media (max-width: 760px) { .capacity-band { grid-template-columns: 1fr; } .capacity-item { border-right: 0; border-bottom: 1px solid #e7e7e3; } .capacity-item:last-child { border-bottom: 0; } .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } .toolbar { grid-template-columns: 1fr; } }
</style>
