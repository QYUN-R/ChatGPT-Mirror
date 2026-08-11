<template>
  <div class="admin-page">
    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>套餐配置</h2>
            <p>管理套餐名称、宣传语、价格和可购买状态</p>
        </div>
        <t-space>
          <t-button variant="outline" @click="openOfferDialog()">新增价格</t-button>
          <t-button theme="primary" @click="openPlanDialog()">
            <template #icon><t-icon name="add" /></template>
            新增套餐
          </t-button>
        </t-space>
      </div>
      <t-loading :loading="loading">
        <t-table :data="plans" :columns="planColumns" row-key="id">
          <template #pool_tier="{ row }">{{ row.pool_tier === 'PREMIUM' ? '高级池' : '普通池' }}</template>
          <template #status="{ row }">
            <t-tag :theme="row.is_active && !row.is_archived ? 'success' : 'default'" variant="light">
              {{ row.is_archived ? '已归档' : row.is_active ? '已上架' : '已下架' }}
            </t-tag>
          </template>
          <template #capacity="{ row }">{{ row.capacity.used }} / {{ row.capacity.total }}</template>
          <template #op="{ row }">
            <t-space size="small">
              <t-link theme="primary" @click="openPlanDialog(row)">编辑</t-link>
              <t-popconfirm content="套餐将停止新购，历史订单仍保留" @confirm="archive('archive_plan', row.id)">
                <t-link theme="danger">归档</t-link>
              </t-popconfirm>
            </t-space>
          </template>
        </t-table>
      </t-loading>
    </section>

    <section class="admin-section">
      <div class="section-heading compact">
        <div>
          <h2>价格方案</h2>
            <p>价格按人民币录入，可随时调整；是否开放购买由你决定</p>
        </div>
      </div>
      <t-table :data="offers" :columns="offerColumns" row-key="key">
        <template #price="{ row }">{{ formatMoney(row.price_cents, row.currency) }}</template>
        <template #availability="{ row }">
          <t-tag :theme="row.is_purchase_enabled && !row.is_draft ? 'success' : 'warning'" variant="light">
            {{ row.is_draft ? '草稿' : row.is_purchase_enabled ? '可购买' : '已停用' }}
          </t-tag>
        </template>
        <template #op="{ row }">
          <t-space size="small">
            <t-link theme="primary" @click="openOfferDialog(row)">编辑</t-link>
            <t-popconfirm content="价格方案将归档" @confirm="archive('archive_offer', row.id)">
              <t-link theme="danger">归档</t-link>
            </t-popconfirm>
          </t-space>
        </template>
      </t-table>
    </section>

    <t-dialog
      :visible="planDialog"
      :header="planForm.id ? '编辑套餐' : '新增套餐'"
      :confirm-btn="{ loading: submitting }"
      width="560px"
      @confirm="savePlan"
      @close="planDialog = false"
    >
      <t-form :data="planForm" label-width="96px">
        <t-form-item label="套餐代码"><t-input v-model="planForm.code" :disabled="Boolean(planForm.id)" /></t-form-item>
        <t-form-item label="套餐名称"><t-input v-model="planForm.name" /></t-form-item>
        <t-form-item label="宣传语"><t-input v-model="planForm.tagline" /></t-form-item>
        <t-form-item label="关联号池">
          <t-select v-model="planForm.pool_id">
            <t-option v-for="pool in pools" :key="pool.id" :value="pool.id" :label="pool.car_name" />
          </t-select>
        </t-form-item>
        <t-form-item label="套餐等级">
          <t-radio-group v-model="planForm.pool_tier">
            <t-radio value="STANDARD">普通</t-radio>
            <t-radio value="PREMIUM">高级</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="公开上架"><t-switch v-model="planForm.is_public" /></t-form-item>
        <t-form-item label="允许使用"><t-switch v-model="planForm.is_active" /></t-form-item>
        <t-form-item label="排序"><t-input-number v-model="planForm.sort_order" :min="0" /></t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog
      :visible="offerDialog"
      :header="offerForm.id ? '编辑价格' : '新增价格'"
      :confirm-btn="{ loading: submitting }"
      width="520px"
      @confirm="saveOffer"
      @close="offerDialog = false"
    >
      <t-form :data="offerForm" label-width="96px">
        <t-form-item label="所属套餐">
          <t-select v-model="offerForm.plan_id">
            <t-option v-for="plan in plans.filter(item => !item.is_archived)" :key="plan.id" :value="plan.id" :label="plan.name" />
          </t-select>
        </t-form-item>
        <t-form-item label="价格代码"><t-input v-model="offerForm.code" placeholder="monthly / quarterly" /></t-form-item>
        <t-form-item label="显示名称"><t-input v-model="offerForm.name" /></t-form-item>
        <t-form-item label="周期月数"><t-input-number v-model="offerForm.months" :min="1" :max="36" /></t-form-item>
        <t-form-item label="价格（元）"><t-input-number v-model="offerForm.price_yuan" :min="0" :decimal-places="2" /></t-form-item>
        <t-form-item label="草稿"><t-switch v-model="offerForm.is_draft" /></t-form-item>
        <t-form-item label="允许购买"><t-switch v-model="offerForm.is_purchase_enabled" :disabled="offerForm.is_draft" /></t-form-item>
      </t-form>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatMoney } from '@/utils/billing'

