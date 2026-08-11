<template>
  <div class="layout">
    <t-layout class="layout-shell">
      <t-aside class="sidebar" width="232px">
        <div class="sidebar-title">
          <t-icon class="brand-icon" name="dashboard" />
          <div class="brand-copy">
            <strong>Chat2</strong>
            <span>{{ isAdmin ? '管理后台' : '用户中心' }}</span>
          </div>
        </div>
        <nav class="navigation" :aria-label="isAdmin ? '管理后台导航' : '用户中心导航'">
          <template v-if="isAdmin">
            <section v-for="section in adminNavigation" :key="section.label" class="nav-section">
              <p class="nav-section-label">{{ section.label }}</p>
              <t-menu class="nav-menu" :value="activeMenu" theme="light" @change="handleMenuChange">
                <t-menu-item v-for="item in section.items" :key="item.path" :value="item.path">
                  <template #icon><t-icon :name="item.icon" /></template>
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
                  <template #icon><t-icon :name="item.icon" /></template>
                  <span class="menu-label">{{ item.label }}</span>
                </t-menu-item>
              </t-menu>
            </section>
          </template>
        </nav>
        <div class="sidebar-footer">
          <t-button class="profile-link" variant="text" block @click="router.push('/account/profile')">
            <template #icon><t-icon name="user-circle" /></template>
            <span>{{ isAdmin ? '账户设置' : '账户中心' }}</span>
          </t-button>
        </div>
      </t-aside>
      <t-layout class="workspace">
        <t-header class="header">
          <h1>{{ pageTitle }}</h1>
          <div class="header-right">
            <t-dropdown :options="userOptions" @click="handleUserAction">
              <t-button class="user-button" variant="text">
                <t-icon name="user-circle" />
                {{ username }}
                <t-icon name="chevron-down" />
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
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/store/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const activeMenu = computed(() => route.path)
const isAdmin = computed(() => userStore.isAdmin)
const username = computed(() => userStore.username || '管理员')
const pageTitle = computed(() => String(route.meta.title || (isAdmin.value ? '管理后台' : '用户中心')))

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
      { label: '订单', path: '/account/orders', icon: 'order-ascending' }
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
      { label: '套餐中心', path: '/account/billing', icon: 'wallet' },
      { label: '通知', path: '/account/notifications', icon: 'mail' },
      { label: '售后支持', path: '/account/support', icon: 'service' }
    ]
  }
]

const userOptions = [
  { content: '退出登录', value: 'logout' }
]

const handleMenuChange = (value: string) => {
  router.push(value)
}

const handleUserAction = (data: { value: string }) => {
  if (data.value === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}
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

@media (max-width: 900px) {
  .sidebar {
    width: 76px !important;
    min-width: 76px;
    flex: 0 0 76px !important;
    flex-basis: 76px !important;
  }

  .sidebar-title {
    justify-content: center;
    padding: 0;
  }

  .brand-copy,
  .nav-section-label,
  .profile-link span {
    display: none;
  }

  .navigation {
    padding: 12px 8px;
  }

  .nav-section + .nav-section {
    margin-top: 10px;
  }

  .nav-menu :deep(.t-menu__item) {
    width: 60px;
    min-width: 60px;
    justify-content: center;
    padding: 0;
  }

  .sidebar-footer {
    padding: 8px;
  }

  .profile-link {
    justify-content: center;
    min-width: 60px;
    padding: 0;
  }

  .menu-label {
    display: none;
  }

  .header {
    padding: 0 20px;
  }

  .content {
    padding: 20px;
  }
}

@media (max-width: 560px) {
  .sidebar {
    width: 64px !important;
    min-width: 64px;
    flex: 0 0 64px !important;
    flex-basis: 64px !important;
  }

  .nav-menu :deep(.t-menu__item) {
    width: 48px;
    min-width: 48px;
  }

  .profile-link {
    min-width: 48px;
  }

  .header {
    padding: 0 16px;
  }

  .header h1 {
    font-size: 16px;
  }

  .user-button {
    font-size: 0;
  }

  .user-button :deep(.t-icon) {
    font-size: 18px;
  }

  .content {
    padding: 16px 12px;
  }
}
</style>
