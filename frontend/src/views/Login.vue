<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="login-logo">&#x1f698;</div>
        <h2>智能车载视觉感知与告警系统</h2>
        <p class="login-subtitle">请登录以继续操作</p>
      </div>

      <!-- 登录方式切换 -->
      <div class="login-tabs">
        <button
          type="button"
          class="login-tab"
          :class="{ active: loginMode === 'password' }"
          @click="loginMode = 'password'"
        >
          密码登录
        </button>
        <button
          type="button"
          class="login-tab"
          :class="{ active: loginMode === 'code' }"
          @click="loginMode = 'code'"
        >
          验证码登录
        </button>
      </div>

      <!-- 密码登录表单 -->
      <form v-if="loginMode === 'password'" class="login-form" @submit.prevent="handleLogin">
        <!-- 账号 -->
        <label class="login-field">
          <span>用户名 / 邮箱</span>
          <input
            v-model="form.account"
            type="text"
            placeholder="请输入用户名或邮箱"
            autocomplete="username"
            :disabled="submitting"
            @input="clearFieldError('account')"
          />
          <p v-if="fieldErrors.account" class="login-field-error">{{ fieldErrors.account }}</p>
        </label>

        <!-- 密码 -->
        <label class="login-field">
          <span>密码</span>
          <div class="login-password-wrap">
            <input
              v-model="form.password"
              :type="showPassword ? 'text' : 'password'"
              placeholder="请输入密码"
              autocomplete="current-password"
              :disabled="submitting"
              @input="clearFieldError('password')"
            />
            <button
              type="button"
              class="login-toggle-pw small-btn"
              @click="showPassword = !showPassword"
              tabindex="-1"
            >
              {{ showPassword ? '隐藏' : '显示' }}
            </button>
          </div>
          <p v-if="fieldErrors.password" class="login-field-error">{{ fieldErrors.password }}</p>
        </label>

        <!-- 记住我 -->
        <label class="checkbox-line login-remember">
          <input v-model="form.remember" type="checkbox" :disabled="submitting" />
          <span>记住我（保持登录 30 天）</span>
        </label>

        <!-- 错误提示 -->
        <div v-if="errorMessage" class="login-error">{{ errorMessage }}</div>

        <!-- 提交按钮 -->
        <button type="submit" class="login-submit-btn" :disabled="submitting">
          <span v-if="submitting" class="login-spinner"></span>
          {{ submitting ? '登录中...' : '登录' }}
        </button>
      </form>

      <!-- 验证码登录表单 -->
      <form v-else class="login-form" @submit.prevent="handleCodeLogin">
        <!-- 邮箱 -->
        <label class="login-field">
          <span>邮箱</span>
          <input
            v-model="codeForm.email"
            type="email"
            placeholder="请输入注册时使用的邮箱"
            autocomplete="email"
            :disabled="submitting"
            @input="clearCodeFieldError('email')"
          />
          <p v-if="codeFieldErrors.email" class="login-field-error">{{ codeFieldErrors.email }}</p>
        </label>

        <!-- 验证码 -->
        <label class="login-field">
          <span>验证码</span>
          <div class="login-code-wrap">
            <input
              v-model="codeForm.code"
              type="text"
              maxlength="6"
              placeholder="请输入6位验证码"
              autocomplete="one-time-code"
              :disabled="submitting"
              @input="clearCodeFieldError('code')"
            />
            <button
              type="button"
              class="login-send-code-btn"
              :disabled="sendingCode || codeCountdown > 0 || !codeForm.email.includes('@')"
              @click="handleSendCode"
            >
              <span v-if="sendingCode" class="login-spinner small-spinner"></span>
              {{ codeCountdown > 0 ? `${codeCountdown}s` : '发送验证码' }}
            </button>
          </div>
          <p v-if="codeFieldErrors.code" class="login-field-error">{{ codeFieldErrors.code }}</p>
        </label>

        <!-- 成功提示 -->
        <div v-if="codeSuccessMessage" class="login-success">{{ codeSuccessMessage }}</div>

        <!-- 错误提示 -->
        <div v-if="errorMessage" class="login-error">{{ errorMessage }}</div>

        <!-- 提交按钮 -->
        <button type="submit" class="login-submit-btn" :disabled="submitting || !codeForm.code">
          <span v-if="submitting" class="login-spinner"></span>
          {{ submitting ? '登录中...' : '登录' }}
        </button>
      </form>

      <div class="login-footer">
        <span>还没有账号？<router-link to="/register">立即注册</router-link></span>
      </div>
    </div>
  </div>
