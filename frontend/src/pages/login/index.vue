<template>
  <main class="login-page">
    <section class="login-panel" aria-labelledby="login-title">
      <div v-if="cfg.notice" class="login-notice" role="status">{{ cfg.notice }}</div>

      <header class="login-header">
        <h1 id="login-title">{{ pageCopy.title }}</h1>
        <p>{{ pageCopy.description }}</p>
      </header>

      <t-loading :loading="loading" class="login-loading">
        <t-form
          ref="loginFormRef"
          :data="form"
          :label-width="0"
          :rules="rules"
          class="login-form"
          @submit="onSubmit"
        >
          <div v-if="isLogin" class="form-field">
            <label for="login-identifier">邮箱</label>
            <t-form-item name="identifier">
              <t-input
                id="login-identifier"
                v-model="form.identifier"
                autocomplete="username"
                placeholder="输入邮箱；旧账号可使用用户名"
                size="large"
              />
            </t-form-item>
          </div>

          <div v-if="needsEmail" class="form-field">
            <label for="auth-email">邮箱</label>
            <t-form-item name="email">
              <t-input
                id="auth-email"
                v-model="form.email"
                type="email"
                autocomplete="email"
                placeholder="仅支持 QQ、网易和 Google 邮箱"
                size="large"
              />
            </t-form-item>
          </div>

          <div v-if="needsCurrentPassword" class="form-field">
            <label for="login-password">密码</label>
            <t-form-item name="password">
              <t-input
                id="login-password"
                v-model="form.password"
                type="password"
                :autocomplete="isRegister ? 'new-password' : 'current-password'"
                placeholder="请输入密码"
                size="large"
              />
            </t-form-item>
          </div>

          <div v-if="isForgotPassword" class="form-field">
            <label for="new-password">新密码</label>
            <t-form-item name="new_password">
              <t-input
                id="new-password"
                v-model="form.new_password"
                type="password"
                autocomplete="new-password"
                placeholder="至少使用 8 位高强度密码"
                size="large"
              />
            </t-form-item>
          </div>

          <div v-if="needsVerificationCode" class="form-field">
            <label for="verification-code">邮箱验证码</label>
            <t-form-item name="verification_code">
              <div class="verification-row">
                <t-input
                  id="verification-code"
                  v-model="form.verification_code"
                  autocomplete="one-time-code"
                  inputmode="numeric"
                  maxlength="6"
                  placeholder="6 位验证码"
                  size="large"
                />
                <t-button
                  type="button"
                  variant="outline"
                  class="verification-button"
                  :loading="verificationSending"
                  :disabled="loading || verificationSending || cooldown > 0 || (turnstileEnabled && !turnstileToken)"
                  @click="requestVerificationCode"
                >
                  {{ cooldown > 0 ? `${cooldown}s 后重发` : '发送验证码' }}
                </t-button>
              </div>
              <p v-if="verificationDeliveryMessage" class="verification-status">{{ verificationDeliveryMessage }}</p>
            </t-form-item>
          </div>

          <div v-if="isRegister && !cfg.billing_enabled" class="form-field">
            <label for="register-upstream-token">上游账号令牌</label>
            <t-form-item name="chatgpt_token">
              <t-textarea
                id="register-upstream-token"
                v-model="form.chatgpt_token"
                placeholder="请粘贴用于绑定的账号令牌"
                :autosize="{ minRows: 3, maxRows: 5 }"
              />
            </t-form-item>
          </div>

          <div v-if="turnstileEnabled" class="turnstile-field">
            <div ref="turnstileContainer" class="turnstile-widget"></div>
            <p v-if="turnstileError" class="turnstile-error" role="alert">{{ turnstileError }}</p>
          </div>

          <t-form-item class="submit-item">
            <t-button
              type="submit"
              size="large"
              class="login-button"
              :disabled="turnstileEnabled && !turnstileToken && needsTurnstileForSubmit"
            >
              {{ pageCopy.submit }}
            </t-button>
          </t-form-item>
        </t-form>
      </t-loading>

      <div class="account-switch">
        <template v-if="isLogin">
          <router-link to="/forgot-password">忘记密码</router-link>
          <span v-if="cfg.allow_register" class="switch-separator">|</span>
          <router-link v-if="cfg.allow_register" to="/register">创建账户</router-link>
        </template>
        <template v-else-if="isRegister">
          已有账户？<router-link to="/login">登录</router-link>
        </template>
        <template v-else-if="isForgotPassword">
          想起密码了？<router-link to="/login">返回登录</router-link>
        </template>
        <template v-else>
          <router-link to="/login">返回登录</router-link>
        </template>
      </div>

      <template v-if="isLogin">
        <div class="login-divider" aria-hidden="true"><span>或</span></div>
        <button
          class="free-button"
          type="button"
          :disabled="loading || (turnstileEnabled && !turnstileToken)"
          @click="goFree"
        >
          免费体验
        </button>
      </template>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { useUserStore } from '@/store/user'

