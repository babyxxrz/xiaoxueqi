<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="login-logo">&#x1f698;</div>
        <h2>智能车载视觉感知与告警系统</h2>
        <p class="login-subtitle">请登录以继续操作</p>
      </div>

      <form class="login-form" @submit.prevent="handleLogin">
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

      <div class="login-footer">
        <span>默认管理员：admin / Admin123!</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuth } from '../useAuth'

const router = useRouter()
const route = useRoute()
const { login } = useAuth()

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
.login-field input[type="password"] {
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

@keyframes login-spin {
  to { transform: rotate(360deg); }
}

.login-footer {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
}
</style>
