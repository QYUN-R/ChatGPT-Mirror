<template>
  <div class="profile-grid">
    <t-card title="邮箱与安全" subtitle="已验证邮箱用于登录、找回密码和站内邮件通知" :bordered="false">
      <t-loading :loading="profileLoading">
        <div class="email-status">
          <div>
            <p class="email-address">{{ profile.email || '尚未绑定邮箱' }}</p>
            <p class="email-copy">{{ profile.email_verified ? '邮箱已验证' : '邮箱尚未验证' }}</p>
          </div>
          <t-tag :theme="profile.email_verified ? 'success' : 'warning'" variant="light">
            {{ profile.email_verified ? '已验证' : '待验证' }}
          </t-tag>
        </div>
        <t-button v-if="!profile.email_verified" theme="primary" variant="outline" @click="startEmailBinding">绑定邮箱</t-button>
        <t-button v-else theme="primary" variant="outline" @click="openEmailChange">修改绑定邮箱</t-button>
      </t-loading>
    </t-card>
    <t-card title="用量配额" subtitle="配额为 0 表示不限制" :bordered="false">
      <t-loading :loading="loading">
        <div v-for="item in quotaItems" :key="item.label" class="quota-row">
          <div class="quota-head">
            <span>{{ item.label }}</span>
            <span>{{ item.used }} / {{ item.limit || '不限' }}</span>
          </div>
          <t-progress :percentage="item.limit ? Math.min(100, item.used / item.limit * 100) : 0" :label="false" />
        </div>
      </t-loading>
    </t-card>
    <t-card title="修改密码" subtitle="修改后会撤销其他设备上的旧登录 Token" :bordered="false">
      <t-form :data="form" label-width="100px" @submit="changePassword">
        <t-form-item label="当前密码" name="current_password">
          <t-input v-model="form.current_password" type="password" autocomplete="current-password" />
        </t-form-item>
        <t-form-item label="新密码" name="new_password">
          <t-input v-model="form.new_password" type="password" autocomplete="new-password" />
        </t-form-item>
        <t-form-item>
          <t-button theme="primary" type="submit" :loading="saving">保存新密码</t-button>
        </t-form-item>
      </t-form>
    </t-card>
    <t-card title="登录设备" subtitle="同一浏览器保持为同一设备，换浏览器或清理 Cookie 会识别为新设备" :bordered="false">
      <t-loading :loading="devicesLoading">
        <div class="device-policy-summary">
          <div>
            <strong>{{ devicePolicy.enabled ? `允许 ${devicePolicy.limit} 台设备` : '仅允许 1 台设备' }}</strong>
            <span>{{ devicePolicySource }}</span>
          </div>
          <t-tag variant="light">{{ devices.length }} 台在线</t-tag>
        </div>
        <div class="profile-device-list">
          <article v-for="device in devices" :key="device.id" class="profile-device-row">
            <div>
              <strong>{{ deviceLabel(device) }}</strong>
              <span>{{ device.ip_address || '未知 IP' }} · 最近活跃 {{ formatDateTime(device.last_seen_at) }}</span>
            </div>
            <t-space size="small">
              <t-tag v-if="device.is_current" theme="success" size="small" variant="light">当前设备</t-tag>
              <t-popconfirm content="确定让这台设备退出登录吗？" @confirm="revokeDevice(device)">
                <t-button theme="danger" variant="text" size="small">下线</t-button>
              </t-popconfirm>
            </t-space>
          </article>
          <div v-if="!devicesLoading && !devices.length" class="device-empty">暂无在线设备</div>
        </div>
      </t-loading>
    </t-card>

    <t-dialog
      :visible="emailChangeVisible"
      header="修改绑定邮箱"
      :confirm-btn="{ loading: emailChangeSubmitting, content: '确认修改' }"
      @confirm="confirmEmailChange"
      @close="emailChangeVisible = false"
      width="460px"
    >
      <t-form :data="emailChange" label-width="88px">
        <t-form-item label="新邮箱">
          <t-input v-model="emailChange.email" type="email" placeholder="QQ、网易或 Google 邮箱" autocomplete="email" />
        </t-form-item>
        <t-form-item label="当前密码">
          <t-input v-model="emailChange.current_password" type="password" autocomplete="current-password" />
        </t-form-item>
        <t-form-item label="验证码">
          <div class="verification-row">
            <t-input v-model="emailChange.verification_code" inputmode="numeric" maxlength="6" autocomplete="one-time-code" />
            <t-button
              variant="outline"
              :loading="emailChangeSending"
              :disabled="emailChangeSending || emailChangeCooldown > 0"
              @click="requestEmailChangeCode"
            >
              {{ emailChangeCooldown > 0 ? `${emailChangeCooldown}s 后重发` : '发送验证码' }}
            </t-button>
          </div>
          <template #help>
            <span class="form-help">{{ emailChangeDelivery || '验证码会发送到新邮箱，确认后其他设备会退出登录' }}</span>
          </template>
        </t-form-item>
      </t-form>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { useUserStore } from '@/store/user'