type TurnstileApi = {
  render: (container: HTMLElement, options: Record<string, unknown>) => string
  reset: (widgetId: string) => void
  remove: (widgetId: string) => void
}

declare global {
  interface Window {
    turnstile?: TurnstileApi
  }
}

const BINDING_TICKET_STORAGE_KEY = 'chat2.email-binding-ticket'
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const verificationSending = ref(false)
const cooldown = ref(0)
const verificationDeliveryMessage = ref('')
const cfg = ref({
  show_github: true,
  allow_register: false,
  billing_enabled: false,
  email_verification_enabled: false,
  notice: '',
  turnstile_enabled: false,
  turnstile_site_key: ''
})
const form = reactive({
  identifier: '',
  email: '',
  password: '',
  new_password: '',
  verification_code: '',
  chatgpt_token: ''
})
const loginFormRef = ref()
const turnstileContainer = ref<HTMLElement | null>(null)
const turnstileToken = ref('')
const turnstileError = ref('')
const turnstileWidgetId = ref<string | null>(null)
let turnstileScriptPromise: Promise<void> | null = null
let cooldownTimer: number | null = null
let verificationDeliveryTimer: number | null = null
let verificationDeliveryPollVersion = 0

const isRegister = computed(() => route.path === '/register')
const isForgotPassword = computed(() => route.path === '/forgot-password')
const isBinding = computed(() => route.path === '/bind-email')
const isLogin = computed(() => !isRegister.value && !isForgotPassword.value && !isBinding.value)
const needsEmail = computed(() => !isLogin.value)
const needsCurrentPassword = computed(() => isLogin.value || isRegister.value)
const needsVerificationCode = computed(() => isRegister.value || isForgotPassword.value || isBinding.value)
const turnstileEnabled = computed(() => cfg.value.turnstile_enabled && Boolean(cfg.value.turnstile_site_key))
const turnstileAction = computed(() => {
  if (isRegister.value) return 'register'
  if (isForgotPassword.value) return 'password_reset'
  if (isBinding.value) return 'bind_email'
  return 'login'
})
const needsTurnstileForSubmit = computed(() => isLogin.value)
const pageCopy = computed(() => {
  if (isRegister.value) return { title: '创建账户', description: '验证邮箱后即可完成注册', submit: '创建账户' }
  if (isForgotPassword.value) return { title: '重置密码', description: '验证邮箱后设置新的登录密码', submit: '重置密码' }
  if (isBinding.value) return { title: '绑定邮箱', description: '完成验证后继续使用此账户', submit: '完成绑定' }
  return { title: '欢迎回来', description: '使用邮箱登录，旧账号可继续使用用户名', submit: '登录' }
})
const rules = computed(() => {
  const fieldRules: Record<string, unknown[]> = {}
  if (isLogin.value) {
    fieldRules.identifier = [{ required: true, message: '请输入邮箱或用户名', trigger: 'blur' }]
    fieldRules.password = [{ required: true, message: '请输入密码', trigger: 'blur' }]
    return fieldRules
  }

  fieldRules.email = [{ required: true, message: '请输入邮箱', trigger: 'blur' }]
  if (isRegister.value) {
    fieldRules.password = [{ required: true, message: '请输入密码', trigger: 'blur' }]
  }
  if (isForgotPassword.value) {
    fieldRules.new_password = [{ required: true, message: '请输入新密码', trigger: 'blur' }]
  }
  fieldRules.verification_code = [{ required: true, message: '请输入邮箱验证码', trigger: 'blur' }]
  if (isRegister.value && !cfg.value.billing_enabled) {
    fieldRules.chatgpt_token = [{ required: true, message: '请输入上游账号令牌', trigger: 'blur' }]
  }
  return fieldRules
})

