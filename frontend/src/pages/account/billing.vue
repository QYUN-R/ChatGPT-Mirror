<template>
  <div class="billing-page">
    <t-loading :loading="loading">
      <section class="subscription-panel">
        <div class="section-heading">
          <div>
            <h2>当前订阅</h2>
            <p>套餐权益、有效期和使用情况</p>
          </div>
          <t-button variant="outline" @click="loadData">
            <template #icon><t-icon name="refresh" /></template>
            刷新
          </t-button>
        </div>
        <div v-if="me?.subscription" class="subscription-grid">
          <div class="subscription-item">
            <span>套餐</span>
            <strong>{{ me.subscription.plan.name }}</strong>
          </div>
          <div class="subscription-item">
            <span>状态</span>
            <t-tag :theme="statusTheme(me.subscription.status) as any" variant="light">
              {{ statusLabel(me.subscription.status) }}
            </t-tag>
          </div>
          <div class="subscription-item">
            <span>到期时间</span>
            <strong>{{ formatDateTime(me.subscription.ends_at) }}</strong>
          </div>
          <div class="subscription-item">
            <span>剩余天数</span>
            <strong>{{ remainingDays(me.subscription.ends_at) }} 天</strong>
          </div>
          <div class="subscription-item">
            <span>本月使用</span>
            <strong>{{ me.usage?.month ?? 0 }} 次</strong>
          </div>
          <div class="subscription-item">
            <span>账号状态</span>
            <strong>{{ me.subscription.assignment ? '已固定分配' : '首次使用时分配' }}</strong>
          </div>
        </div>
        <div v-else class="empty-subscription">
          <div>
            <strong>尚未开通套餐</strong>
            <span>选择下方套餐后即可建立固定 Plus 账号绑定。</span>
          </div>
        </div>
        <t-alert
          v-if="me?.subscription?.scheduled_plan_name"
          theme="info"
          :message="`已安排在当前周期结束后切换为 ${me.subscription.scheduled_plan_name}`"
        />
      </section>

      <section class="plans-section">
        <div class="section-heading">
          <div>
            <h2>选择套餐</h2>
            <p>季度套餐保留在后台，定价完成前不可购买</p>
          </div>
          <t-radio-group v-model="period" variant="default-filled" size="small">
            <t-radio-button value="monthly">月付</t-radio-button>
            <t-radio-button value="quarterly" disabled>季付（待定价）</t-radio-button>
          </t-radio-group>
        </div>
        <div class="plan-grid">
          <article v-for="plan in plans" :key="plan.id" class="plan-panel" :class="{ current: isCurrent(plan) }">
            <div class="plan-topline">
              <div>
                <h3>{{ plan.name }}</h3>
                <p>{{ plan.tagline }}</p>
              </div>
              <div class="plan-price" v-if="monthlyOffer(plan)">
                <strong>{{ formatMoney(monthlyOffer(plan).price_cents) }}</strong>
                <span>/ 月</span>
              </div>
            </div>
            <dl class="plan-facts">
              <div>
                <dt>号池类型</dt>
                <dd>{{ plan.pool_tier === 'PREMIUM' ? '高级 Plus 池' : '普通 Plus 池' }}</dd>
              </div>
              <div>
                <dt>可用席位</dt>
                <dd>{{ plan.capacity.available }} / {{ plan.capacity.total }}</dd>
              </div>
              <div>
                <dt>账号绑定</dt>
                <dd>{{ plan.pool_tier === 'PREMIUM' ? '每账号最多 3 人' : '每账号 3-8 人' }}</dd>
              </div>
            </dl>
            <div class="plan-actions">
              <t-tag v-if="isCurrent(plan)" theme="success" variant="light">当前使用中</t-tag>
              <span v-else></span>
              <t-button
                :theme="plan.pool_tier === 'PREMIUM' ? 'primary' : 'default'"
                :variant="plan.pool_tier === 'PREMIUM' ? 'base' : 'outline'"
                :loading="submittingPlanId === plan.id"
                :disabled="!monthlyOffer(plan) || (!isCurrent(plan) && plan.capacity.is_full)"
                @click="submitPlan(plan)"
              >
                {{ actionLabel(plan) }}
              </t-button>
            </div>
          </article>
        </div>
      </section>

      <section class="orders-section">
        <div class="section-heading">
          <div>
            <h2>最近订单</h2>
            <p>订单金额和套餐快照会永久保留</p>
          </div>
        </div>
        <div class="orders-table">
          <t-table :data="orders" :columns="orderColumns" row-key="id" :hover="true">
            <template #price="{ row }">{{ formatMoney(row.price_cents, row.currency) }}</template>
            <template #status="{ row }">
              <t-tag :theme="statusTheme(row.status) as any" variant="light">{{ statusLabel(row.status) }}</t-tag>
            </template>
            <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
            <template #paid_at="{ row }">{{ formatDateTime(row.paid_at) }}</template>
          </t-table>
        </div>
        <div v-if="orders.length" class="order-card-list">
          <article v-for="order in orders" :key="order.id" class="order-card">
            <div>
              <strong>{{ order.plan_name }}</strong>
              <span>{{ order.order_no }}</span>
            </div>
            <div class="order-card-meta">
              <span>{{ formatMoney(order.price_cents, order.currency) }}</span>
              <t-tag :theme="statusTheme(order.status) as any" variant="light">{{ statusLabel(order.status) }}</t-tag>
            </div>
            <small>{{ formatDateTime(order.paid_at || order.created_at) }}</small>
          </article>
        </div>
        <div v-if="!orders.length" class="table-empty">暂无订单</div>
      </section>
    </t-loading>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime, formatMoney, remainingDays, statusLabel, statusTheme } from '@/utils/billing'

