/**
 * HTTP 客户端 - 全局唯一 API 请求模块
 * 自动附加 Authorization 头，支持 401 自动刷新 token 并重试。
 */

const API_BASE = 'http://127.0.0.1:8000'

// 刷新锁：防止多个并发请求同时刷新 token
let isRefreshing = false
let refreshQueue = []

function getAccessToken() {
  return localStorage.getItem('access_token')
}

function getRefreshToken() {
  return localStorage.getItem('refresh_token')
}

function setTokens(accessToken, refreshToken) {
  localStorage.setItem('access_token', accessToken)
  if (refreshToken) {
    localStorage.setItem('refresh_token', refreshToken)
  }
}

function clearTokens() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  localStorage.removeItem('user')
}

/**
 * 尝试用 refresh token 换取新的 access token
 * 返回 true 表示刷新成功，false 表示失败
 */
async function tryRefreshToken() {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false

  try {
    const response = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (!response.ok) return false

    const data = await response.json()
    if (data.status === 'success') {
      setTokens(data.access_token, data.refresh_token)
      if (data.user) {
        localStorage.setItem('user', JSON.stringify(data.user))
      }
      return true
    }
    return false
  } catch {
    return false
  }
}

/**
 * 执行刷新 token（带锁，确保多个并发请求只刷新一次）
 */
async function doRefresh() {
  if (isRefreshing) {
    // 已有刷新正在进行，等待其结果
    return new Promise((resolve) => {
      refreshQueue.push(resolve)
    })
  }

  isRefreshing = true
  const success = await tryRefreshToken()
  isRefreshing = false

  // 通知等待队列
  refreshQueue.forEach((resolve) => resolve(success))
  refreshQueue = []

  return success
}

/**
 * 核心请求函数
 */
async function requestJson(path, options = {}) {
  const token = getAccessToken()

  const headers = {
    ...(options.headers || {}),
  }

  // 自动附加 token
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  // 非 FormData 请求自动设置 Content-Type
  if (!(options.body instanceof FormData)) {
    if (!headers['Content-Type']) {
      headers['Content-Type'] = 'application/json'
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  // 401 时尝试刷新 token 并重试一次
  if (response.status === 401 && token) {
    const refreshed = await doRefresh()
    if (refreshed) {
      const newHeaders = { ...headers }
      newHeaders['Authorization'] = `Bearer ${getAccessToken()}`
      const retryResponse = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: newHeaders,
      })
      if (retryResponse.ok) {
        const text = await retryResponse.text()
        try {
          return text ? JSON.parse(text) : {}
        } catch {
          return { raw: text }
        }
      }
      if (retryResponse.status === 401) {
        clearTokens()
        window.dispatchEvent(new CustomEvent('auth:logout'))
        throw new Error('登录已过期，请重新登录')
      }
    }
    // 刷新失败
    clearTokens()
    window.dispatchEvent(new CustomEvent('auth:logout'))
    throw new Error('登录已过期，请重新登录')
  }

  // 解析响应
  const text = await response.text()
  let data = {}
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { raw: text }
  }

  if (!response.ok) {
    const detail = data?.detail || data?.message || text || `HTTP ${response.status}`
    throw new Error(detail)
  }

  return data
}

// 便捷方法
function apiGet(path) {
  return requestJson(path, { method: 'GET' })
}

function apiPost(path, body) {
  return requestJson(path, {
    method: 'POST',
    body: body instanceof FormData ? body : JSON.stringify(body),
    headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
  })
}

function apiPut(path, body) {
  return requestJson(path, {
    method: 'PUT',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  })
}

function apiDelete(path) {
  return requestJson(path, { method: 'DELETE' })
}

export {
  API_BASE,
  requestJson,
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  getAccessToken,
  getRefreshToken,
  setTokens,
  clearTokens,
  tryRefreshToken,
}