const loadTurnstileScript = () => {
  if (window.turnstile) return Promise.resolve()
  if (turnstileScriptPromise) return turnstileScriptPromise

  turnstileScriptPromise = new Promise((resolve, reject) => {
    const existingScript = document.querySelector<HTMLScriptElement>('script[data-turnstile-script]')
    if (existingScript) {
      existingScript.addEventListener('load', () => resolve(), { once: true })
      existingScript.addEventListener('error', () => reject(new Error('load failed')), { once: true })
      return
    }
    const script = document.createElement('script')
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
    script.async = true
    script.defer = true
    script.dataset.turnstileScript = 'true'
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('load failed'))
    document.head.appendChild(script)
  })
  return turnstileScriptPromise
}

const removeTurnstile = () => {
  if (turnstileWidgetId.value && window.turnstile) window.turnstile.remove(turnstileWidgetId.value)
  turnstileWidgetId.value = null
  turnstileToken.value = ''
}

const resetTurnstile = () => {
  turnstileToken.value = ''
  if (turnstileWidgetId.value && window.turnstile) window.turnstile.reset(turnstileWidgetId.value)
}

const renderTurnstile = async () => {
  if (!turnstileEnabled.value) return
  turnstileError.value = ''
  try {
    await loadTurnstileScript()
    await nextTick()
    if (!window.turnstile || !turnstileContainer.value) return
    removeTurnstile()
    turnstileWidgetId.value = window.turnstile.render(turnstileContainer.value, {
      sitekey: cfg.value.turnstile_site_key,
      action: turnstileAction.value,
      theme: 'light',
      size: 'flexible',
      appearance: 'always',
      callback: (token: string) => {
        turnstileToken.value = token
        turnstileError.value = ''
      },
      'expired-callback': () => {
        turnstileToken.value = ''
        turnstileError.value = '验证已过期，请重新验证'
      },
      'error-callback': () => {
        turnstileToken.value = ''
        turnstileError.value = '人机验证加载失败，请刷新页面'
      }
    })
  } catch {
    turnstileError.value = '人机验证加载失败，请刷新页面'
  }
}

const startCooldown = () => {
  if (cooldownTimer) window.clearInterval(cooldownTimer)
  cooldown.value = 60
  cooldownTimer = window.setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0 && cooldownTimer) {
      window.clearInterval(cooldownTimer)
      cooldownTimer = null
    }
  }, 1000)
}

const emailLooksPresent = () => form.email.trim().includes('@')

const watchVerificationDelivery = (challengeId: string) => {
  verificationDeliveryPollVersion += 1
  const pollVersion = verificationDeliveryPollVersion
  const poll = async () => {
    const data = await request(`/0x/user/email-verifications/${challengeId}/status`)
    if (pollVersion !== verificationDeliveryPollVersion || !data) return
    if (data.delivery_status === 'SENT') {
      verificationDeliveryMessage.value = '验证码已发送，请查收邮箱'
      return
    }
    if (data.delivery_status === 'FAILED') {
      verificationDeliveryMessage.value = '验证码发送失败，请稍后重试'
      return
    }
    verificationDeliveryMessage.value = '验证码正在发送，请稍候'
    verificationDeliveryTimer = window.setTimeout(poll, 1200)
  }
  void poll()
}

const requestVerificationCode = async () => {
  if (!emailLooksPresent()) {
    MessagePlugin.warning('请先输入有效邮箱')
    return
  }
  if (turnstileEnabled.value && !turnstileToken.value) {
    MessagePlugin.warning('请完成人机验证')
    return
  }

  const url = isRegister.value
    ? '/0x/user/email-verifications/request'
    : isForgotPassword.value
      ? '/0x/user/password-reset/request'
      : '/0x/user/email-binding/request'
  const payload: Record<string, string> = {
    email: form.email.trim(),
    turnstile_token: turnstileToken.value
  }
  if (isBinding.value) payload.binding_ticket = sessionStorage.getItem(BINDING_TICKET_STORAGE_KEY) || ''

  verificationSending.value = true
  const data = await request(url, 'POST', payload)
  verificationSending.value = false
  if (data) {
    verificationDeliveryMessage.value = data.challenge_id ? '验证码正在发送，请稍候' : '验证码正在发送，请查收邮箱'
    MessagePlugin.success(data.message || '验证码已进入发送队列')
    startCooldown()
    resetTurnstile()
    if (data.challenge_id) watchVerificationDelivery(data.challenge_id)
  }
}