const quota = ref<any>({ daily: { limit: 0, used: 0 }, monthly: { limit: 0, used: 0 } })
const loading = ref(false)
const saving = ref(false)
const profileLoading = ref(false)
const emailChangeVisible = ref(false)
const emailChangeSending = ref(false)
const emailChangeSubmitting = ref(false)
const emailChangeCooldown = ref(0)
const emailChangeDelivery = ref('')
const router = useRouter()
const userStore = useUserStore()
const profile = ref({ email: '', email_verified: false, email_binding_ticket: '' })
const form = reactive({ current_password: '', new_password: '' })
const emailChange = reactive({ email: '', current_password: '', verification_code: '' })
const devicesLoading = ref(false)
const devices = ref<any[]>([])
const devicePolicy = ref<any>({ enabled: true, limit: 3, source: 'system', plan_name: '' })
let emailCooldownTimer: number | null = null
let emailDeliveryTimer: number | null = null
let emailDeliveryPollVersion = 0
const quotaItems = computed(() => [
  { label: '今日用量', ...quota.value.daily },
  { label: '本月用量', ...quota.value.monthly }
])
const devicePolicySource = computed(() => {
  if (devicePolicy.value.source === 'plan') return devicePolicy.value.plan_name ? `跟随 ${devicePolicy.value.plan_name}` : '跟随套餐'
  if (devicePolicy.value.source === 'user') return '管理员单独设置'
  return '系统默认'
})

const loadQuota = async () => {
  loading.value = true
  quota.value = (await request('/0x/user/quota')) || quota.value
  loading.value = false
}

const loadProfile = async () => {
  profileLoading.value = true
  const data = await request('/0x/user/me')
  if (data) {
    profile.value = {
      email: data.email || '',
      email_verified: Boolean(data.email_verified),
      email_binding_ticket: data.email_binding_ticket || ''
    }
  }
  profileLoading.value = false
}

const loadDevices = async () => {
  devicesLoading.value = true
  const data = await request('/0x/user/devices')
  devicesLoading.value = false
  if (!data) return
  devices.value = data.sessions || []
  devicePolicy.value = data.policy || devicePolicy.value
}

const deviceLabel = (device: any) => {
  const type = device.device_type === 'mobile' ? '手机' : device.device_type === 'tablet' ? '平板' : '电脑'
  return `${type} · ${device.browser_name || '其他浏览器'} · ${device.os_name || '其他系统'}`
}

const formatDateTime = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'

const revokeDevice = async (device: any) => {
  const data = await request('/0x/user/devices', 'DELETE', { session_id: device.id })
  if (!data) return
  if (data.current_device_revoked) {
    await userStore.logout()
    await router.replace('/login')
    return
  }
  MessagePlugin.success(data.message)
  await loadDevices()
}

const startEmailBinding = async () => {
  if (!profile.value.email_binding_ticket) {
    MessagePlugin.warning('邮箱绑定会话已过期，请重新登录')
    return
  }
  sessionStorage.setItem('chat2.email-binding-ticket', profile.value.email_binding_ticket)
  await router.push('/bind-email')
}

const clearEmailCooldown = () => {
  if (emailCooldownTimer) window.clearInterval(emailCooldownTimer)
  emailCooldownTimer = null
  emailChangeCooldown.value = 0
}

const startEmailCooldown = () => {
  clearEmailCooldown()
  emailChangeCooldown.value = 60
  emailCooldownTimer = window.setInterval(() => {
    emailChangeCooldown.value -= 1
    if (emailChangeCooldown.value <= 0) clearEmailCooldown()
  }, 1000)
}

