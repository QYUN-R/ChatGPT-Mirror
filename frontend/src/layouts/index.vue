<template>
  <div class="layout">
    <t-layout class="layout-shell">
      <t-aside class="sidebar" :class="{ 'sidebar-open': mobileMenuOpen }" width="232px">
        <div class="sidebar-title">
          <div class="brand-lockup">
            <local-icon class="brand-icon" name="dashboard" />
            <div class="brand-copy">
              <strong>tuwugpt</strong>
              <span>{{ isAdmin ? '管理后台' : '用户中心' }}</span>
            </div>
          </div>
          <t-button class="mobile-close-button" variant="text" shape="square" aria-label="关闭导航" @click="mobileMenuOpen = false">
            <template #icon><local-icon name="close" /></template>
          </t-button>
        </div>
        <nav class="navigation" :aria-label="isAdmin ? '管理后台导航' : '用户中心导航'">
          <template v-if="isAdmin">
            <section v-for="section in adminNavigation" :key="section.label" class="nav-section">
              <p class="nav-section-label">{{ section.label }}</p>
              <t-menu class="nav-menu" :value="activeMenu" theme="light" @change="handleMenuChange">
                <t-menu-item v-for="item in section.items" :key="item.path" :value="item.path">
                  <template #icon><local-icon :name="item.icon" /></template>
                  <span class="menu-label">{{ item.label }}</span>
                </t-menu-item>
              </t-menu>
            </section>
          </template>
          <template v-else>
            <section v-for="section in memberNavigation" :key="section.label" class="nav-section">
              <p class="nav-section-label">{{ section.label }}</p>
              <t-menu class="nav-menu" :value="activeMenu" theme="light" @change="handleMenuChange">
                <t-menu-item v-for="item in section.items" :key="item.path" :value="item.path">
                  <template #icon><local-icon :name="item.icon" /></template>
                  <span class="menu-label">{{ item.label }}</span>
                </t-menu-item>
              </t-menu>
            </section>
          </template>
        </nav>
        <div class="sidebar-footer">
          <t-button class="profile-link" variant="text" block @click="router.push('/account/profile')">
            <template #icon><local-icon name="user-circle" /></template>
            <span>{{ isAdmin ? '账户设置' : '账户中心' }}</span>
          </t-button>
        </div>
      </t-aside>
      <button v-if="mobileMenuOpen" class="mobile-nav-backdrop" type="button" aria-label="关闭导航" @click="mobileMenuOpen = false" />
      <t-layout class="workspace">
        <t-header class="header">
          <div class="header-title">
            <t-button class="mobile-menu-button" variant="text" shape="square" aria-label="打开导航" @click="mobileMenuOpen = true">
              <template #icon><local-icon name="menu" /></template>
            </t-button>
            <h1>{{ pageTitle }}</h1>
          </div>
          <div class="header-right">
            <t-dropdown :options="userOptions" @click="handleUserAction">
              <t-button class="user-button" variant="text">
                <local-icon name="user-circle" />
                {{ username }}
                <local-icon name="chevron-down" />
              </t-button>
            </t-dropdown>
          </div>
        </t-header>
        <t-content class="content">
          <div class="content-inner">
            <router-view />
          </div>
        </t-content>
      </t-layout>
    </t-layout>

    <t-dialog
      :visible="Boolean(requiredNotification)"
      :header="requiredNotification?.title || '重要通知'"
      :close-btn="false"
      :close-on-esc-keydown="false"
      :close-on-overlay-click="false"
      :cancel-btn="null"
      :confirm-btn="{ content: '我已阅读', loading: acknowledgingNotification }"
      width="520px"
      @confirm="acknowledgeRequiredNotification"
    >
      <div v-if="requiredNotification" class="required-notice">
        <p>{{ requiredNotification.content }}</p>
        <time>{{ formatDateTime(requiredNotification.created_at) }}</time>
      </div>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/store/user'
import request from '@/api/request'
import { formatDateTime } from '@/utils/billing'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const activeMenu = computed(() => route.path)
const isAdmin = computed(() => userStore.isAdmin)
const username = computed(() => userStore.username || '管理员')
const pageTitle = computed(() => String(route.meta.title || (isAdmin.value ? '管理后台' : '用户中心')))
const requiredNotifications = ref<any[]>([])
const acknowledgingNotification = ref(false)
const requiredNotification = computed(() => requiredNotifications.value[0] || null)
const mobileMenuOpen = ref(false)

