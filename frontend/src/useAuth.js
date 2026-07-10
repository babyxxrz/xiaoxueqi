/**
 * 认证状态管理 - Vue 3 Composable（模块级单例）
 * 同时保留：
 * 1. 用户名/邮箱 + 密码登录
 * 2. 邮箱验证码登录
 * 3. access token 自动刷新
 */
import { computed, ref } from 'vue'
import { apiGet, apiPost, clearTokens, getRefreshToken, setTokens } from './api'

const currentUser = ref(null)
const loading = ref(false)
const initialized = ref(false)
let initPromise = null

function loadUserFromStorage() {
  try {
    const raw = localStorage.getItem('user')
    if (raw) {
      const savedUser = JSON.parse(raw)
      if (savedUser?.id) return savedUser
    }
  } catch {
    // 本地缓存损坏时忽略。
  }
  return null
}

function saveLoginResult(data) {
  if (data.status !== 'success' || !data.access_token || !data.user) {
    return {
      success: false,
      error: data.message || '登录失败',
    }
  }

  setTokens(data.access_token, data.refresh_token)
  localStorage.setItem('user', JSON.stringify(data.user))
  currentUser.value = data.user

  return {
    success: true,
    user: data.user,
  }
}

const isAuthenticated = computed(() => {
  return Boolean(currentUser.value?.id && localStorage.getItem('access_token'))
})

const isAdmin = computed(() => currentUser.value?.role === 'admin')
const role = computed(() => currentUser.value?.role || 'user')

// 兼容重构后的用户端和管理员端命名。
const user = currentUser

async function login(account, password, remember = false) {
  loading.value = true

  try {
    const data = await apiPost('/api/auth/login', {
      account,
      password,
      remember,
    })
    return saveLoginResult(data)
  } catch (error) {
    return {
      success: false,
      error: error.message || '登录失败，请检查网络连接',
    }
  } finally {
    loading.value = false
  }
}

async function sendLoginCode(email) {
  const data = await apiPost('/api/auth/send-code', {
    email: email.trim().toLowerCase(),
  })

  return {
    success: data.status === 'success',
    message: data.message || '验证码已发送',
  }
}

async function loginByCode(email, code, remember = false) {
  loading.value = true

  try {
    const data = await apiPost('/api/auth/login-by-code', {
      email: email.trim().toLowerCase(),
      code: code.trim(),
      remember,
    })
    return saveLoginResult(data)
  } catch (error) {
    return {
      success: false,
      error: error.message || '验证码登录失败',
    }
  } finally {
    loading.value = false
  }
}

async function logout() {
  const refreshToken = getRefreshToken()

  if (refreshToken) {
    try {
      await apiPost('/api/auth/logout', {
        refresh_token: refreshToken,
      })
    } catch {
      // 服务端退出失败时仍清除本地登录状态。
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
      return true
    }
  } catch {
    clearTokens()
    currentUser.value = null
  }

  return false
}

async function init() {
  if (initialized.value) return
  if (initPromise) return initPromise

  initPromise = (async () => {
    const savedUser = loadUserFromStorage()
    if (savedUser) {
      currentUser.value = savedUser
    }

    if (localStorage.getItem('access_token')) {
      await fetchMe()
    }

    initialized.value = true
  })()

  try {
    await initPromise
  } finally {
    initPromise = null
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('auth:logout', () => {
    clearTokens()
    currentUser.value = null
  })
}

export function useAuth() {
  return {
    currentUser,
    user,
    role,
    isAuthenticated,
    isAdmin,
    loading,
    initialized,
    login,
    sendLoginCode,
    loginByCode,
    logout,
    fetchMe,
    init,
    clearTokens,
  }
}
