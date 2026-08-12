<template>
  <div class="billing-page">
    <t-loading :loading="loading">
      <section class="subscription-panel">
        <div class="section-heading">
          <div>
            <h2>当前套餐</h2>
            <p>查看套餐状态与有效时间</p>
          </div>
          <t-space>
            <t-button variant="outline" @click="loadData">
              <template #icon><t-icon name="refresh" /></template>
              刷新
            </t-button>
            <t-button theme="primary" :disabled="!me?.service_available" @click="enterService">
              <template #icon><t-icon name="play-circle" /></template>
              进入使用页面
            </t-button>
          </t-space>
        </div>

        <div v-if="me?.subscription" class="subscription-summary">
          <div class="subscription-identity">
            <span>当前套餐</span>
            <strong>{{ me.subscription.plan.name }}</strong>
            <small>{{ me.subscription.offer?.name || `${me.subscription.offer?.months || 1} 个月` }}</small>
          </div>
          <div class="subscription-metric">
            <span>套餐状态</span>
            <t-tag :theme="statusTheme(me.subscription.status) as any" variant="light">
              {{ statusLabel(me.subscription.status) }}
            </t-tag>
          </div>
          <div class="subscription-metric expiry-metric">
            <span>套餐失效时间</span>
            <strong>{{ formatDateTime(me.subscription.ends_at) }}</strong>
            <small>剩余 {{ remainingDays(me.subscription.ends_at) }} 天</small>
          </div>
          <div class="subscription-metric">
            <span>本月使用</span>
            <strong>{{ me.usage?.month ?? 0 }} 次</strong>
          </div>
        </div>
        <div v-else class="empty-subscription">
          <div>
            <strong>尚未开通套餐</strong>
            <span>购买卡密并完成兑换后即可进入使用页面。</span>
          </div>
          <t-button v-if="purchaseUrl" theme="primary" @click="openPurchaseLink">
            <template #icon><t-icon name="cart" /></template>
            购买卡密
          </t-button>
        </div>
        <t-alert
          v-if="me?.subscription?.scheduled_plan_name"
          theme="info"
          :message="`当前套餐结束后将切换为 ${me.subscription.scheduled_plan_name}`"
        />
      </section>

      <section class="redemption-section">
        <div class="section-heading redemption-heading">
          <div>
            <h2>卡密兑换</h2>
            <p>卡密仅可使用一次，兑换成功后套餐立即生效或续期</p>
          </div>
          <t-button v-if="purchaseUrl" variant="outline" @click="openPurchaseLink">
            <template #icon><t-icon name="cart" /></template>
            购买卡密
          </t-button>
        </div>
        <div class="redemption-form">
          <t-input
            v-model="redemptionCode"
            :disabled="!redemptionEnabled"
            clearable
            maxlength="128"
            placeholder="输入卡密，例如 TWG-XXXX-XXXX-XXXX"
            @enter="submitRedemption"
          />
          <t-button
            theme="primary"
            :loading="redeeming"
            :disabled="!redemptionEnabled || !redemptionCode.trim()"
            @click="submitRedemption"
          >
            立即兑换
          </t-button>
        </div>
        <span v-if="!redemptionEnabled" class="redemption-hint">卡密兑换暂未开放，请联系管理员。</span>
      </section>

      <section class="plans-section">
        <div class="section-heading">
          <div>
            <h2>可兑换套餐</h2>
            <p>价格由管理员维护，购买后返回本页输入对应卡密</p>
          </div>
        </div>
        <div class="plan-grid">
          <article v-for="plan in plans" :key="plan.id" class="plan-panel" :class="{ current: isCurrent(plan) }">
            <div class="plan-topline">
              <div>
                <div class="plan-title-row">
                  <h3>{{ plan.name }}</h3>
                  <t-tag v-if="isCurrent(plan)" theme="success" variant="light">当前套餐</t-tag>
                </div>
                <p>{{ plan.tagline }}</p>
              </div>
              <div class="plan-price" v-if="selectedOffer(plan)">
                <strong>{{ formatMoney(selectedOffer(plan).price_cents) }}</strong>
                <span>/ {{ selectedOffer(plan).name }}</span>
              </div>
            </div>
            <t-radio-group
              v-if="plan.offers?.length > 1"
              v-model="selectedOfferIds[plan.id]"
              class="offer-selector"
              size="small"
              variant="default-filled"
            >
              <t-radio-button v-for="offer in plan.offers" :key="offer.id" :value="offer.id">
                {{ offer.name }} {{ formatMoney(offer.price_cents, offer.currency) }}
              </t-radio-button>
            </t-radio-group>
            <div class="plan-actions">
              <span>{{ selectedOffer(plan)?.months || 1 }} 个月有效期</span>
              <t-button :theme="plan.pool_tier === 'PREMIUM' ? 'primary' : 'default'" variant="outline" :disabled="!purchaseUrl" @click="openPurchaseLink">
                购买对应卡密
              </t-button>
            </div>
          </article>
        </div>
      </section>

      <section class="history-section">
        <div class="section-heading">
          <div>
            <h2>卡密充值记录</h2>
            <p>记录充值套餐、充值时间和该次充值后的失效时间</p>
          </div>
        </div>
        <div class="history-table">
          <t-table :data="redemptionOrders" :columns="historyColumns" row-key="id" :hover="true">
            <template #term="{ row }">{{ row.offer_name }} · {{ row.entitlement_months }} 个月</template>
            <template #paid_at="{ row }">{{ formatDateTime(row.paid_at) }}</template>
            <template #entitlement_ends_at="{ row }">{{ formatDateTime(row.entitlement_ends_at) }}</template>
            <template #status>
              <t-tag theme="success" variant="light">充值成功</t-tag>
            </template>
          </t-table>
        </div>
        <div v-if="redemptionOrders.length" class="history-card-list">
          <article v-for="order in redemptionOrders" :key="order.id" class="history-card">
            <div class="history-card-title">
              <strong>{{ order.plan_name }}</strong>
              <t-tag theme="success" variant="light">充值成功</t-tag>
            </div>
            <dl>
              <div><dt>充值套餐</dt><dd>{{ order.offer_name }} · {{ order.entitlement_months }} 个月</dd></div>
              <div><dt>充值时间</dt><dd>{{ formatDateTime(order.paid_at) }}</dd></div>
              <div><dt>充值后失效</dt><dd>{{ formatDateTime(order.entitlement_ends_at) }}</dd></div>
            </dl>
          </article>
        </div>
        <div v-if="!redemptionOrders.length" class="table-empty">暂无卡密充值记录</div>
      </section>
    </t-loading>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime, formatMoney, remainingDays, statusLabel, statusTheme } from '@/utils/billing'

