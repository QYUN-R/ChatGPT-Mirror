<template>
  <div class="layout">
    <t-layout class="layout-shell">
      <t-aside class="sidebar" width="232px">
        <div class="sidebar-title">
          管理
        </div>
        <t-menu class="nav-menu" :value="activeMenu" theme="light" @change="handleMenuChange">
          <t-menu-item v-if="userStore.isAdmin" value="/account/overview">
            <template #icon><t-icon name="dashboard" /></template>
            <span class="menu-label">运维概览</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/user">
            <template #icon><t-icon name="user" /></template>
            <span class="menu-label">用户</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/chatgpt">
            <template #icon><t-icon name="root-list" /></template>
            <span class="menu-label">上游账号</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/gptcar">
            <template #icon><t-icon name="server" /></template>
            <span class="menu-label">账号池</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/plans">
            <template #icon><t-icon name="money-circle" /></template>
            <span class="menu-label">套餐配置</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/pools">
            <template #icon><t-icon name="layers" /></template>
            <span class="menu-label">商业号池</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/subscriptions">
            <template #icon><t-icon name="usergroup" /></template>
            <span class="menu-label">用户订阅</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/orders">
            <template #icon><t-icon name="order-ascending" /></template>
            <span class="menu-label">订单</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/announcements">
            <template #icon><t-icon name="notification" /></template>
            <span class="menu-label">公告</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/audit">
            <template #icon><t-icon name="history" /></template>
            <span class="menu-label">审计日志</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/logs">
            <template #icon><t-icon name="file" /></template>
            <span class="menu-label">日志</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/proxy">
            <template #icon><t-icon name="internet" /></template>
            <span class="menu-label">代理</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/scripts">
            <template #icon><t-icon name="code" /></template>
            <span class="menu-label">脚本</span>
          </t-menu-item>
          <t-menu-item v-if="userStore.isAdmin" value="/account/access">
            <template #icon><t-icon name="secured" /></template>
            <span class="menu-label">访问与安全</span>
          </t-menu-item>
          <t-menu-item value="/account/billing">
            <template #icon><t-icon name="wallet" /></template>
            <span class="menu-label">套餐中心</span>
          </t-menu-item>
          <t-menu-item value="/account/notifications">
            <template #icon><t-icon name="mail" /></template>
            <span class="menu-label">通知</span>
          </t-menu-item>
          <t-menu-item value="/account/profile">
            <template #icon><t-icon name="user-circle" /></template>
            <span class="menu-label">账户中心</span>
          </t-menu-item>
        </t-menu>
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
const username = computed(() => userStore.username || '管理员')
const pageTitle = computed(() => String(route.meta.title || '管理'))

const userOptions = [
  { content: '账户中心', value: 'profile' },
  { content: '退出登录', value: 'logout' }
]

const handleMenuChange = (value: string) => {
  router.push(value)
}

const handleUserAction = (data: { value: string }) => {
  if (data.value === 'profile') {
    router.push('/account/profile')
    return
  }
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
  overflow-y: auto;
}

.sidebar-title {
  display: flex;
  align-items: center;
  height: 64px;
  padding: 0 22px;
  color: var(--app-text);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  border-bottom: 1px solid var(--app-border);
}

.nav-menu {
  width: 100%;
  min-width: 0;
  padding: 12px 10px;
  background: transparent;
  overflow-x: hidden;
}

.nav-menu :deep(.t-menu__item) {
  min-width: 0;
  height: 44px;
  margin-bottom: 4px;
  color: #555550;
  border-radius: 8px;
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
    font-size: 0;
  }

  .sidebar-title::after {
    font-size: 15px;
    content: "管理";
  }

  .nav-menu {
    padding: 12px 8px;
  }

  .nav-menu :deep(.t-menu__item) {
    width: 60px;
    min-width: 60px;
    justify-content: center;
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