</template>
<script setup>
import { reactive, ref, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuth } from '../useAuth'
import { apiPost, setTokens } from '../api'

const router = useRouter()
const route = useRoute()
const { login, currentUser } = useAuth()

// 登录方式：'password' | 'code'
const loginMode = ref('password')

// ---- 密码登录表单 ----
const form = reactive({
  account: '',
  password: '',
  remember: false,
})

const showPassword = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const fieldErrors = reactive({
  account: '',
  password: '',
})

function clearFieldError(field) {
  fieldErrors[field] = ''
  errorMessage.value = ''
}

function validate() {
  let valid = true
  fieldErrors.account = ''
  fieldErrors.password = ''

  if (!form.account.trim()) {
    fieldErrors.account = '请输入用户名或邮箱'
    valid = false
  }

  if (!form.password) {
    fieldErrors.password = '请输入密码'
    valid = false
  } else if (form.password.length < 8) {
    fieldErrors.password = '密码长度不能少于 8 位'
    valid = false
  }

  return valid
}

async function handleLogin() {
  if (submitting.value) return
  if (!validate()) return

  submitting.value = true
  errorMessage.value = ''

  const result = await login(form.account, form.password, form.remember)

  if (result.success) {
    const redirect = route.query.redirect || '/'
    router.push(redirect)
  } else {
    errorMessage.value = result.error || '登录失败，请重试'
  }

  submitting.value = false
}

// ---- 验证码登录表单 ----
const codeForm = reactive({
  email: '',
  code: '',
})

const codeFieldErrors = reactive({
  email: '',
  code: '',
})

const codeSuccessMessage = ref('')
const sendingCode = ref(false)
const codeCountdown = ref(0)
let countdownTimer = null

function clearCodeFieldError(field) {
  codeFieldErrors[field] = ''
  errorMessage.value = ''
  codeSuccessMessage.value = ''
}

function validateCodeForm() {
  let valid = true
  codeFieldErrors.email = ''
  codeFieldErrors.code = ''

  if (!codeForm.email.trim() || !codeForm.email.includes('@')) {
    codeFieldErrors.email = '请输入有效的邮箱地址'
    valid = false
  }

  if (!codeForm.code) {
    codeFieldErrors.code = '请输入验证码'
    valid = false
  } else if (codeForm.code.length !== 6) {
    codeFieldErrors.code = '验证码为 6 位数字'
    valid = false
  }

  return valid
}

async function handleSendCode() {
  if (sendingCode.value || codeCountdown.value > 0) return
  if (!codeForm.email.trim() || !codeForm.email.includes('@')) {
    codeFieldErrors.email = '请输入有效的邮箱地址'
    return
  }

  sendingCode.value = true
  codeSuccessMessage.value = ''
  errorMessage.value = ''

  try {
    const data = await apiPost('/api/auth/send-code', {
      email: codeForm.email.trim(),
    })

    if (data.status === 'success') {
      codeSuccessMessage.value = data.message || '验证码已发送，请查收邮件'
      // 开始倒计时
      codeCountdown.value = 60
      countdownTimer = setInterval(() => {
        codeCountdown.value--
        if (codeCountdown.value <= 0) {
          clearInterval(countdownTimer)
          countdownTimer = null
        }
      }, 1000)
    }
  } catch (error) {
    errorMessage.value = error.message || '验证码发送失败，请重试'
  } finally {
    sendingCode.value = false
  }
}

async function handleCodeLogin() {
  if (submitting.value) return
  if (!validateCodeForm()) return

  submitting.value = true
  errorMessage.value = ''
  codeSuccessMessage.value = ''

  try {
    const data = await apiPost('/api/auth/login-by-code', {
      email: codeForm.email.trim(),
      code: codeForm.code,
    })

    if (data.status === 'success') {
      // 保存 token 和用户信息
      setTokens(data.access_token, data.refresh_token)
      localStorage.setItem('user', JSON.stringify(data.user))

      // 更新 useAuth 的响应式状态，让路由守卫立即识别已登录
      currentUser.value = data.user

      const redirect = route.query.redirect || '/'
      router.push(redirect)
    }
  } catch (error) {
    errorMessage.value = error.message || '登录失败，请重试'
  } finally {
    submitting.value = false
  }
}

