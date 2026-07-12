/**
 * HTTP 客户端 - 全局唯一 API 请求模块
 * 自动附加 Authorization 头，支持 401 自动刷新 token 并重试。
 */

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

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

function authHeaders(extra = {}) {
  const token = getAccessToken()
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  }
}

function formatErrorDetail(data, text, status) {
  const detail = data?.detail ?? data?.message ?? text ?? `HTTP ${status}`
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}

async function parseResponse(response) {
  const text = await response.text()
  let data = {}

  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { raw: text }
  }

  if (!response.ok) {
    const error = new Error(formatErrorDetail(data, text, response.status))
    error.status = response.status
    error.data = data
    throw error
  }

  return data
}

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

async function doRefresh() {
  if (isRefreshing) {
    return new Promise((resolve) => {
      refreshQueue.push(resolve)
    })
  }

  isRefreshing = true
  const success = await tryRefreshToken()
  isRefreshing = false

  refreshQueue.forEach((resolve) => resolve(success))
  refreshQueue = []

  return success
}

async function requestJson(path, options = {}) {
  const token = getAccessToken()
  const headers = authHeaders(options.headers || {})

  if (!(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  let response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  if (response.status === 401 && token) {
    const refreshed = await doRefresh()

    if (refreshed) {
      response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: authHeaders(options.headers || {}),
      })
    } else {
      clearTokens()
      window.dispatchEvent(new CustomEvent('auth:logout'))
      const error = new Error('登录已过期，请重新登录')
      error.status = 401
      throw error
    }
  }

  try {
    return await parseResponse(response)
  } catch (error) {
    if (error.status === 401) {
      clearTokens()
      window.dispatchEvent(new CustomEvent('auth:logout'))
    }
    throw error
  }
}

function apiGet(path, options = {}) {
  return requestJson(path, {
    ...options,
    method: 'GET',
  })
}

function apiPost(path, body = {}, options = {}) {
  return requestJson(path, {
    ...options,
    method: 'POST',
    body: body instanceof FormData ? body : JSON.stringify(body),
    headers: {
      ...(body instanceof FormData
        ? {}
        : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
  })
}

function apiPut(path, body = {}, options = {}) {
  return requestJson(path, {
    ...options,
    method: 'PUT',
    body: JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  })
}

function apiDelete(path, options = {}) {
  return requestJson(path, {
    ...options,
    method: 'DELETE',
  })
}

function uploadFile(path, file, fieldName = 'file') {
  const formData = new FormData()
  formData.append(fieldName, file)
  return apiPost(path, formData)
}

export {
  API_BASE,
  requestJson,
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  uploadFile,
  authHeaders,
  getAccessToken,
  getRefreshToken,
  setTokens,
  clearTokens,
  tryRefreshToken,
}