type NavigationItem = {
  label: string
  path: string
  icon: string
}

type NavigationSection = {
  label: string
  items: NavigationItem[]
}

const adminNavigation: NavigationSection[] = [
  {
    label: '运营',
    items: [
      { label: '运维概览', path: '/account/overview', icon: 'dashboard' },
      { label: '用户', path: '/account/user', icon: 'user' },
      { label: '上游账号', path: '/account/chatgpt', icon: 'root-list' },
      { label: '账号池', path: '/account/gptcar', icon: 'server' }
    ]
  },
  {
    label: '商业管理',
    items: [
      { label: '套餐配置', path: '/account/plans', icon: 'money-circle' },
      { label: '商业号池', path: '/account/pools', icon: 'layers' },
      { label: '用户订阅', path: '/account/subscriptions', icon: 'usergroup' },
      { label: '订单', path: '/account/orders', icon: 'order-ascending' },
      { label: '卡密管理', path: '/account/redemption-codes', icon: 'ticket' }
    ]
  },
  {
    label: '系统设置',
    items: [
      { label: '公告', path: '/account/announcements', icon: 'notification' },
      { label: '售后设置', path: '/account/support-contacts', icon: 'service' },
      { label: '审计日志', path: '/account/audit', icon: 'history' },
      { label: '日志', path: '/account/logs', icon: 'file' },
      { label: '代理', path: '/account/proxy', icon: 'internet' },
      { label: '脚本', path: '/account/scripts', icon: 'code' },
      { label: '访问与安全', path: '/account/access', icon: 'secured' }
    ]
  }
]

const memberNavigation: NavigationSection[] = [
  {
    label: '服务',
    items: [
      { label: '进入使用', path: '/login-chatgpt', icon: 'play-circle' },
      { label: '卡密充值', path: '/account/billing', icon: 'wallet' },
      { label: '通知', path: '/account/notifications', icon: 'mail' },
      { label: '售后支持', path: '/account/support', icon: 'service' }
    ]
  }
]

const userOptions = [
  { content: '退出登录', value: 'logout' }
]

const handleMenuChange = (value: string) => {
  mobileMenuOpen.value = false
  router.push(value)
}

const loadRequiredNotifications = async () => {
  if (isAdmin.value) return
  const data = await request('/0x/billing/notifications?unread=1&requires_acknowledgement=1&page_size=50')
  requiredNotifications.value = data?.results || []
}

const acknowledgeRequiredNotification = async () => {
  const notification = requiredNotification.value
  if (!notification) return
  acknowledgingNotification.value = true
  const data = await request(`/0x/billing/notifications/${notification.id}/read`, 'POST', {})
  acknowledgingNotification.value = false
  if (!data) return
  requiredNotifications.value.shift()
}

