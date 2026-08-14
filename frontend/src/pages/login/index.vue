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
          <div v-if="isLogin && !deviceVerificationRequired" class="form-field">
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
                  :disabled="loading || verificationSending || cooldown > 0 || (!deviceVerificationRequired && !humanVerificationReady)"
                  @click="requestVerificationCode"
                >
                  {{ verificationButtonLabel }}
                </t-button>
              </div>
              <p v-if="verificationDeliveryMessage" class="verification-status">{{ verificationDeliveryMessage }}</p>
              <p v-if="verificationRequested" class="verification-resend-status">
                {{ cooldown > 0 ? `未收到？${cooldown} 秒后可重新发送` : '未收到验证码？可以重新发送' }}
              </p>
              <p v-if="verificationRequested && isGoogleMailbox" class="verification-mailbox-hint">
                Gmail 用户请同时检查“垃圾邮件”和“所有邮件”。
              </p>
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

          <div v-if="!deviceVerificationRequired && turnstileEnabled" class="turnstile-field">
            <div ref="turnstileContainer" class="turnstile-widget"></div>
            <p v-if="turnstileError" class="turnstile-error" role="alert">{{ turnstileError }}</p>
          </div>

          <div v-else-if="!deviceVerificationRequired && localCaptchaEnabled" class="local-captcha-field">
            <label for="local-captcha-answer">图形验证码</label>
            <div class="local-captcha-row">
              <t-input
                id="local-captcha-answer"
                v-model="localCaptchaAnswer"
                autocomplete="off"
                autocapitalize="characters"
                maxlength="6"
                placeholder="输入 6 位字符"
                size="large"
              />
              <button
                type="button"
                class="local-captcha-image"
                :disabled="localCaptchaLoading"
                aria-label="刷新图形验证码"
                title="点击刷新"
                @click="loadLocalCaptcha"
              >
                <img v-if="localCaptchaImage" :src="localCaptchaImage" alt="图形验证码，点击刷新" />
                <span v-else>{{ localCaptchaLoading ? '加载中' : '点击刷新' }}</span>
              </button>
            </div>
            <p class="captcha-hint">看不清可点击图片刷新</p>
          </div>

          <t-form-item class="submit-item">
            <t-button
              type="submit"
              size="large"
              class="login-button"
              :disabled="submitDisabled"
            >
              {{ pageCopy.submit }}
            </t-button>
          </t-form-item>
        </t-form>
      </t-loading>

      <div class="account-switch">
        <template v-if="isLogin && !deviceVerificationRequired">
          <router-link to="/forgot-password">忘记密码</router-link>
          <span v-if="cfg.allow_register" class="switch-separator">|</span>
          <router-link v-if="cfg.allow_register" to="/register">创建账户</router-link>
        </template>
        <template v-else-if="deviceVerificationRequired">
          <button type="button" class="text-button" @click="resetDeviceVerification">返回账号登录</button>
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

      <template v-if="isLogin && !deviceVerificationRequired">
        <div class="login-divider" aria-hidden="true"><span>或</span></div>
        <button
          class="free-button"
          type="button"
          :disabled="loading"
          @click="goFree"
        >
          免费体验
        </button>
        <p class="free-hint">无需填写邮箱和密码，只需先完成图形验证码</p>
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
const VERIFICATION_COOLDOWN_STORAGE_PREFIX = 'tuwugpt.email-verification.cooldown'
const PUBLIC_AUTH_PATHS = new Set(['/login', '/register', '/forgot-password', '/bind-email'])
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const verificationSending = ref(false)
const cooldown = ref(0)
const verificationRequested = ref(false)
const verificationDeliveryMessage = ref('')
const cfg = ref({
  show_github: true,
  allow_register: false,
  billing_enabled: false,
  email_verification_enabled: false,
  email_verification_resend_seconds: 60,
  notice: '',
  turnstile_enabled: false,
  turnstile_site_key: '',
  local_captcha_enabled: false
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
const localCaptchaToken = ref('')
const localCaptchaImage = ref('')
const localCaptchaAnswer = ref('')
const localCaptchaLoading = ref(false)
const deviceVerificationRequired = ref(false)
const deviceTicket = ref('')
const deviceMaskedEmail = ref('')
let turnstileScriptPromise: Promise<void> | null = null
let cooldownTimer: number | null = null
let verificationDeliveryTimer: number | null = null
let verificationDeliveryPollVersion = 0

const postLoginRedirect = () => {
  const value = Array.isArray(route.query.redirect) ? route.query.redirect[0] : route.query.redirect
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) {
    return ''
  }
  const resolved = router.resolve(value)
  if (PUBLIC_AUTH_PATHS.has(resolved.path)) return ''
  return resolved.fullPath
}

const isRegister = computed(() => route.path === '/register')
const isForgotPassword = computed(() => route.path === '/forgot-password')
const isBinding = computed(() => route.path === '/bind-email')
const isLogin = computed(() => !isRegister.value && !isForgotPassword.value && !isBinding.value)
const needsEmail = computed(() => !isLogin.value)
const needsCurrentPassword = computed(() => (isLogin.value && !deviceVerificationRequired.value) || isRegister.value)
const needsVerificationCode = computed(() => deviceVerificationRequired.value || isRegister.value || isForgotPassword.value || isBinding.value)
const turnstileEnabled = computed(() => cfg.value.turnstile_enabled && Boolean(cfg.value.turnstile_site_key))
const localCaptchaEnabled = computed(() => cfg.value.local_captcha_enabled)
const turnstileAction = computed(() => {
  if (isRegister.value) return 'register'
  if (isForgotPassword.value) return 'password_reset'
  if (isBinding.value) return 'bind_email'
  return 'login'
})
const isGoogleMailbox = computed(() => {
  const domain = form.email.trim().toLowerCase().split('@').pop() || ''
  return domain === 'gmail.com' || domain === 'googlemail.com'
})
const verificationButtonLabel = computed(() => {
  if (cooldown.value > 0) return `${cooldown.value}s 后重发`
  return verificationRequested.value ? '重新发送验证码' : '发送验证码'
})
const needsTurnstileForSubmit = computed(() => isLogin.value && !deviceVerificationRequired.value)
const humanVerificationReady = computed(() => {
  if (localCaptchaEnabled.value) {
    return Boolean(localCaptchaToken.value && localCaptchaAnswer.value.trim().length === 6)
  }
  if (turnstileEnabled.value) return Boolean(turnstileToken.value)
  return true
})
const pageCopy = computed(() => {
  if (deviceVerificationRequired.value) return { title: '验证新设备', description: `验证码将发送至 ${deviceMaskedEmail.value || '已绑定邮箱'}`, submit: '验证并登录' }
  if (isRegister.value) return { title: '创建账户', description: '验证邮箱后即可完成注册', submit: '创建账户' }
  if (isForgotPassword.value) return { title: '重置密码', description: '验证邮箱后设置新的登录密码', submit: '重置密码' }
  if (isBinding.value) return { title: '绑定邮箱', description: '完成验证后继续使用此账户', submit: '完成绑定' }
  return { title: '欢迎回来', description: '使用邮箱登录，旧账号可继续使用用户名', submit: '登录' }
})
const submitDisabled = computed(() => {
  if (deviceVerificationRequired.value) return false
  if (localCaptchaEnabled.value) return !humanVerificationReady.value
  return needsTurnstileForSubmit.value && !humanVerificationReady.value
})
const rules = computed(() => {
  const fieldRules: Record<string, unknown[]> = {}
  if (deviceVerificationRequired.value) {
    fieldRules.verification_code = [{ required: true, message: '请输入邮箱验证码', trigger: 'blur' }]
    return fieldRules
  }
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

const loadLocalCaptcha = async () => {
  if (!localCaptchaEnabled.value) return
  localCaptchaLoading.value = true
  localCaptchaAnswer.value = ''
  try {
    const response = await fetch(`/0x/user/captcha?action=${encodeURIComponent(turnstileAction.value)}`, {
      credentials: 'include',
      cache: 'no-store'
    })
    if (!response.ok) throw new Error('captcha unavailable')
    const contentType = response.headers.get('content-type') || ''
    const text = await response.text()
    if (!contentType.includes('application/json')) throw new Error('captcha unavailable')
    const data = JSON.parse(text)
    localCaptchaToken.value = data.captcha_token || ''
    localCaptchaImage.value = data.image_data_url || ''
  } catch {
    localCaptchaToken.value = ''
    localCaptchaImage.value = ''
    MessagePlugin.error('图形验证码加载失败，请点击重试')
  } finally {
    localCaptchaLoading.value = false
  }
}

const humanVerificationPayload = () => ({
  turnstile_token: turnstileToken.value,
  captcha_token: localCaptchaToken.value,
  captcha_answer: localCaptchaAnswer.value.trim().toUpperCase()
})

const resetHumanVerification = async () => {
  if (localCaptchaEnabled.value) {
    await loadLocalCaptcha()
    return
  }
  resetTurnstile()
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

const stopCooldownTimer = () => {
  if (cooldownTimer) window.clearInterval(cooldownTimer)
  cooldownTimer = null
}

const verificationCooldownKey = () => {
  const identity = deviceVerificationRequired.value
    ? `device:${deviceTicket.value.slice(-24)}`
    : `${turnstileAction.value}:${form.email.trim().toLowerCase()}`
  return `${VERIFICATION_COOLDOWN_STORAGE_PREFIX}:${identity}`
}

const runCooldown = (deadline: number) => {
  stopCooldownTimer()
  const update = () => {
    cooldown.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
    if (cooldown.value <= 0) stopCooldownTimer()
  }
  update()
  if (cooldown.value <= 0) return
  cooldownTimer = window.setInterval(() => {
    update()
  }, 1000)
}

const startCooldown = (seconds = Number(cfg.value.email_verification_resend_seconds || 60)) => {
  const safeSeconds = Math.max(1, Math.min(Number(seconds || 60), 3600))
  const deadline = Date.now() + safeSeconds * 1000
  verificationRequested.value = true
  sessionStorage.setItem(verificationCooldownKey(), String(deadline))
  runCooldown(deadline)
}

const restoreCooldown = () => {
  stopCooldownTimer()
  cooldown.value = 0
  verificationRequested.value = false
  const key = verificationCooldownKey()
  const deadline = Number(sessionStorage.getItem(key) || 0)
  if (!Number.isFinite(deadline) || deadline <= Date.now()) {
    sessionStorage.removeItem(key)
    return
  }
  verificationRequested.value = true
  runCooldown(deadline)
}

const clearCooldown = () => {
  sessionStorage.removeItem(verificationCooldownKey())
  stopCooldownTimer()
  cooldown.value = 0
  verificationRequested.value = false
}

const emailLooksPresent = () => form.email.trim().includes('@')

const watchVerificationDelivery = (challengeId: string) => {
  verificationDeliveryPollVersion += 1
  const pollVersion = verificationDeliveryPollVersion
  const poll = async () => {
    const data = await request(`/0x/user/email-verifications/${challengeId}/status`)
    if (pollVersion !== verificationDeliveryPollVersion || !data) return
    if (data.delivery_status === 'SENT') {
      verificationDeliveryMessage.value = isGoogleMailbox.value
        ? '验证码已发送，请查收 Gmail；若收件箱没有，请检查垃圾邮件或所有邮件'
        : '验证码已发送，请查收邮箱'
      return
    }
    if (data.delivery_status === 'FAILED') {
      verificationDeliveryMessage.value = '验证码发送失败，倒计时结束后可重新发送'
      return
    }
    verificationDeliveryMessage.value = '验证码正在发送，请稍候'
    verificationDeliveryTimer = window.setTimeout(poll, 1200)
  }
  void poll()
}

const requestVerificationCode = async () => {
  if (!deviceVerificationRequired.value && !emailLooksPresent()) {
    MessagePlugin.warning('请先输入有效邮箱')
    return
  }
  if (!deviceVerificationRequired.value && !humanVerificationReady.value) {
    MessagePlugin.warning('请完成人机验证')
    return
  }

  const url = deviceVerificationRequired.value
    ? '/0x/user/device-login/request'
    : isRegister.value
    ? '/0x/user/email-verifications/request'
    : isForgotPassword.value
      ? '/0x/user/password-reset/request'
      : '/0x/user/email-binding/request'
  const payload: Record<string, string> = deviceVerificationRequired.value
    ? { device_ticket: deviceTicket.value }
    : { email: form.email.trim(), ...humanVerificationPayload() }
  if (isBinding.value) payload.binding_ticket = sessionStorage.getItem(BINDING_TICKET_STORAGE_KEY) || ''

  verificationSending.value = true
  const data = await request(url, 'POST', payload)
  verificationSending.value = false
  if (data) {
    verificationDeliveryMessage.value = data.challenge_id ? '验证码正在发送，请稍候' : '验证码正在发送，请查收邮箱'
    MessagePlugin.success(data.message || '验证码已进入发送队列')
    startCooldown()
    if (!deviceVerificationRequired.value) await resetHumanVerification()
    if (data.challenge_id) watchVerificationDelivery(data.challenge_id)
  }
}

const goAfterAuthentication = async () => {
  const redirect = postLoginRedirect()
  if (redirect) {
    await router.replace(redirect)
    return
  }
  if (userStore.isAdmin) {
    await router.push({ name: 'User' })
    return
  }
  await router.push({ name: 'Billing' })
}

const onSubmit = async ({ validateResult }: any) => {
  if (validateResult !== true) return
  if (submitDisabled.value) {
    MessagePlugin.warning('请完成人机验证')
    return
  }

  loading.value = true
  try {
    if (deviceVerificationRequired.value) {
      const data = await userStore.login('/0x/user/device-login/confirm', {
        device_ticket: deviceTicket.value,
        verification_code: form.verification_code.trim()
      })
      if (data.authenticated) {
        resetDeviceVerification()
        await goAfterAuthentication()
      }
      return
    }

    if (isLogin.value) {
      const data = await userStore.login('/0x/user/login', {
        identifier: form.identifier.trim(),
        password: form.password,
        ...humanVerificationPayload()
      })
      if (data.email_binding_required && data.binding_ticket) {
        sessionStorage.setItem(BINDING_TICKET_STORAGE_KEY, data.binding_ticket)
        const redirect = postLoginRedirect()
        await router.replace({ path: '/bind-email', query: redirect ? { redirect } : undefined })
        return
      }
      if (data.device_verification_required && data.device_ticket) {
        deviceVerificationRequired.value = true
        deviceTicket.value = data.device_ticket
        deviceMaskedEmail.value = data.masked_email || ''
        form.verification_code = ''
        verificationDeliveryMessage.value = ''
        await requestVerificationCode()
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
    if (!deviceVerificationRequired.value) await resetHumanVerification()
  } finally {
    loading.value = false
  }
}

const resetDeviceVerification = () => {
  clearCooldown()
  deviceVerificationRequired.value = false
  deviceTicket.value = ''
  deviceMaskedEmail.value = ''
  form.verification_code = ''
  verificationDeliveryMessage.value = ''
}

const goFree = async () => {
  if (!humanVerificationReady.value) {
    MessagePlugin.warning('免费体验只需填写图形验证码')
    const field = document.getElementById('local-captcha-answer')
    const input = field?.querySelector<HTMLInputElement>('input') || null
    field?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => input?.focus(), 250)
    return
  }
  loading.value = true
  try {
    const data = await userStore.login('/0x/user/login-free', humanVerificationPayload())
    if (data.authenticated) await router.push({ name: 'LoginChatgpt' })
  } catch (error: any) {
    MessagePlugin.error(error.message || '免费体验暂不可用')
    await resetHumanVerification()
  } finally {
    loading.value = false
  }
}

const getVersionCfg = async () => {
  try {
    const response = await fetch('/0x/user/version-cfg', { credentials: 'include' })
    if (!response.ok) throw new Error('version unavailable')
    const contentType = response.headers.get('content-type') || ''
    const text = await response.text()
    if (!contentType.includes('application/json')) throw new Error('version unavailable')
    const data = JSON.parse(text)
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
  restoreCooldown()
  if (localCaptchaEnabled.value) await loadLocalCaptcha()
  else await renderTurnstile()
})

watch(turnstileAction, async () => {
  if (localCaptchaEnabled.value) {
    await loadLocalCaptcha()
    return
  }
  if (!turnstileEnabled.value) return
  removeTurnstile()
  await renderTurnstile()
})

watch(
  () => [turnstileAction.value, form.email.trim().toLowerCase(), deviceTicket.value],
  () => {
    verificationDeliveryPollVersion += 1
    if (verificationDeliveryTimer) window.clearTimeout(verificationDeliveryTimer)
    verificationDeliveryMessage.value = ''
    restoreCooldown()
  },
)

onBeforeUnmount(() => {
  removeTurnstile()
  stopCooldownTimer()
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
.verification-resend-status { margin: 7px 0 0; color: var(--login-text); font-size: 12px; line-height: 1.5; }
.verification-mailbox-hint { margin: 5px 0 0; color: #8a6418; font-size: 12px; line-height: 1.5; }
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
.local-captcha-field { margin-top: 4px; }
.local-captcha-field > label { display: inline-block; margin-bottom: 8px; color: #373735; font-size: 14px; font-weight: 500; line-height: 20px; }
.local-captcha-row { display: grid; grid-template-columns: minmax(0, 1fr) 192px; gap: 10px; }
.local-captcha-row :deep(.t-input) { min-height: 56px; padding: 0 14px; background: var(--login-surface); border-color: var(--login-border); border-radius: 8px; }
.local-captcha-row :deep(.t-input__inner) { text-transform: uppercase; }
.local-captcha-image { display: grid; width: 192px; aspect-ratio: 3 / 1; min-height: 64px; height: auto; padding: 0; overflow: hidden; cursor: pointer; background: #f2f2ef; border: 1px solid var(--login-border); border-radius: 8px; place-items: center; }
.local-captcha-image:hover:not(:disabled) { border-color: var(--login-border-hover); }
.local-captcha-image:disabled { cursor: wait; opacity: 0.7; }
.local-captcha-image img { display: block; width: 100%; height: 100%; object-fit: contain; }
.local-captcha-image span { color: var(--login-muted); font-size: 13px; }
.captcha-hint { margin: 7px 0 0; color: var(--login-muted); font-size: 12px; line-height: 1.5; }
.submit-item { margin: 28px 0 0; }
.login-button { width: 100%; height: 50px; color: #f9f9f7; font-size: 15px; font-weight: 600; background: var(--login-action); border-color: var(--login-action); border-radius: 8px; box-shadow: none; }
.login-button:hover { background: #3a3a37; border-color: #3a3a37; }
.account-switch { margin: 22px 0 0; color: var(--login-muted); font-size: 14px; line-height: 22px; text-align: center; }
.account-switch a { color: var(--login-text); font-weight: 600; text-decoration: underline; text-decoration-color: #b8b8b3; text-underline-offset: 3px; }
.text-button { padding: 0; color: var(--login-text); font: inherit; font-weight: 600; cursor: pointer; background: transparent; border: 0; text-decoration: underline; text-decoration-color: #b8b8b3; text-underline-offset: 3px; }
.switch-separator { padding: 0 10px; color: #b0afa9; }
.login-divider { display: flex; align-items: center; gap: 12px; margin: 24px 0; color: #8a8a85; font-size: 13px; }
.login-divider::before, .login-divider::after { flex: 1; height: 1px; background: #dfdfdb; content: ''; }
.free-button { width: 100%; height: 50px; padding: 0 16px; color: var(--login-text); font: inherit; font-size: 15px; font-weight: 500; cursor: pointer; background: var(--login-surface); border: 1px solid var(--login-border); border-radius: 8px; }
.free-button:hover:not(:disabled) { background: #efefec; border-color: var(--login-border-hover); }
.free-button:disabled { cursor: not-allowed; opacity: 0.5; }
.free-hint { margin: 8px 0 0; color: var(--login-muted); font-size: 12px; line-height: 1.5; text-align: center; }
@media (max-width: 520px) { .login-page { align-items: flex-start; padding: 72px 24px 40px; } .login-header { margin-bottom: 28px; } .login-header h1 { font-size: 28px; } .verification-row { grid-template-columns: 1fr; } .verification-button { width: 100%; } .local-captcha-row { grid-template-columns: 1fr; } .local-captcha-image { width: 100%; min-height: 0; } }
@media (prefers-reduced-motion: reduce) { .login-page *, .login-page *::before, .login-page *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; } }
</style>