const loading = ref(false)
const router = useRouter()
const me = ref<any>(null)
const plans = ref<any[]>([])
const redemptionOrders = ref<any[]>([])
const selectedOfferIds = ref<Record<number, number>>({})
const redemptionEnabled = ref(false)
const purchaseUrl = ref('')
const redemptionCode = ref('')
const redeeming = ref(false)

const historyColumns = [
  { colKey: 'plan_name', title: '充值套餐', minWidth: 140 },
  { colKey: 'term', title: '套餐周期', cell: 'term', minWidth: 150 },
  { colKey: 'paid_at', title: '充值时间', cell: 'paid_at', minWidth: 170 },
  { colKey: 'entitlement_ends_at', title: '充值后失效时间', cell: 'entitlement_ends_at', minWidth: 180 },
  { colKey: 'status', title: '状态', cell: 'status', width: 110 }
]

const isCurrent = (plan: any) => me.value?.subscription?.plan?.id === plan.id
const selectedOffer = (plan: any) => {
  const offers = plan.offers || []
  const selectedId = Number(selectedOfferIds.value[plan.id])
  return offers.find((offer: any) => offer.id === selectedId) || offers[0] || null
}

const loadData = async () => {
  loading.value = true
  const [planData, meData, orderData] = await Promise.all([
    request('/0x/billing/plans'),
    request('/0x/billing/me'),
    request('/0x/billing/orders?provider=redemption_code&page_size=50')
  ])
  plans.value = planData?.plans || []
  const nextOfferIds = { ...selectedOfferIds.value }
  for (const plan of plans.value) {
    if (!plan.offers?.some((offer: any) => offer.id === Number(nextOfferIds[plan.id])) && plan.offers?.[0]) {
      nextOfferIds[plan.id] = plan.offers[0].id
    }
  }
  selectedOfferIds.value = nextOfferIds
  redemptionEnabled.value = Boolean(planData?.redemption_enabled)
  purchaseUrl.value = String(planData?.purchase_url || '')
  me.value = meData
  redemptionOrders.value = orderData?.results || []
  loading.value = false
}

const submitRedemption = async () => {
  if (!redemptionEnabled.value || !redemptionCode.value.trim()) return
  redeeming.value = true
  const data = await request('/0x/billing/redemption-codes/redeem', 'POST', { code: redemptionCode.value.trim() })
  redeeming.value = false
  if (!data?.order) return
  redemptionCode.value = ''
  MessagePlugin.success(data.already_redeemed ? '该卡密已兑换，已恢复充值结果' : '卡密兑换成功，套餐已生效')
  await loadData()
}

const openPurchaseLink = () => {
  if (purchaseUrl.value) window.open(purchaseUrl.value, '_blank', 'noopener,noreferrer')
}

const enterService = async () => {
  if (!me.value?.service_available) {
    MessagePlugin.warning('套餐尚未生效，请先兑换卡密')
    return
  }
  await router.push({ name: 'LoginChatgpt' })
}

onMounted(loadData)
</script>

