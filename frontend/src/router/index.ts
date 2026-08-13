import { createRouter, createWebHashHistory, RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/store/user'

function hasChatGPTSession(): boolean {
  return false
}

function clearAccessibleCookies(): void {
  const cookies = document.cookie.split(';')
  for (const entry of cookies) {
    const [rawName] = entry.split('=', 1)
    const name = rawName?.trim()
    if (!name) continue
    document.cookie = `${name}=; Path=/; Max-Age=0`
  }
}

async function hasRequiredAnnouncement(): Promise<boolean> {
  try {
    const response = await fetch(
      '/0x/billing/notifications?unread=1&requires_acknowledgement=1&page_size=1',
      { credentials: 'include' }
    )
    if (!response.ok) return false
    const contentType = response.headers.get('content-type') || ''
    const text = await response.text()
    if (!contentType.includes('application/json')) return false
    const data = JSON.parse(text)
    return Array.isArray(data.results) && data.results.length > 0
  } catch {
    return false
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/login'
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/login/index.vue')
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/pages/login/index.vue')
  },
  {
    path: '/forgot-password',
    name: 'ForgotPassword',
    component: () => import('@/pages/login/index.vue')
  },
  {
    path: '/bind-email',
    name: 'BindEmail',
    component: () => import('@/pages/login/index.vue')
  },
  {
    path: '/login-chatgpt',
    name: 'LoginChatgpt',
    component: () => import('@/pages/login/chatgpt.vue')
  },
  {
    path: '/account',
    name: 'Account',
    component: () => import('@/layouts/index.vue'),
    redirect: '/account/overview',
    children: [
      {
        path: 'overview',
        name: 'Overview',
        component: () => import('@/pages/account/overview.vue'),
        meta: { title: '运维概览', requiresAdmin: true }
      },
      {
        path: 'user',
        name: 'User',
        component: () => import('@/pages/account/user.vue'),
        meta: { title: '用户', requiresAdmin: true }
      },
      {
        path: 'chatgpt',
        name: 'ChatGPT',
        component: () => import('@/pages/account/chatgpt.vue'),
        meta: { title: '上游账号', requiresAdmin: true }
      },
      {
        path: 'gptcar',
        name: 'GptCar',
        component: () => import('@/pages/account/gptcar.vue'),
        meta: { title: '免费账号池', requiresAdmin: true }
      },
      {
        path: 'plans',
        name: 'Plans',
        component: () => import('@/pages/account/plans.vue'),
        meta: { title: '套餐配置', requiresAdmin: true }
      },
      {
        path: 'pools',
        name: 'CommercialPools',
        component: () => import('@/pages/account/pools.vue'),
        meta: { title: '套餐号池', requiresAdmin: true }
      },
      {
        path: 'subscriptions',
        name: 'Subscriptions',
        component: () => import('@/pages/account/subscriptions.vue'),
        meta: { title: '用户订阅', requiresAdmin: true }
      },
      {
        path: 'orders',
        name: 'Orders',
        component: () => import('@/pages/account/orders.vue'),
        meta: { title: '订单', requiresAdmin: true }
      },
      {
        path: 'redemption-codes',
        name: 'RedemptionCodes',
        component: () => import('@/pages/account/redemption-codes.vue'),
        meta: { title: '卡密管理', requiresAdmin: true }
      },
      {
        path: 'announcements',
        name: 'Announcements',
        component: () => import('@/pages/account/announcements.vue'),
        meta: { title: '公告', requiresAdmin: true }
      },
      {
        path: 'support-contacts',
        name: 'SupportContactsAdmin',
        component: () => import('@/pages/account/support-contacts.vue'),
        meta: { title: '售后支持', requiresAdmin: true }
      },
      {
        path: 'audit',
        name: 'AuditLogs',
        component: () => import('@/pages/account/audit.vue'),
        meta: { title: '审计日志', requiresAdmin: true }
      },
      {
        path: 'logs',
        name: 'Logs',
        component: () => import('@/pages/account/logs.vue'),
        meta: { title: '日志', requiresAdmin: true }
      },
      {
        path: 'proxy',
        name: 'Proxy',
        component: () => import('@/pages/account/proxy.vue'),
        meta: { title: '代理', requiresAdmin: true }
      },
      {
        path: 'scripts',
        name: 'Scripts',
        component: () => import('@/pages/account/scripts.vue'),
        meta: { title: '脚本', requiresAdmin: true }
      },
      {
        path: 'access',
        name: 'AccessControl',
        component: () => import('@/pages/account/access.vue'),
        meta: { title: '访问与安全', requiresAdmin: true }
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/pages/account/profile.vue'),
        meta: { title: '账户中心' }
      },
      {
        path: 'billing',
        name: 'Billing',
        component: () => import('@/pages/account/billing.vue'),
        meta: { title: '卡密充值', memberOnly: true }
      },
      {
        path: 'notifications',
        name: 'Notifications',
        component: () => import('@/pages/account/notifications.vue'),
        meta: { title: '通知', memberOnly: true }
      },
      {
        path: 'support',
        name: 'SupportCenter',
        component: () => import('@/pages/account/support.vue'),
        meta: { title: '售后支持', memberOnly: true }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHashHistory('/admin/'),
  routes
})

// 路由守卫
router.beforeEach(async (to, _from, next) => {
  const userStore = useUserStore()
  const isLoginPage = to.path === '/login' || to.path === '/login-chatgpt'
  const isPublicAuthPage = ['/login', '/register', '/forgot-password', '/bind-email'].includes(to.path)

  if (isLoginPage && hasChatGPTSession()) {
    window.location.replace('/chat')
    next(false)
    return
  }

  const authenticated = await userStore.hydrate()
  if (to.path === '/account/overview' && authenticated && !userStore.isAdmin) {
    next('/account/billing')
    return
  }
  if (to.meta.memberOnly && authenticated && userStore.isAdmin) {
    next('/account/overview')
    return
  }
  if (to.meta.requiresAdmin && (!authenticated || !userStore.isAdmin)) {
    if (authenticated && !userStore.isAdmin) {
      clearAccessibleCookies()
    }
    next('/login')
    return
  }

  if (to.path === '/login-chatgpt' && authenticated && !userStore.isAdmin && await hasRequiredAnnouncement()) {
    next({ name: 'Billing', query: { notice: 'required' } })
    return
  }
  
  if (!isPublicAuthPage && to.path !== '/login-chatgpt' && !authenticated) {
    next('/login')
  } else {
    next()
  }
})

export default router
