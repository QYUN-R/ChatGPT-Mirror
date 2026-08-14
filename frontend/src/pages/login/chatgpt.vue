<template>
  <div class="account-entry-page">
    <section v-if="tableLoading || loadFailed || !tableData.length" class="entry-state">
      <t-loading :loading="tableLoading" size="medium">
        <div v-if="loadFailed" class="empty-state is-error">
          <div class="empty-state__icon"><local-icon name="server" /></div>
          <h2>账号池加载失败</h2>
          <p>请检查网络后重试；如果持续出现，请联系管理员处理。</p>
          <t-button theme="primary" @click="getUserChatGPTAccountList">重新加载</t-button>
        </div>
        <div v-else-if="!tableLoading && !tableData.length" class="empty-state">
          <div class="empty-state__icon"><local-icon name="server" /></div>
          <h2>目前没有可用账号，请联系管理员</h2>
          <p>管理员添加或恢复上游账号后，可重新进入“进入使用”选择账号。</p>
          <div class="empty-state__actions">
            <t-button v-if="isAdmin" theme="primary" @click="router.push('/account/chatgpt')">
              前往上游账号
            </t-button>
            <t-button v-else theme="primary" @click="router.push('/account/support')">
              联系管理员
            </t-button>
            <t-button variant="outline" @click="getUserChatGPTAccountList">刷新状态</t-button>
          </div>
        </div>
        <div v-else class="loading-placeholder">
          <span>正在加载账号池...</span>
        </div>
      </t-loading>
    </section>

    <t-dialog
      :visible="dialogVisible"
      header="请选择 ChatGPT 账号"
      dialog-class-name="account-selector-dialog"
      placement="center"
      :cancel-btn="null"
      :confirm-btn="null"
      width="930px"
      @close="goAccountCenter"
    >
      <t-loading :loading="tableLoading">
        <div class="dialog-content">
          <t-alert
            v-if="route.query.upstream === 'expired'"
            theme="error"
            message="刚才使用的上游账号已失效，已在账号池中标记，请选择其他可用账号。"
          />

          <div class="selector-toolbar">
            <div class="mode-switch">
              <span class="mode-switch__label">登录模式</span>
              <t-radio-group v-model="selectedMode" variant="default-filled">
                <t-radio-button value="api">API 模式</t-radio-button>
                <t-radio-button value="web">混合模式</t-radio-button>
              </t-radio-group>
            </div>
            <div class="selector-actions">
              <t-button
                theme="primary"
                :disabled="!hasUsableAccountForMode"
                @click="onSelect(null)"
              >
                {{ smartLoginLabel }}
              </t-button>
              <t-button variant="text" @click="goAccountCenter">账户中心</t-button>
            </div>
          </div>

          <t-alert
            v-if="!usableAccountCount"
            theme="error"
            message="目前没有可用账号，请联系管理员。失效账号已保留在下方，恢复后可继续使用。"
          />
          <t-alert
            v-else
            :theme="selectedMode === 'api' ? 'info' : 'warning'"
            :message="selectedMode === 'api'
              ? 'API 模式优先使用 AccessToken，适合接口能力。'
              : '混合模式优先建立网页会话，并保留接口链路回退。'"
          />

          <div class="pool-summary">
            <span><strong>{{ availableAccountCount }}</strong> 个空闲</span>
            <span><strong>{{ fullAccountCount }}</strong> 个已满</span>
            <span><strong>{{ unavailableAccountCount }}</strong> 个失效</span>
          </div>

          <div class="account-grid">
            <button
              v-for="item in tableData"
              :key="item.id"
              class="account-card"
              :class="{
                'is-current': item.is_current,
                'is-unavailable': !isAccountHealthy(item),
                'is-full': isAccountHealthy(item) && item.is_full && !item.is_current,
                'is-mode-unavailable': isAccountHealthy(item) && !supportsMode(item, selectedMode),
              }"
              type="button"
              :disabled="!isAccountUsable(item, selectedMode)"
              :aria-label="`${item.chatgpt_flag}，${accountStatusLabel(item)}`"
              @click="onSelect(item.id)"
            >
              <span class="account-card__topline">
                <t-tag
                  size="small"
                  :theme="isAccountHealthy(item) ? 'primary' : 'danger'"
                  :variant="isAccountHealthy(item) ? 'outline' : 'light'"
                >
                  {{ planLabel(item.plan_type) }}
                </t-tag>
                <span class="account-card__name">{{ item.chatgpt_flag }}</span>
              </span>

              <span class="account-card__tags">
                <t-tag size="small" :theme="item.access_token_valid ? 'success' : 'default'" variant="light">
                  API
                </t-tag>
                <t-tag size="small" :theme="item.session_token_valid ? 'success' : 'default'" variant="light">
                  混合
                </t-tag>
                <t-tag v-if="item.is_current" size="small" theme="success" variant="light">
                  当前使用
                </t-tag>
              </span>

              <template v-if="managedAssignment && item.binding_limit">
                <span class="account-card__status">
                  <span>当前人数</span>
                  <strong :class="{ danger: item.is_full && !item.is_current }">
                    {{ item.active_bindings }} / {{ item.binding_limit }} 人
                  </strong>
                </span>
                <t-progress
                  v-if="isAccountHealthy(item)"
                  :percentage="getGPTUsePercent(item)"
                  :status="usageStatus(item)"
                  :label="false"
                />
                <span v-if="isAccountHealthy(item)" class="capacity-note">
                  {{ item.is_full && !item.is_current ? '人数已满' : `剩余 ${item.remaining_capacity} 个名额` }}
                </span>
                <span v-else class="invalid-bar">账号失效，请选择其他账号</span>
              </template>

              <template v-else>
                <span class="account-card__status">
                  <span>实时状态</span>
                  <strong :class="{ danger: !isAccountHealthy(item) }">{{ accountStatusLabel(item) }}</strong>
                </span>
                <t-progress
                  v-if="isAccountHealthy(item)"
                  :percentage="getGPTUsePercent(item)"
                  :status="usageStatus(item)"
                  :label="false"
                />
                <span v-else class="invalid-bar">账号失效，请选择其他账号</span>
              </template>
            </button>
          </div>

          <div class="dialog-footer-note">
            <span>可随时选择空闲账号；账号是否上线及人数上限由管理员控制。</span>
            <t-button variant="outline" size="small" :loading="tableLoading" @click="getUserChatGPTAccountList">
              <template #icon><local-icon name="refresh" /></template>
              刷新账号状态
            </t-button>
          </div>
        </div>
      </t-loading>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { MessagePlugin } from 'tdesign-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import request from '@/api/request'