const watchEmailDelivery = (challengeId: string) => {
  emailDeliveryPollVersion += 1
  const pollVersion = emailDeliveryPollVersion
  const poll = async () => {
    const data = await request(`/0x/user/email-verifications/${challengeId}/status`)
    if (pollVersion !== emailDeliveryPollVersion || !data) return
    if (data.delivery_status === 'SENT') {
      emailChangeDelivery.value = '验证码已发送，请查收新邮箱'
      return
    }
    if (data.delivery_status === 'FAILED') {
      emailChangeDelivery.value = '验证码发送失败，请稍后重试'
      return
    }
    emailChangeDelivery.value = '验证码正在发送，请稍候'
    emailDeliveryTimer = window.setTimeout(poll, 1200)
  }
  void poll()
}

const openEmailChange = () => {
  emailChange.email = ''
  emailChange.current_password = ''
  emailChange.verification_code = ''
  emailChangeDelivery.value = ''
  emailChangeVisible.value = true
}

const requestEmailChangeCode = async () => {
  if (!emailChange.email.trim() || !emailChange.current_password) {
    MessagePlugin.warning('请先填写新邮箱和当前密码')
    return
  }
  emailChangeSending.value = true
  const data = await request('/0x/user/email-change/request', 'POST', {
    email: emailChange.email.trim(),
    current_password: emailChange.current_password
  })
  emailChangeSending.value = false
  if (!data) return
  emailChangeDelivery.value = '验证码正在发送，请稍候'
  startEmailCooldown()
  if (data.challenge_id) watchEmailDelivery(data.challenge_id)
}

const confirmEmailChange = async () => {
  if (!emailChange.verification_code.trim()) {
    MessagePlugin.warning('请输入 6 位验证码')
    return
  }
  emailChangeSubmitting.value = true
  const data = await request('/0x/user/email-change/confirm', 'POST', {
    verification_code: emailChange.verification_code.trim()
  })
  emailChangeSubmitting.value = false
  if (!data) return
  emailChangeVisible.value = false
  clearEmailCooldown()
  emailDeliveryPollVersion += 1
  if (emailDeliveryTimer) window.clearTimeout(emailDeliveryTimer)
  await loadProfile()
  MessagePlugin.success('绑定邮箱已更新，其他设备已退出登录')
}

const changePassword = async () => {
  if (!form.current_password || !form.new_password) {
    MessagePlugin.warning('请完整填写当前密码和新密码')
    return
  }
  saving.value = true
  const data = await request('/0x/user/change-password', 'POST', form)
  saving.value = false
  if (data) {
    form.current_password = ''
    form.new_password = ''
    MessagePlugin.success(data.message)
  }
}

onMounted(() => {
  loadQuota()
  loadProfile()
  loadDevices()
})

onBeforeUnmount(() => {
  clearEmailCooldown()
  emailDeliveryPollVersion += 1
  if (emailDeliveryTimer) window.clearTimeout(emailDeliveryTimer)
})
</script>

<style scoped>
.profile-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; }
.email-status { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.email-address { margin: 0; color: var(--app-text); font-size: 15px; font-weight: 600; word-break: break-word; }
.email-copy { margin: 6px 0 0; color: var(--app-muted); font-size: 13px; }
.quota-row + .quota-row { margin-top: 24px; }
.quota-head { display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--app-text); font-size: 14px; }
.verification-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; width: 100%; }
.device-policy-summary { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 14px; padding: 12px 14px; background: #f7f7f5; border: 1px solid var(--app-border); border-radius: 6px; }
.device-policy-summary strong, .device-policy-summary span, .profile-device-row strong, .profile-device-row span { display: block; }
.device-policy-summary span, .profile-device-row span { margin-top: 4px; color: var(--app-text-muted); font-size: 12px; }
.profile-device-list { display: grid; gap: 10px; }
.profile-device-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 13px 14px; border: 1px solid var(--app-border); border-radius: 6px; }
.device-empty { padding: 20px 8px; color: var(--app-text-muted); text-align: center; }
.form-help { color: var(--app-text-muted); font-size: 12px; line-height: 1.6; }
@media (max-width: 900px) { .profile-grid { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .profile-device-row { align-items: flex-start; flex-direction: column; } }
</style>