const loading = ref(false)
const submitting = ref(false)
const plans = ref<any[]>([])
const pools = ref<any[]>([])
const planDialog = ref(false)
const offerDialog = ref(false)

const planColumns = [
  { colKey: 'name', title: '套餐', width: 150 },
  { colKey: 'code', title: '代码', width: 150 },
  { colKey: 'pool_name', title: '号池' },
  { colKey: 'pool_tier', title: '等级', cell: 'pool_tier', width: 90 },
  { colKey: 'capacity', title: '已用/总席位', cell: 'capacity', width: 120 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 },
  { colKey: 'op', title: '操作', cell: 'op', width: 130 }
]
const offerColumns = [
  { colKey: 'plan_name', title: '套餐', width: 140 },
  { colKey: 'name', title: '方案', width: 120 },
  { colKey: 'code', title: '代码', width: 130 },
  { colKey: 'months', title: '月数', width: 80 },
  { colKey: 'price', title: '价格', cell: 'price', width: 110 },
  { colKey: 'availability', title: '状态', cell: 'availability', width: 100 },
  { colKey: 'op', title: '操作', cell: 'op', width: 120 }
]

const offers = computed(() => plans.value.flatMap(plan => (plan.offers || []).map((offer: any) => ({
  ...offer,
  key: `${plan.id}-${offer.id}`,
  plan_id: plan.id,
  plan_name: plan.name
}))))

const planForm = reactive<any>({ id: 0, code: '', name: '', tagline: '', pool_id: null, pool_tier: 'STANDARD', is_active: true, is_public: true, sort_order: 0 })
const offerForm = reactive<any>({ id: 0, plan_id: null, code: 'monthly', name: '月套餐', months: 1, price_yuan: 0, is_draft: false, is_purchase_enabled: true })

const loadData = async () => {
  loading.value = true
  const data = await request('/0x/admin/plans')
  plans.value = data?.plans || []
  pools.value = data?.pools || []
  loading.value = false
}

const openPlanDialog = (row?: any) => {
  Object.assign(planForm, row ? {
    id: row.id, code: row.code, name: row.name, tagline: row.tagline, pool_id: row.pool_id,
    pool_tier: row.pool_tier, is_active: row.is_active, is_public: row.is_public, sort_order: row.sort_order
  } : { id: 0, code: '', name: '', tagline: '', pool_id: pools.value[0]?.id || null, pool_tier: 'STANDARD', is_active: true, is_public: true, sort_order: 0 })
  planDialog.value = true
}

const openOfferDialog = (row?: any) => {
  Object.assign(offerForm, row ? {
    id: row.id, plan_id: row.plan_id, code: row.code, name: row.name, months: row.months,
    price_yuan: Number(row.price_cents || 0) / 100, is_draft: row.is_draft, is_purchase_enabled: row.is_purchase_enabled
  } : { id: 0, plan_id: plans.value.find(item => !item.is_archived)?.id || null, code: 'monthly', name: '月套餐', months: 1, price_yuan: 0, is_draft: false, is_purchase_enabled: true })
  offerDialog.value = true
}

const savePlan = async () => {
  submitting.value = true
  const data = await request('/0x/admin/plans', 'POST', { action: 'save_plan', ...planForm })
  submitting.value = false
  if (!data) return
  planDialog.value = false
  MessagePlugin.success('套餐已保存')
  loadData()
}

const saveOffer = async () => {
  submitting.value = true
  const data = await request('/0x/admin/plans', 'POST', {
    action: 'save_offer', ...offerForm, price_cents: Math.round(Number(offerForm.price_yuan || 0) * 100),
    is_purchase_enabled: offerForm.is_draft ? false : offerForm.is_purchase_enabled
  })
  submitting.value = false
  if (!data) return
  offerDialog.value = false
  MessagePlugin.success('价格已保存')
  loadData()
}

const archive = async (action: string, id: number) => {
  const data = await request('/0x/admin/plans', 'POST', { action, id })
  if (data) {
    MessagePlugin.success('已归档')
    loadData()
  }
}

onMounted(loadData)
</script>

<style scoped>
.admin-page { display: grid; gap: 20px; }
.admin-section { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.section-heading.compact { margin-bottom: 16px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } }
</style>
