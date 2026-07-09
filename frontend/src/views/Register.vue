<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <div class="login-logo">&#x1f698;</div>
        <h2>注册新账号</h2>
        <p class="login-subtitle">创建账号以使用智能车载系统</p>
      </div>

      <form class="login-form" @submit.prevent="handleRegister">
        <!-- 用户名 -->
        <label class="login-field">
          <span>用户名</span>
          <input
            v-model="form.username"
            type="text"
            placeholder="2-20位字母、数字、下划线或中文"
            autocomplete="username"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.username" class="login-field-error">{{ fieldErrors.username }}</p>
        </label>

        <!-- 邮箱 -->
        <label class="login-field">
          <span>邮箱</span>
          <input
            v-model="form.email"
            type="email"
            placeholder="请输入邮箱地址"
            autocomplete="email"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.email" class="login-field-error">{{ fieldErrors.email }}</p>
        </label>

        <!-- 密码 -->
        <label class="login-field">
          <span>密码</span>
          <div class="login-password-wrap">
            <input
              v-model="form.password"
              :type="showPassword ? 'text' : 'password'"
              placeholder="最少8位，含大小写字母和数字"
              autocomplete="new-password"
              :disabled="submitting"
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

        <!-- 确认密码 -->
        <label class="login-field">
          <span>确认密码</span>
          <input
            v-model="form.confirmPassword"
            type="password"
            placeholder="再次输入密码"
            autocomplete="new-password"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.confirmPassword" class="login-field-error">{{ fieldErrors.confirmPassword }}</p>
        </label>

        <!-- 成功提示 -->
        <div v-if="successMessage" class="login-success">{{ successMessage }}</div>

        <!-- 错误提示 -->
        <div v-if="errorMessage" class="login-error">{{ errorMessage }}</div>

        <!-- 提交按钮 -->
        <button type="submit" class="login-submit-btn" :disabled="submitting">
          <span v-if="submitting" class="login-spinner"></span>
          {{ submitting ? '注册中...' : '注册' }}
        </button>
      </form>

      <div class="login-footer">
        已有账号？
        <router-link to="/login">立即登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiPost } from '../api'

const router = useRouter()

const form = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

const showPassword = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const fieldErrors = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

function validate() {
  let valid = true
  fieldErrors.username = ''
  fieldErrors.email = ''
  fieldErrors.password = ''
  fieldErrors.confirmPassword = ''

  if (!form.username.trim() || form.username.trim().length < 2) {
    fieldErrors.username = '用户名至少需要2个字符'
    valid = false
  } else if (form.username.length > 20) {
    fieldErrors.username = '用户名不能超过20个字符'
    valid = false
  }

  if (!form.email.trim() || !form.email.includes('@')) {
    fieldErrors.email = '请输入有效的邮箱地址'
    valid = false
  }

  if (!form.password) {
    fieldErrors.password = '请输入密码'
    valid = false
  } else if (form.password.length < 8) {
    fieldErrors.password = '密码长度不能少于8位'
    valid = false
  } else if (!/[A-Z]/.test(form.password)) {
    fieldErrors.password = '密码必须包含大写字母'
    valid = false
  } else if (!/[a-z]/.test(form.password)) {
    fieldErrors.password = '密码必须包含小写字母'
    valid = false
  } else if (!/\d/.test(form.password)) {
    fieldErrors.password = '密码必须包含数字'
    valid = false
  }

  if (form.password !== form.confirmPassword) {
    fieldErrors.confirmPassword = '两次输入的密码不一致'
    valid = false
  }

  return valid
}

async function handleRegister() {
  if (submitting.value) return
  if (!validate()) return

  submitting.value = true
  errorMessage.value = ''
  successMessage.value = ''

  try {
    const data = await apiPost('/api/auth/register', {
      username: form.username.trim(),
      email: form.email.trim(),
      password: form.password,
    })

    if (data.status === 'success') {
      successMessage.value = '注册成功！即将跳转到登录页...'
      setTimeout(() => {
        router.push('/login')
      }, 1500)
    }
  } catch (error) {
    errorMessage.value = error.message || '注册失败，请重试'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-success {
  padding: 12px 14px;
  border-radius: 12px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #15803d;
  font-size: 14px;
  font-weight: 600;
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