const loading = ref(false)
const me = ref<any>(null)
const plans = ref<any[]>([])
const orders = ref<any[]>([])
const period = ref('monthly')
const submittingPlanId = ref<number | null>(null)

const orderColumns = [
  { colKey: 'order_no', title: '订单号', width: 190 },
  { colKey: 'plan_name', title: '套餐', width: 130 },
  { colKey: 'order_type', title: '类型', width: 100 },
  { colKey: 'price', title: '金额', cell: 'price', width: 110 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 },
  { colKey: 'created_at', title: '下单时间', cell: 'created_at', width: 170 },
  { colKey: 'paid_at', title: '支付时间', cell: 'paid_at', width: 170 }
]

const monthlyOffer = (plan: any) => plan.offers?.find((offer: any) => offer.code === 'monthly' && !offer.is_draft)
const isCurrent = (plan: any) => me.value?.subscription?.plan?.id === plan.id

const actionLabel = (plan: any) => {
  const current = me.value?.subscription?.plan
  if (!current) return '立即开通'
  if (current.id === plan.id) return '续费'
  if (current.pool_tier === 'STANDARD' && plan.pool_tier === 'PREMIUM') return '升级套餐'
  if (current.pool_tier === 'PREMIUM' && plan.pool_tier === 'STANDARD') return '下期降级'
  return '切换套餐'
}

const loadData = async () => {
  loading.value = true
  const [planData, meData, orderData] = await Promise.all([
    request('/0x/billing/plans'),
    request('/0x/billing/me'),
    request('/0x/billing/orders?page_size=8')
  ])
  plans.value = planData?.plans || []
  me.value = meData
  orders.value = orderData?.results || []
  loading.value = false
}

const submitPlan = async (plan: any) => {
  const offer = monthlyOffer(plan)
  if (!offer) return
  submittingPlanId.value = plan.id
  const data = await request('/0x/billing/orders', 'POST', {
    offer_id: offer.id,
    idempotency_key: crypto.randomUUID(),
    pay_now: true
  })
  submittingPlanId.value = null
  if (!data?.order) return
  MessagePlugin.success(data.order.status === 'PAID' ? '套餐已生效' : '订单已创建，等待管理员确认')
  await loadData()
}

onMounted(loadData)
</script>