import { useUserStore } from '@/store/user'

interface TableData {
  id: number
  chatgpt_flag: string
  plan_type: string
  auth_status: boolean
  use_count: number
  access_token_valid: boolean
  session_token_valid: boolean
  supported_login_modes: string[]
  default_login_mode: 'api' | 'web'
  is_current?: boolean
  health_status?: string
  last_error?: string
  pool_id?: number | null
  pool_name?: string
  active_bindings?: number
  binding_limit?: number | null
  remaining_capacity?: number | null
  is_full?: boolean
  occupancy_ratio?: number
}

const tableLoading = ref(false)
const loadFailed = ref(false)
const dialogVisible = ref(false)
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const tableData = ref<TableData[]>([])
const managedAssignment = ref(false)
const selectedMode = ref<'api' | 'web'>('web')

const isAdmin = computed(() => userStore.isAdmin)
const usableAccountCount = computed(() => tableData.value.filter(isAccountHealthy).length)
const availableAccountCount = computed(() => tableData.value.filter(item =>
  isAccountHealthy(item) && !item.is_full,
).length)
const fullAccountCount = computed(() => tableData.value.filter(item =>
  isAccountHealthy(item) && item.is_full,
).length)
const unavailableAccountCount = computed(() => tableData.value.length - usableAccountCount.value)
const hasUsableAccountForMode = computed(() =>
  tableData.value.some(item => isAccountUsable(item, selectedMode.value)),
)
const smartLoginLabel = computed(() => managedAssignment.value ? '智能推荐空闲账号' : '智能分配可用账号')

onMounted(async () => {
  selectedMode.value = route.query.mode === 'api' ? 'api' : 'web'
  await getUserChatGPTAccountList()
})

const getGPTUsePercent = (item: TableData) => {
  if (managedAssignment.value && item.binding_limit) {
    return Math.min((Number(item.active_bindings || 0) / item.binding_limit) * 100, 100)
  }
  const maxLimitCount = item.plan_type === 'free' ? 80 : 320
  return Math.min((Number(item.use_count || 0) / maxLimitCount) * 100 + 1, 99)
}

