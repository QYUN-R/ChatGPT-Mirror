import { defineStore } from 'pinia'
import { ref } from 'vue'

const clearAccessibleCookies = () => {
  const cookies = document.cookie.split(';')
  for (const entry of cookies) {
    const [rawName] = entry.split('=', 1)
    const name = rawName?.trim()
    if (!name) continue
    document.cookie = `${name}=; Path=/; Max-Age=0`
  }
}

const extractLoginError = (error: any): string => {
  if (typeof error?.message === 'string' && error.message.trim()) return error.message
  if (typeof error?.detail === 'string' && error.detail.trim()) return error.detail
  for (const value of Object.values(error || {})) {
    if (Array.isArray(value) && value.length > 0) return String(value[0])
    if (typeof value === 'string' && value.trim()) return value
  }
  return '登录失败'
}

const parseJsonResponse = async (response: Response) => {
  const text = await response.text()
  if (!text) return null

  try {
    return JSON.parse(text)
  } catch {
    const isServerFailure = response.status >= 500
    throw new Error(isServerFailure ? '系统异常，请稍后重试' : '服务响应异常，请刷新后重试')
  }
}

export const useUserStore = defineStore('user', () => {
  const authenticated = ref(false)
  const isAdmin = ref(false)
  const username = ref('')
  const csrfToken = ref('')
  let hydrated = false

  const setIsAdmin = (admin: boolean) => {
    isAdmin.value = admin
  }

  const setUsername = (name: string) => {
    username.value = name
  }

  const setCsrfToken = (token: string) => {
    csrfToken.value = token
  }

  const applySession = (result: any) => {
    if (!result || typeof result !== 'object') return false
    authenticated.value = Boolean(result.authenticated)
    isAdmin.value = Boolean(result.is_admin)
    username.value = result.username || ''
    csrfToken.value = result.csrf_token || ''
    return authenticated.value
  }

  const login = async (url: string, data: any) => {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      credentials: 'include',
      body: JSON.stringify(data)
    })

    if (!response.ok) {
      const error = await parseJsonResponse(response)
      throw new Error(extractLoginError(error))
    }

    const result = await parseJsonResponse(response)
    if (!result || typeof result !== 'object') {
      throw new Error('服务响应异常，请刷新后重试')
    }
    
    applySession(result)
    if (!username.value) setUsername(data.username || data.identifier || '')
    hydrated = true

    return result
  }

  const hydrate = async (force = false) => {
    if (hydrated && !force) return authenticated.value
    hydrated = true
    try {
      const response = await fetch('/0x/user/me', { credentials: 'include' })
      if (!response.ok) return false
      const result = await parseJsonResponse(response)
      return applySession(result)
    } catch {
      return false
    }
  }

  const logout = async () => {
    const activeCsrfToken =
      document.cookie.match(/(?:^|; )csrftoken=([^;]*)/)?.[1] || csrfToken.value || ''
    if (authenticated.value) {
      try {
        await fetch('/0x/user/logout', {
          method: 'POST',
          keepalive: true,
          credentials: 'include',
          headers: activeCsrfToken ? { 'X-CSRFToken': decodeURIComponent(activeCsrfToken) } : {}
        })
      } catch {
        // 本地状态仍需清理；服务端 Token 会按 TTL 自动过期。
      }
    }
    authenticated.value = false
    isAdmin.value = false
    username.value = ''
    csrfToken.value = ''
    clearAccessibleCookies()
  }

  return {
    authenticated,
    isAdmin,
    username,
    csrfToken,
    setIsAdmin,
    setUsername,
    setCsrfToken,
    login,
    hydrate,
    logout
  }
})
