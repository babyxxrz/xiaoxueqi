<template>
  <main class="auth-page">
    <section class="auth-card">
      <div class="auth-brand">
        <div class="auth-brand-mark">AI</div>
        <div>
          <h2>注册新账号</h2>
          <p>创建普通用户账号，管理员角色由后台分配</p>
        </div>
      </div>

      <form class="auth-form" @submit.prevent="handleRegister">
        <label class="auth-field">
          <span>用户名</span>
          <input
            v-model.trim="form.username"
            autocomplete="username"
            placeholder="2—20 位字符"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.username" class="field-error">{{ fieldErrors.username }}</p>
        </label>

        <label class="auth-field">
          <span>邮箱</span>
          <input
            v-model.trim="form.email"
            type="email"
            autocomplete="email"
            placeholder="请输入邮箱"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.email" class="field-error">{{ fieldErrors.email }}</p>
        </label>

        <label class="auth-field">
          <span>密码</span>
          <input
            v-model="form.password"
            type="password"
            autocomplete="new-password"
            placeholder="至少 8 位，包含大小写字母和数字"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.password" class="field-error">{{ fieldErrors.password }}</p>
        </label>

        <label class="auth-field">
          <span>确认密码</span>
          <input
            v-model="form.confirmPassword"
            type="password"
            autocomplete="new-password"
            placeholder="再次输入密码"
            :disabled="submitting"
          />
          <p v-if="fieldErrors.confirmPassword" class="field-error">
            {{ fieldErrors.confirmPassword }}
          </p>
        </label>

        <div v-if="successMessage" class="auth-success">{{ successMessage }}</div>
        <div v-if="errorMessage" class="auth-error">{{ errorMessage }}</div>

        <button class="auth-submit" type="submit" :disabled="submitting">
          <span v-if="submitting" class="loading-dot"></span>
          {{ submitting ? '注册中...' : '注册账号' }}
        </button>
      </form>

      <div class="auth-footer">
        已有账号？
        <router-link to="/login">返回登录</router-link>
      </div>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiPost } from '../api'

const router = useRouter()
const submitting = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

const form = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

const fieldErrors = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

function validate() {
  Object.keys(fieldErrors).forEach((key) => {
    fieldErrors[key] = ''
  })

  let valid = true

  if (form.username.length < 2 || form.username.length > 20) {
    fieldErrors.username = '用户名长度应为 2—20 位'
    valid = false
  }

  if (!form.email.includes('@')) {
    fieldErrors.email = '请输入有效邮箱'
    valid = false
  }

  if (
    form.password.length < 8 ||
    !/[A-Z]/.test(form.password) ||
    !/[a-z]/.test(form.password) ||
    !/\d/.test(form.password)
  ) {
    fieldErrors.password = '密码至少 8 位，并包含大小写字母和数字'
    valid = false
  }

  if (form.password !== form.confirmPassword) {
    fieldErrors.confirmPassword = '两次输入的密码不一致'
    valid = false
  }

  return valid
}

async function handleRegister() {
  if (!validate() || submitting.value) return

  submitting.value = true
  errorMessage.value = ''
  successMessage.value = ''

  try {
    await apiPost(
      '/api/auth/register',
      {
        username: form.username,
        email: form.email,
        password: form.password,
      },
      { auth: false },
    )

    successMessage.value = '注册成功，即将返回登录页'
    window.setTimeout(() => router.push('/login'), 1000)
  } catch (error) {
    errorMessage.value = error.message || '注册失败'
  } finally {
    submitting.value = false
  }
}
</script>
