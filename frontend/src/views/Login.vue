<template>
  <main class="auth-page">
    <section class="auth-card login-card-wide">
      <div class="auth-brand">
        <div class="auth-brand-mark">AI</div>
        <div>
          <h1>智能交通识别系统</h1>
          <p>用户业务端与管理员数据端统一认证入口</p>
        </div>
      </div>

      <div class="login-tabs" role="tablist">
        <button
          type="button"
          :class="{ active: loginMode === 'password' }"
          @click="switchMode('password')"
        >
          密码登录
        </button>
        <button
          type="button"
          :class="{ active: loginMode === 'code' }"
          @click="switchMode('code')"
        >
          邮箱验证码登录
        </button>
      </div>

      <form v-if="loginMode === 'password'" class="auth-form" @submit.prevent="handlePasswordLogin">
        <label class="auth-field">
          <span>用户名或邮箱</span>
          <input
            v-model.trim="passwordForm.account"
            autocomplete="username"
            placeholder="请输入用户名或邮箱"
            :disabled="submitting"
          />
        </label>

        <label class="auth-field">
          <span>密码</span>
          <div class="password-row">
            <input
              v-model="passwordForm.password"
              :type="showPassword ? 'text' : 'password'"
              autocomplete="current-password"
              placeholder="请输入密码"
              :disabled="submitting"
            />
            <button type="button" @click="showPassword = !showPassword">
              {{ showPassword ? '隐藏' : '显示' }}
            </button>
          </div>
        </label>

        <label class="remember-row">
          <input v-model="passwordForm.remember" type="checkbox" />
          <span>记住登录状态</span>
        </label>

        <div v-if="errorMessage" class="auth-error">{{ errorMessage }}</div>

        <button class="auth-submit" type="submit" :disabled="submitting">
          <span v-if="submitting" class="loading-dot"></span>
          {{ submitting ? '登录中...' : '登录系统' }}
        </button>
      </form>

      <form v-else class="auth-form" @submit.prevent="handleCodeLogin">
        <label class="auth-field">
          <span>注册邮箱</span>
          <input
            v-model.trim="codeForm.email"
            type="email"
            autocomplete="email"
            placeholder="请输入已注册邮箱"
            :disabled="submitting"
          />
        </label>

        <label class="auth-field">
          <span>邮箱验证码</span>
          <div class="code-row">
            <input
              v-model.trim="codeForm.code"
              inputmode="numeric"
              maxlength="6"
              autocomplete="one-time-code"
              placeholder="请输入 6 位验证码"
              :disabled="submitting"
            />
            <button
              type="button"
              :disabled="sendingCode || countdown > 0 || submitting"
              @click="handleSendCode"
            >
              {{ sendCodeButtonText }}
            </button>
          </div>
        </label>

        <label class="remember-row">
          <input v-model="codeForm.remember" type="checkbox" />
          <span>记住登录状态</span>
        </label>

        <div v-if="successMessage" class="auth-success">{{ successMessage }}</div>
        <div v-if="errorMessage" class="auth-error">{{ errorMessage }}</div>

        <button class="auth-submit" type="submit" :disabled="submitting">
          <span v-if="submitting" class="loading-dot"></span>
          {{ submitting ? '验证中...' : '验证码登录' }}
        </button>
      </form>

      <div class="auth-footer">
        还没有账号？
        <router-link to="/register">注册新账号</router-link>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../useAuth'

const route = useRoute()
const router = useRouter()
const auth = useAuth()

const loginMode = ref('password')
const showPassword = ref(false)
const submitting = ref(false)
const sendingCode = ref(false)
const countdown = ref(0)
const countdownTimer = ref(null)
const errorMessage = ref('')
const successMessage = ref('')

const passwordForm = reactive({
  account: '',
  password: '',
  remember: false,
})

const codeForm = reactive({
  email: '',
  code: '',
  remember: false,
})

const sendCodeButtonText = computed(() => {
  if (sendingCode.value) return '发送中...'
  if (countdown.value > 0) return `${countdown.value} 秒后重试`
  return '获取验证码'
})

function switchMode(mode) {
  loginMode.value = mode
  errorMessage.value = ''
  successMessage.value = ''
}

function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
}

function startCountdown() {
  countdown.value = 60

  if (countdownTimer.value) {
    window.clearInterval(countdownTimer.value)
  }

  countdownTimer.value = window.setInterval(() => {
    countdown.value -= 1

    if (countdown.value <= 0) {
      window.clearInterval(countdownTimer.value)
      countdownTimer.value = null
    }
  }, 1000)
}

async function handleSendCode() {
  if (!validateEmail(codeForm.email)) {
    errorMessage.value = '请输入有效的注册邮箱'
    return
  }

  sendingCode.value = true
  errorMessage.value = ''
  successMessage.value = ''

  try {
    const result = await auth.sendLoginCode(codeForm.email)
    successMessage.value = result.message || '验证码已发送，请查收邮箱'
    startCountdown()
  } catch (error) {
    errorMessage.value = error.message || '验证码发送失败'
  } finally {
    sendingCode.value = false
  }
}

async function finishLogin(result) {
  if (!result.success) {
    errorMessage.value = result.error || '登录失败'
    return
  }

  const redirect = String(route.query.redirect || '')

  if (redirect && redirect !== '/login') {
    await router.replace(redirect)
    return
  }

  await router.replace(auth.isAdmin.value ? '/admin' : '/')
}

async function handlePasswordLogin() {
  if (!passwordForm.account || !passwordForm.password) {
    errorMessage.value = '请输入账号和密码'
    return
  }

  submitting.value = true
  errorMessage.value = ''
  successMessage.value = ''

  try {
    const result = await auth.login(
      passwordForm.account,
      passwordForm.password,
      passwordForm.remember,
    )
    await finishLogin(result)
  } finally {
    submitting.value = false
  }
}

async function handleCodeLogin() {
  if (!validateEmail(codeForm.email)) {
    errorMessage.value = '请输入有效的注册邮箱'
    return
  }

  if (!/^\d{6}$/.test(codeForm.code)) {
    errorMessage.value = '请输入 6 位数字验证码'
    return
  }

  submitting.value = true
  errorMessage.value = ''
  successMessage.value = ''

  try {
    const result = await auth.loginByCode(
      codeForm.email,
      codeForm.code,
      codeForm.remember,
    )
    await finishLogin(result)
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => {
  if (countdownTimer.value) {
    window.clearInterval(countdownTimer.value)
  }
})
</script>

<style scoped>
.login-card-wide {
  width: min(500px, 100%);
}

.login-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 20px;
  padding: 5px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1424;
}

.login-tabs button {
  padding: 10px 12px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #94a3b8;
  font-weight: 800;
}

.login-tabs button.active {
  background: #1d4ed8;
  color: #ffffff;
}

.password-row,
.code-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 9px;
}

.password-row button,
.code-row button {
  min-width: 96px;
  padding: 0 12px;
  border: 1px solid #334155;
  border-radius: 12px;
  background: #172033;
  color: #cbd5e1;
  font-weight: 800;
}

.password-row button:disabled,
.code-row button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.remember-row {
  display: flex;
  gap: 8px;
  align-items: center;
  color: #94a3b8;
  font-size: 13px;
}

.remember-row input {
  width: 16px;
  height: 16px;
}
</style>