const handleUserAction = (data: { value: string }) => {
  if (data.value === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}

onMounted(loadRequiredNotifications)
watch(() => route.fullPath, () => { mobileMenuOpen.value = false })
</script>

<style scoped>
.layout {
  min-height: 100vh;
  min-height: 100dvh;
}

.layout-shell {
  min-height: 100vh;
  min-height: 100dvh;
}

.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  height: 100dvh;
  min-width: 232px;
  flex: 0 0 232px;
  color: var(--app-text);
  background: #f1f1ee;
  border-right: 1px solid var(--app-border);
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-title {
  display: flex;
  align-items: center;
  gap: 11px;
  height: 68px;
  padding: 0 18px;
  color: var(--app-text);
  border-bottom: 1px solid var(--app-border);
}

.brand-lockup {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 11px;
}

.mobile-menu-button,
.mobile-close-button,
.mobile-nav-backdrop {
  display: none;
}

.brand-icon {
  flex: 0 0 auto;
  color: #4f8061;
  font-size: 19px;
}

.brand-copy {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.brand-copy strong {
  font-size: 15px;
  font-weight: 650;
  line-height: 1.2;
}

.brand-copy span {
  color: var(--app-text-muted);
  font-size: 11px;
  line-height: 1.2;
}

.navigation {
  flex: 1 1 auto;
  min-height: 0;
  padding: 14px 10px 10px;
  overflow-x: hidden;
  overflow-y: auto;
}

.nav-section + .nav-section {
  margin-top: 16px;
}

.nav-section-label {
  margin: 0 0 6px;
  padding: 0 10px;
  color: #8a8a84;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
}

.nav-menu {
  width: 100%;
  min-width: 0;
  padding: 0;
  background: transparent;
  overflow-x: hidden;
}

.nav-menu :deep(.t-menu__item) {
  min-width: 0;
  height: 40px;
  margin-bottom: 2px;
  color: #555550;
  border-radius: 7px;
}

.nav-menu :deep(.t-menu__item:hover) {
  color: var(--app-text);
  background: #e8e8e4;
}

.nav-menu :deep(.t-menu__item.t-is-active) {
  color: var(--app-text);
  font-weight: 600;
  background: #dededa;
}

.sidebar-footer {
  flex: 0 0 auto;
  padding: 10px;
  border-top: 1px solid var(--app-border);
}

.profile-link {
  justify-content: flex-start;
  height: 40px;
  color: #555550;
  font-size: 14px;
  border-radius: 7px;
}

.profile-link:hover {
  color: var(--app-text);
  background: #e8e8e4;
}

.workspace {
  min-width: 0;
  background: var(--app-bg);
}

.header {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 64px;
  padding: 0 32px;
  background: rgba(247, 247, 245, 0.96);
  border-bottom: 1px solid var(--app-border);
}

.header h1 {
  margin: 0;
  color: var(--app-text);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}


.user-button {
  color: #4f4f4b;
  border-radius: 7px;
}

.user-button:hover {
  color: var(--app-text);
  background: #ecece8;
}

.content {
  min-width: 0;
  padding: 32px;
  background: var(--app-bg);
  min-height: calc(100vh - 64px);
}

.content-inner {
  width: 100%;
  max-width: 1440px;
  margin: 0 auto;
}

.required-notice p { margin: 0; color: var(--app-text); font-size: 14px; line-height: 1.8; white-space: pre-wrap; }
.required-notice time { display: block; margin-top: 18px; color: var(--app-text-muted); font-size: 12px; }

@media (max-width: 900px) {
  .layout-shell {
    display: block;
  }

  .sidebar {
    position: fixed;
    z-index: 40;
    width: min(286px, 84vw) !important;
    min-width: min(286px, 84vw);
    max-width: 84vw;
    flex: none !important;
    transform: translateX(-102%);
    transition: transform 0.22s ease;
    box-shadow: 14px 0 42px rgba(25, 25, 23, 0.16);
  }

  .sidebar.sidebar-open {
    transform: translateX(0);
  }

  .sidebar-title {
    justify-content: space-between;
    padding: 0 14px 0 18px;
  }

  .navigation {
    padding: 14px 10px 10px;
  }

  .nav-menu :deep(.t-menu__item) {
    width: 100%;
    min-width: 0;
    justify-content: flex-start;
    padding: 0 12px;
  }

  .profile-link {
    justify-content: flex-start;
    min-width: 0;
    padding: 0 12px;
  }

  .mobile-menu-button,
  .mobile-close-button {
    display: inline-flex;
    flex: 0 0 40px;
    width: 40px;
    min-width: 40px;
    height: 40px;
    padding: 0;
  }

  .mobile-nav-backdrop {
    position: fixed;
    inset: 0;
    z-index: 35;
    display: block;
    width: 100%;
    height: 100%;
    cursor: pointer;
    background: rgba(25, 25, 23, 0.34);
    border: 0;
  }

  .workspace {
    width: 100%;
  }

  .header {
    height: 60px;
    padding: 0 14px;
  }

  .content {
    min-height: calc(100vh - 60px);
    padding: 18px 14px;
  }
}

@media (max-width: 560px) {
  .header {
    padding: 0 10px;
  }

  .header h1 {
    max-width: 44vw;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 16px;
  }

  .user-button {
    font-size: 0;
  }


  .user-button :deep(.t-icon) {
    font-size: 18px;
  }

  .content {
    padding: 14px 10px;
  }
}
</style>