const getUserChatGPTAccountList = async () => {
  tableLoading.value = true
  loadFailed.value = false
  const data = await request('/0x/user/chatgpt-list')
  tableLoading.value = false

  if (!data) {
    dialogVisible.value = false
    loadFailed.value = true
    return
  }

  const results: TableData[] = data.results || []
  tableData.value = results
  managedAssignment.value = Boolean(data.managed_assignment)

  const healthyAccounts = results.filter(isAccountHealthy)
  if (healthyAccounts.length && !healthyAccounts.some(item => supportsMode(item, selectedMode.value))) {
    const fallbackMode = selectedMode.value === 'web' ? 'api' : 'web'
    if (healthyAccounts.some(item => supportsMode(item, fallbackMode))) {
      selectedMode.value = fallbackMode
      MessagePlugin.info(
        fallbackMode === 'api'
          ? '当前没有支持混合模式的可用账号，已切换到 API 模式'
          : '当前没有支持 API 模式的可用账号，已切换到混合模式',
      )
    }
  }

  dialogVisible.value = results.length > 0
}

const supportsMode = (item: TableData, mode: 'api' | 'web') => {
  if (Array.isArray(item.supported_login_modes)) {
    return item.supported_login_modes.includes(mode)
  }
  return mode === 'api' ? Boolean(item.access_token_valid) : Boolean(item.session_token_valid)
}

const isAccountHealthy = (item: TableData) => {
  return item.auth_status !== false && item.health_status !== 'DEGRADED'
}

const isAccountUsable = (item: TableData, mode: 'api' | 'web') => {
  return isAccountHealthy(item)
    && supportsMode(item, mode)
    && (!item.is_full || Boolean(item.is_current))
}

const planLabel = (planType: string) => {
  const normalized = String(planType || 'free').trim().toLowerCase()
  const labels: Record<string, string> = {
    free: 'free',
    plus: 'plus',
    pro: 'pro',
    team: 'team',
    business: 'business',
    enterprise: 'enterprise',
  }
  return labels[normalized] || planType || 'ChatGPT'
}

const accountStatusLabel = (item: TableData) => {
  if (!isAccountHealthy(item)) return '账号失效'
  if (!supportsMode(item, selectedMode.value)) return '当前模式不可用'
  if (item.is_full && !item.is_current) return '人数已满'
  if (item.is_current) return '当前使用'
  const percentage = getGPTUsePercent(item)
  if (percentage < 40) return '空闲'
  if (percentage < 80) return '忙碌'
  return '繁忙但可用'
}

const usageStatus = (item: TableData): 'success' | 'warning' | 'error' => {
  const percentage = getGPTUsePercent(item)
  if (percentage < 40) return 'success'
  if (percentage < 80) return 'warning'
  return 'error'
}

const goAccountCenter = () => {
  dialogVisible.value = false
  router.push(isAdmin.value ? '/account/overview' : '/account/billing')
}

const onSelect = async (chatgptId: number | null) => {
  const current = tableData.value.find(item => item.id === chatgptId)
  if (current && !isAccountHealthy(current)) {
    MessagePlugin.error('该上游账号已经失效，请选择其他账号')
    return
  }
  if (current && !supportsMode(current, selectedMode.value)) {
    MessagePlugin.warning(
      selectedMode.value === 'api'
        ? '该账号当前不支持 API 模式，请切换到混合模式'
        : '该账号当前不支持混合模式，请切换到 API 模式',
    )
    return
  }
  if (current && current.is_full && !current.is_current) {
    MessagePlugin.warning('该账号当前人数已满，请选择其他空闲账号')
    return
  }
  if (chatgptId === null && !hasUsableAccountForMode.value) {
    MessagePlugin.warning('当前没有支持所选模式的可用账号')
    return
  }

  tableLoading.value = true
  const data = await request('/0x/chatgpt/login', 'POST', {
    chatgpt_id: chatgptId,
    login_mode: selectedMode.value,
  })
  tableLoading.value = false

  if (!data) return
  sessionStorage.setItem('tuwugpt.activePoolAccountId', String(chatgptId ?? ''))
  MessagePlugin.success('登录成功')
  if (data.login_url) {
    window.location.replace(data.login_url)
  }
}
</script>

<style scoped>
.account-entry-page {
  min-width: 0;
}

.entry-state {
  min-height: 420px;
}

.entry-state :deep(.t-loading) {
  display: block;
  min-height: 420px;
}

.loading-placeholder,
.empty-state {
  display: flex;
  min-height: 420px;
  padding: 48px 24px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  color: var(--app-text-muted);
  text-align: center;
  background: var(--app-surface);
  border: 1px dashed var(--app-border-strong);
  border-radius: 8px;
}