const goAfterAuthentication = async () => {
  if (userStore.isAdmin) {
    await router.push({ name: 'User' })
    return
  }
  let billing: any = null
  try {
    const response = await fetch('/0x/billing/me', { credentials: 'include' })
    if (response.ok) billing = await response.json()
  } catch {
    billing = null
  }
  if (billing?.enabled && !billing.service_available && (billing.enforced || billing.subscription || isRegister.value)) {
    await router.push({ name: 'Billing' })
    return
  }
  await router.push({ name: 'LoginChatgpt' })
}

const onSubmit = async ({ validateResult }: any) => {
  if (validateResult !== true) return
  if (turnstileEnabled.value && needsTurnstileForSubmit.value && !turnstileToken.value) {
    MessagePlugin.warning('请完成人机验证')
    return
  }

  loading.value = true
  try {
    if (isLogin.value) {
      const data = await userStore.login('/0x/user/login', {
        identifier: form.identifier.trim(),
        password: form.password,
        turnstile_token: turnstileToken.value
      })
      if (data.email_binding_required && data.binding_ticket) {
        sessionStorage.setItem(BINDING_TICKET_STORAGE_KEY, data.binding_ticket)
        await router.replace('/bind-email')
        return
      }
      if (data.authenticated) await goAfterAuthentication()
      return
    }

    if (isRegister.value) {
      const data = await userStore.login('/0x/user/register', {
        email: form.email.trim(),
        password: form.password,
        verification_code: form.verification_code.trim(),
        ...(cfg.value.billing_enabled ? {} : { chatgpt_token: form.chatgpt_token })
      })
      if (data.authenticated) await goAfterAuthentication()
      return
    }

    if (isForgotPassword.value) {
      const data = await request('/0x/user/password-reset/confirm', 'POST', {
        email: form.email.trim(),
        verification_code: form.verification_code.trim(),
        new_password: form.new_password
      })
      if (data) {
        MessagePlugin.success(data.message || '密码已重置')
        await router.replace('/login')
      }
      return
    }

    const bindingTicket = sessionStorage.getItem(BINDING_TICKET_STORAGE_KEY) || ''
    if (!bindingTicket) {
      MessagePlugin.warning('邮箱绑定会话已过期，请重新登录')
      await router.replace('/login')
      return
    }
    const data = await userStore.login('/0x/user/email-binding/confirm', {
      binding_ticket: bindingTicket,
      verification_code: form.verification_code.trim()
    })
    if (data.authenticated) {
      sessionStorage.removeItem(BINDING_TICKET_STORAGE_KEY)
      await goAfterAuthentication()
    }
  } catch (error: any) {
    MessagePlugin.error(error.message || '操作失败')
    resetTurnstile()
  } finally {
    loading.value = false
  }
}

const goFree = async () => {
  if (turnstileEnabled.value && !turnstileToken.value) {
    MessagePlugin.warning('请完成人机验证')
    return
  }
  loading.value = true
  try {
    const data = await userStore.login('/0x/user/login-free', { turnstile_token: turnstileToken.value })
    if (data.authenticated) await router.push({ name: 'LoginChatgpt' })
  } catch (error: any) {
    MessagePlugin.error(error.message || '免费体验暂不可用')
    resetTurnstile()
  } finally {
    loading.value = false
  }
}

const getVersionCfg = async () => {
  try {
    const response = await fetch('/0x/user/version-cfg', { credentials: 'include' })
    const data = await response.json()
    Object.assign(cfg.value, data)
    if (isRegister.value && !cfg.value.allow_register) await router.replace('/login')
  } catch {
    MessagePlugin.error('无法读取站点配置，请稍后重试')
  }
}

onMounted(async () => {
  if (route.query.logout === '1') userStore.logout()
  if (isBinding.value && !sessionStorage.getItem(BINDING_TICKET_STORAGE_KEY)) {
    await router.replace('/login')
    return
  }
  await getVersionCfg()
  await renderTurnstile()
})

watch(turnstileAction, async () => {
  if (!turnstileEnabled.value) return
  removeTurnstile()
  await renderTurnstile()
})

onBeforeUnmount(() => {
  removeTurnstile()
  if (cooldownTimer) window.clearInterval(cooldownTimer)
  verificationDeliveryPollVersion += 1
  if (verificationDeliveryTimer) window.clearTimeout(verificationDeliveryTimer)
})
</script>