<style scoped>
.billing-page { display: grid; min-width: 0; gap: 20px; }
.billing-page :deep(.t-loading__parent) { min-width: 0; }
.subscription-panel,
.redemption-section,
.plans-section,
.history-section { min-width: 0; padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.section-heading h2 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: 0; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; line-height: 1.5; }
.subscription-summary { display: grid; grid-template-columns: 1.3fr 0.8fr 1.4fr 0.8fr; border: 1px solid #e3e3df; border-radius: 7px; overflow: hidden; }
.subscription-identity,
.subscription-metric { min-width: 0; padding: 20px; border-right: 1px solid #e3e3df; }
.subscription-summary > div:last-child { border-right: 0; }
.subscription-summary span { display: block; color: var(--app-text-muted); font-size: 12px; }
.subscription-summary strong { display: block; margin-top: 8px; color: var(--app-text); font-size: 17px; font-weight: 600; }
.subscription-summary small { display: block; margin-top: 5px; color: var(--app-text-muted); font-size: 12px; }
.subscription-metric :deep(.t-tag) { margin-top: 10px; }
.expiry-metric strong { font-variant-numeric: tabular-nums; }
.empty-subscription { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 24px; background: #f6f6f3; border: 1px dashed var(--app-border-strong); border-radius: 7px; }
.empty-subscription strong,
.empty-subscription span { display: block; }
.empty-subscription span { margin-top: 6px; color: var(--app-text-muted); font-size: 13px; }
.subscription-panel :deep(.t-alert) { margin-top: 14px; }
.redemption-heading { margin-bottom: 14px; }
.redemption-form { display: grid; grid-template-columns: minmax(260px, 620px) 120px; gap: 10px; align-items: center; }
.redemption-form :deep(.t-input__inner) { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.redemption-hint { display: block; margin-top: 8px; color: var(--app-text-muted); font-size: 12px; }
.plan-grid { display: grid; min-width: 0; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.plan-panel { display: grid; min-width: 0; min-height: 220px; padding: 22px; border: 1px solid var(--app-border-strong); border-radius: 8px; }
.plan-panel.current { border-color: #91b7a0; box-shadow: inset 0 3px 0 #4f8061; }
.plan-topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.plan-title-row { display: flex; align-items: center; gap: 10px; }
.plan-topline h3 { margin: 0; font-size: 20px; font-weight: 600; }
.plan-topline p { margin-top: 8px; color: var(--app-text-muted); font-size: 14px; }
.plan-price { display: flex; align-items: baseline; white-space: nowrap; }
.plan-price strong { font-size: 29px; font-weight: 600; }
.plan-price span { margin-left: 5px; color: var(--app-text-muted); font-size: 13px; }
.offer-selector { margin-top: 18px; overflow-x: auto; white-space: nowrap; }
.plan-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; align-self: end; }
.plan-actions > span { color: var(--app-text-muted); font-size: 13px; }
.history-table { min-width: 0; overflow-x: auto; }
.history-card-list { display: none; }
.table-empty { padding: 30px; color: var(--app-text-muted); font-size: 13px; text-align: center; border: 1px solid #e7e7e3; border-top: 0; }
@media (max-width: 1000px) { .subscription-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } .subscription-summary > div { border-bottom: 1px solid #e3e3df; } .subscription-summary > div:nth-child(2n) { border-right: 0; } .subscription-summary > div:nth-last-child(-n+2) { border-bottom: 0; } }
@media (max-width: 820px) { .plan-grid { grid-template-columns: 1fr; } .section-heading { align-items: flex-start; flex-direction: column; } }
@media (max-width: 620px) {
  .subscription-panel, .redemption-section, .plans-section, .history-section { padding: 18px 14px; }
  .section-heading :deep(.t-space) { width: 100%; }
  .section-heading :deep(.t-space .t-button) { flex: 1; }
  .redemption-form { grid-template-columns: 1fr; }
  .subscription-summary { grid-template-columns: 1fr; }
  .subscription-summary > div { border-right: 0; border-bottom: 1px solid #e3e3df; }
  .subscription-summary > div:nth-last-child(-n+2) { border-bottom: 1px solid #e3e3df; }
  .subscription-summary > div:last-child { border-bottom: 0; }
  .empty-subscription { align-items: stretch; flex-direction: column; }
  .plan-topline { flex-direction: column; }
  .plan-actions { align-items: stretch; flex-direction: column; }
  .history-table { display: none; }
  .history-card-list { display: grid; gap: 10px; }
  .history-card { padding: 15px; background: #f6f6f3; border: 1px solid #e7e7e3; border-radius: 7px; }
  .history-card-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .history-card-title strong { font-size: 15px; }
  .history-card dl { display: grid; gap: 8px; margin-top: 13px; }
  .history-card dl > div { display: flex; justify-content: space-between; gap: 16px; }
  .history-card dt { color: var(--app-text-muted); font-size: 12px; }
  .history-card dd { margin: 0; font-size: 12px; text-align: right; }
}
</style>