.empty-state.is-error {
  border-color: #e3b3ad;
}

.empty-state__icon {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  color: #4f8061;
  font-size: 24px;
  background: #eef3ef;
  border-radius: 50%;
}

.empty-state h2 {
  margin: 16px 0 0;
  color: var(--app-text);
  font-size: 18px;
  font-weight: 600;
}

.empty-state p {
  max-width: 460px;
  margin: 8px 0 20px;
  font-size: 13px;
  line-height: 1.6;
}

.empty-state__actions {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
}

.dialog-content {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.selector-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.mode-switch,
.selector-actions,
.pool-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.mode-switch__label {
  color: var(--app-text);
  font-size: 14px;
  font-weight: 600;
}

.pool-summary {
  color: var(--app-text-muted);
  font-size: 12px;
}

.pool-summary strong {
  color: var(--app-text);
  font-size: 14px;
}

.account-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  min-height: 136px;
}

.account-card {
  display: flex;
  min-width: 0;
  min-height: 138px;
  padding: 12px;
  flex-direction: column;
  color: var(--app-text);
  text-align: left;
  cursor: pointer;
  background: #f5f6f7;
  border: 1px solid transparent;
  border-radius: 7px;
}

.account-card:hover:not(:disabled) {
  background: #f0f3f1;
  border-color: #9ab8a3;
}

.account-card.is-current {
  border-color: #79a789;
  box-shadow: inset 3px 0 0 #4f8061;
}

.account-card.is-unavailable {
  background: #fbefed;
  border-color: #e1b6b0;
}

.account-card.is-full {
  background: #f5f5f3;
  border-color: #d9d9d4;
}

.account-card.is-mode-unavailable {
  background: #fbfaf4;
  border-color: #ded6b7;
}

.account-card:disabled {
  cursor: not-allowed;
  opacity: 1;
}

.account-card__topline,
.account-card__status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.account-card__name {
  min-width: 0;
  overflow: hidden;
  color: var(--app-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-card__tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 12px;
}

.account-card__status {
  margin: 14px 0 8px;
  color: var(--app-text-muted);
  font-size: 12px;
}

.account-card__status strong {
  color: var(--app-text);
  font-weight: 500;
}

.account-card__status strong.danger {
  color: #a54238;
}

.invalid-bar {
  display: block;
  padding: 6px 8px;
  color: #9f443b;
  font-size: 11px;
  line-height: 1.4;
  background: #f4deda;
  border-radius: 5px;
}

.capacity-note {
  display: block;
  margin-top: 7px;
  color: var(--app-text-muted);
  font-size: 11px;
}

.dialog-footer-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 12px;
  color: var(--app-text-muted);
  font-size: 12px;
  border-top: 1px solid var(--app-border);
}

:global(.account-selector-dialog) {
  display: flex;
  max-height: calc(100dvh - 32px);
  flex-direction: column;
  overflow: hidden;
}

:global(.account-selector-dialog .t-dialog__header) {
  flex: 0 0 auto;
}

:global(.account-selector-dialog .t-dialog__body) {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
  overscroll-behavior: contain;
}

@media (max-width: 700px) {
  .selector-toolbar,
  .dialog-footer-note {
    align-items: stretch;
    flex-direction: column;
  }

  .selector-actions {
    justify-content: flex-start;
  }

  .account-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  :global(.t-dialog__position:has(.account-selector-dialog)) {
    box-sizing: border-box;
    height: 100% !important;
    min-height: 0 !important;
    padding: max(12px, env(safe-area-inset-top)) 0 max(12px, env(safe-area-inset-bottom)) !important;
  }

  :global(.account-selector-dialog) {
    width: calc(100vw - 24px) !important;
    max-height: 100%;
    margin: 0 auto;
  }

  .mode-switch {
    align-items: stretch;
    flex-direction: column;
  }

  .mode-switch :deep(.t-radio-group) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }

  .mode-switch :deep(.t-radio-button) {
    justify-content: center;
  }

  .selector-actions {
    display: grid;
    grid-template-columns: 1fr;
  }

  .selector-actions :deep(.t-button),
  .dialog-footer-note :deep(.t-button) {
    width: 100%;
  }

  .account-grid {
    grid-template-columns: 1fr;
  }

  .empty-state__actions {
    width: 100%;
    flex-direction: column;
  }

  .empty-state__actions :deep(.t-button) {
    width: 100%;
  }
}
</style>