<style scoped>
.login-page {
  --login-bg: #f7f7f5;
  --login-surface: #ffffff;
  --login-text: #20201e;
  --login-muted: #6f6f6b;
  --login-border: #d9d9d5;
  --login-border-hover: #aaa9a3;
  --login-action: #252523;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  min-height: 100dvh;
  padding: 48px 24px;
  color: var(--login-text);
  background: var(--login-bg);
}
.login-panel { width: 100%; max-width: 400px; }
.login-notice { margin-bottom: 24px; padding: 12px 14px; color: #4f4f4b; font-size: 14px; line-height: 1.5; background: #efefec; border: 1px solid #e2e2de; border-radius: 8px; }
.login-header { margin-bottom: 32px; text-align: center; }
.login-header h1 { margin: 0; font-size: 30px; font-weight: 600; line-height: 1.25; letter-spacing: 0; }
.login-header p { margin: 10px 0 0; color: var(--login-muted); font-size: 15px; line-height: 1.6; }
.login-loading { display: block; width: 100%; }
.form-field { margin-bottom: 20px; }
.verification-status { margin: 8px 0 0; color: var(--login-muted); font-size: 12px; line-height: 1.5; }
.form-field label { display: inline-block; margin-bottom: 8px; color: #373735; font-size: 14px; font-weight: 500; line-height: 20px; }
.form-field :deep(.t-form__item), .form-field :deep(.t-form__controls-content), .submit-item :deep(.t-form__controls-content) { display: block; margin-bottom: 0; }
.form-field :deep(.t-input), .form-field :deep(.t-textarea) { color: var(--login-text); background: var(--login-surface); border-color: var(--login-border); border-radius: 8px; box-shadow: none; }
.form-field :deep(.t-input) { min-height: 50px; padding: 0 14px; }
.form-field :deep(.t-textarea) { padding: 12px 14px; }
.form-field :deep(.t-input:hover), .form-field :deep(.t-textarea:hover) { border-color: var(--login-border-hover); }
.form-field :deep(.t-input--focused), .form-field :deep(.t-textarea--focused) { border-color: var(--login-action); box-shadow: 0 0 0 1px var(--login-action); }
.form-field :deep(.t-input__inner), .form-field :deep(.t-textarea__inner) { color: var(--login-text); font-size: 15px; }
.form-field :deep(.t-form__status) { margin-top: 7px; font-size: 13px; }
.verification-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; }
.verification-button { min-width: 112px; height: 50px; border-radius: 8px; }
.turnstile-field { min-height: 65px; margin-top: 4px; }
.turnstile-widget { width: 100%; min-height: 65px; }
.turnstile-error { margin: 8px 0 0; color: #a3413a; font-size: 13px; line-height: 1.5; }
.submit-item { margin: 28px 0 0; }
.login-button { width: 100%; height: 50px; color: #f9f9f7; font-size: 15px; font-weight: 600; background: var(--login-action); border-color: var(--login-action); border-radius: 8px; box-shadow: none; }
.login-button:hover { background: #3a3a37; border-color: #3a3a37; }
.account-switch { margin: 22px 0 0; color: var(--login-muted); font-size: 14px; line-height: 22px; text-align: center; }
.account-switch a { color: var(--login-text); font-weight: 600; text-decoration: underline; text-decoration-color: #b8b8b3; text-underline-offset: 3px; }
.switch-separator { padding: 0 10px; color: #b0afa9; }
.login-divider { display: flex; align-items: center; gap: 12px; margin: 24px 0; color: #8a8a85; font-size: 13px; }
.login-divider::before, .login-divider::after { flex: 1; height: 1px; background: #dfdfdb; content: ''; }
.free-button { width: 100%; height: 50px; padding: 0 16px; color: var(--login-text); font: inherit; font-size: 15px; font-weight: 500; cursor: pointer; background: var(--login-surface); border: 1px solid var(--login-border); border-radius: 8px; }
.free-button:hover:not(:disabled) { background: #efefec; border-color: var(--login-border-hover); }
.free-button:disabled { cursor: not-allowed; opacity: 0.5; }
@media (max-width: 520px) { .login-page { align-items: flex-start; padding: 72px 24px 40px; } .login-header { margin-bottom: 28px; } .login-header h1 { font-size: 28px; } .verification-row { grid-template-columns: 1fr; } .verification-button { width: 100%; } }
@media (prefers-reduced-motion: reduce) { .login-page *, .login-page *::before, .login-page *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; } }
</style>
