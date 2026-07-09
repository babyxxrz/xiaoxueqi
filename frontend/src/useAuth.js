/**
 * 认证状态管理 - Vue 3 Composable（模块级单例）
 * 所有组件共享同一份认证状态
 */
import { ref, computed } from 'vue'
import { apiPost, apiGet, setTokens, clearTokens, getRefreshToken } from './api'

// ---- 模块级响应式状态（单例）----

const currentUser = ref(null)
const loading = ref(false)
const initialized = ref(false)

function loadUserFromStorage() {
  try {
    const raw = localStorage.getItem('user')
    if (raw) {
      const user = JSON.parse(raw)
      if (user && user.id) return user
    }
  } catch {
    // ignore
  }
  return null
}

// ---- 计算属性 ----

const isAuthenticated = computed(() => {
  return !!(currentUser.value?.id && localStorage.getItem('access_token'))
})

const isAdmin = computed(() => {
  return currentUser.value?.role === 'admin'
})

// ---- 公开方法 ----

async function login(account, password, remember = false) {
  loading.value = true
  try {
    const data = await apiPost('/api/auth/login', {
      account,
      password,
      remember,
    })

    if (data.status === 'success') {
      setTokens(data.access_token, data.refresh_token)
      localStorage.setItem('user', JSON.stringify(data.user))
      currentUser.value = data.user
      return { success: true, user: data.user }
    }
    return { success: false, error: data.message || '登录失败' }
  } catch (error) {
    return { success: false, error: error.message || '登录失败，请检查网络连接' }
  } finally {
    loading.value = false
  }
}

async function logout() {
  const refreshToken = getRefreshToken()
  if (refreshToken) {
    try {
      await apiPost('/api/auth/logout', { refresh_token: refreshToken })
    } catch {
      // 即使服务端请求失败，也要清除本地状态
    }
  }
  clearTokens()
  currentUser.value = null
}

async function fetchMe() {
  try {
    const data = await apiGet('/api/auth/me')
    if (data.status === 'success') {
      currentUser.value = data.user
      localStorage.setItem('user', JSON.stringify(data.user))
    }
  } catch {
    // token 无效，清除状态
    clearTokens()
    currentUser.value = null
  }
}

async function init() {
  if (initialized.value) return
  initialized.value = true

  // 从 localStorage 恢复用户信息
  const savedUser = loadUserFromStorage()
  if (savedUser) {
    currentUser.value = savedUser
  }

  // 如果有 access token，验证其有效性
  if (localStorage.getItem('access_token')) {
    await fetchMe()
  }
}

// 注册全局退出事件监听（在首次 import 时执行）
if (typeof window !== 'undefined') {
  window.addEventListener('auth:logout', () => {
    clearTokens()
    currentUser.value = null
  })
}

// ---- 导出 ----

export function useAuth() {
  return {
    currentUser,
    isAuthenticated,
    isAdmin,
    loading,
    initialized,
    login,
    logout,
    fetchMe,
    init,
    clearTokens,
  }
}