<style scoped>
.billing-page { display: grid; min-width: 0; gap: 20px; }
.billing-page :deep(.t-loading__parent) { min-width: 0; }
.subscription-panel,
.plans-section,
.orders-section { min-width: 0; padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.section-heading h2 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: 0; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; line-height: 1.5; }
.subscription-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); border: 1px solid #e7e7e3; border-radius: 7px; }
.subscription-item { min-width: 0; padding: 18px; border-right: 1px solid #e7e7e3; }
.subscription-item:last-child { border-right: 0; }
.subscription-item span { display: block; color: var(--app-text-muted); font-size: 12px; }
.subscription-item strong { display: block; margin-top: 9px; overflow: hidden; color: var(--app-text); font-size: 15px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.empty-subscription { padding: 24px; background: #f6f6f3; border: 1px dashed var(--app-border-strong); border-radius: 7px; }
.empty-subscription strong,
.empty-subscription span { display: block; }
.empty-subscription span { margin-top: 6px; color: var(--app-text-muted); font-size: 13px; }
.subscription-panel :deep(.t-alert) { margin-top: 14px; }
.plan-grid { display: grid; min-width: 0; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.plan-panel { display: grid; min-width: 0; min-height: 290px; padding: 22px; border: 1px solid var(--app-border-strong); border-radius: 8px; }
.plan-panel.current { border-color: #91b7a0; box-shadow: inset 0 3px 0 #4f8061; }
.plan-topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.plan-topline h3 { font-size: 20px; font-weight: 600; letter-spacing: 0; }
.plan-topline p { margin-top: 8px; color: var(--app-text-muted); font-size: 14px; }
.plan-price { display: flex; align-items: baseline; white-space: nowrap; }
.plan-price strong { font-size: 29px; font-weight: 600; }
.plan-price span { margin-left: 5px; color: var(--app-text-muted); font-size: 13px; }
.plan-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-self: center; margin: 22px 0; padding: 16px 0; border-top: 1px solid #ecece8; border-bottom: 1px solid #ecece8; }
.plan-facts div { padding: 0 14px; border-right: 1px solid #ecece8; }
.plan-facts div:first-child { padding-left: 0; }
.plan-facts div:last-child { padding-right: 0; border-right: 0; }
.plan-facts dt { color: var(--app-text-muted); font-size: 12px; }
.plan-facts dd { margin-top: 8px; font-size: 14px; font-weight: 500; }
.plan-actions { display: flex; align-items: center; justify-content: space-between; align-self: end; }
.orders-table { min-width: 0; overflow-x: auto; }
.order-card-list { display: none; }
.table-empty { padding: 28px; color: var(--app-text-muted); font-size: 13px; text-align: center; border: 1px solid #e7e7e3; border-top: 0; }
@media (max-width: 1180px) { .subscription-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } .subscription-item:nth-child(3) { border-right: 0; } .subscription-item:nth-child(-n+3) { border-bottom: 1px solid #e7e7e3; } }
@media (max-width: 820px) { .plan-grid { grid-template-columns: 1fr; } .section-heading { align-items: flex-start; flex-direction: column; } }
@media (max-width: 620px) {
  .subscription-panel, .plans-section, .orders-section { padding: 18px 14px; }
  .subscription-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .subscription-item { border-bottom: 1px solid #e7e7e3; }
  .subscription-item:nth-child(2n) { border-right: 0; }
  .subscription-item:nth-last-child(-n+2) { border-bottom: 0; }
  .plan-topline { flex-direction: column; }
  .plan-facts { grid-template-columns: 1fr; gap: 12px; }
  .plan-facts div { padding: 0; border: 0; }
  .orders-table { display: none; }
  .order-card-list { display: grid; gap: 10px; }
  .order-card { display: grid; gap: 9px; padding: 14px; background: #f6f6f3; border: 1px solid #e7e7e3; border-radius: 7px; }
  .order-card > div:first-child { display: grid; gap: 4px; min-width: 0; }
  .order-card strong { font-size: 15px; font-weight: 600; }
  .order-card > div:first-child span { overflow: hidden; color: var(--app-text-muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .order-card-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .order-card-meta > span { font-size: 15px; font-weight: 600; }
  .order-card small { color: var(--app-text-muted); font-size: 12px; }
}
</style>