// 组件卸载时清除定时器
onBeforeUnmount(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
})
</script>
<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(circle at top left, rgba(42, 109, 255, 0.10), transparent 34%),
    linear-gradient(135deg, #eef3f8 0%, #f7fafc 100%);
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(211, 222, 235, 0.9);
  border-radius: 24px;
  padding: 36px 32px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.10);
  backdrop-filter: blur(18px);
}

.login-header {
  text-align: center;
  margin-bottom: 28px;
}

.login-logo {
  font-size: 48px;
  margin-bottom: 8px;
}

.login-header h2 {
  font-size: 20px;
  color: #0f172a;
  margin: 0 0 6px;
}

.login-subtitle {
  color: #64748b;
  font-size: 14px;
  margin: 0;
}

/* ---- 登录方式切换标签 ---- */
.login-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 24px;
  border: 1px solid #d7e0ea;
  border-radius: 12px;
  overflow: hidden;
}

.login-tab {
  flex: 1;
  padding: 10px;
  border: none;
  background: #f8fafc;
  color: #64748b;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.login-tab.active {
  background: #2563eb;
  color: white;
}

.login-tab:not(.active):hover {
  background: #eef2f6;
}

/* ---- 表单 ---- */
.login-form {
  display: grid;
  gap: 18px;
}

.login-field {
  display: grid;
  gap: 6px;
}

.login-field > span {
  font-size: 13px;
  font-weight: 700;
  color: #506177;
}

.login-field input[type="text"],
.login-field input[type="password"],
.login-field input[type="email"] {
  border: 1px solid #d7e0ea;
  border-radius: 10px;
  padding: 11px 12px;
  background: #ffffff;
  color: #162033;
  outline: none;
  font-size: 15px;
  width: 100%;
}

.login-field input:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.login-field input:disabled {
  background: #f1f5f9;
  cursor: not-allowed;
}

.login-password-wrap {
  display: flex;
  gap: 8px;
}

.login-password-wrap input {
  flex: 1;
}

.login-toggle-pw {
  white-space: nowrap;
  padding: 8px 14px;
  border-radius: 10px;
  font-size: 13px;
  background: #f1f5f9;
  color: #506177;
  box-shadow: none;
}

.login-toggle-pw:hover {
  background: #e2e8f0;
}

.login-field-error {
  color: #dc2626;
  font-size: 12px;
  margin: 2px 0 0;
}

.login-remember {
  margin: 0;
}

.login-error {
  padding: 12px 14px;
  border-radius: 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
  font-size: 14px;
  font-weight: 600;
}

.login-success {
  padding: 12px 14px;
  border-radius: 12px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #15803d;
  font-size: 14px;
  font-weight: 600;
}

.login-submit-btn {
  width: 100%;
  padding: 13px;
  font-size: 16px;
  border-radius: 12px;
  background: #2563eb;
  color: white;
  border: 0;
  cursor: pointer;
  font-weight: 700;
  box-shadow: 0 10px 22px rgba(37, 99, 235, 0.18);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.login-submit-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 14px 26px rgba(37, 99, 235, 0.22);
}

.login-submit-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.login-spinner {
  display: inline-block;
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: login-spin 0.6s linear infinite;
}

.small-spinner {
  width: 14px;
  height: 14px;
  border-width: 2px;
}

@keyframes login-spin {
  to { transform: rotate(360deg); }
}

/* ---- 验证码输入行 ---- */
.login-code-wrap {
  display: flex;
  gap: 8px;
}

.login-code-wrap input {
  flex: 1;
}

.login-send-code-btn {
  white-space: nowrap;
  padding: 8px 14px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  background: #2563eb;
  color: white;
  box-shadow: none;
  border: none;
  cursor: pointer;
  transition: background 0.2s ease;
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 100px;
  justify-content: center;
}

.login-send-code-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.login-send-code-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

/* ---- 页脚 ---- */
.login-footer {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
}

.login-footer a {
  color: #2563eb;
  text-decoration: none;
  font-weight: 600;
}

.login-footer a:hover {
  text-decoration: underline;
}
</style>


