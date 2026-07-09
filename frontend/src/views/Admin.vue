<template>
  <div class="admin-page">
    <header class="admin-header">
      <h2>管理后台</h2>
      <p>用户管理与系统设置</p>
    </header>

    <section class="panel">
      <div class="panel-header">
        <h3>用户列表</h3>
        <span class="admin-user-count">共 {{ users.length }} 个用户</span>
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="empty-state">
        <p>加载中...</p>
      </div>

      <!-- 错误 -->
      <div v-else-if="errorMsg" class="admin-error">{{ errorMsg }}</div>

      <!-- 用户表格 -->
      <div v-else-if="users.length" class="table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>用户名</th>
              <th>邮箱</th>
              <th>角色</th>
              <th>注册时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td>{{ user.id }}</td>
              <td>{{ user.username }}</td>
              <td>{{ user.email }}</td>
              <td>
                <span :class="['role-badge', user.role === 'admin' ? 'role-admin' : 'role-user']">
                  {{ user.role === 'admin' ? '管理员' : '普通用户' }}
                </span>
              </td>
              <td>{{ user.created_at }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 空状态 -->
      <div v-else class="empty-state">
        <p>暂无用户数据</p>
      </div>
    </section>

    <!-- 返回首页 -->
    <div class="admin-back">
      <router-link to="/">← 返回首页</router-link>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAuth } from '../useAuth'

const { currentUser } = useAuth()

const users = ref([])
const loading = ref(true)
const errorMsg = ref('')

async function loadUsers() {
  loading.value = true
  errorMsg.value = ''
  try {
    // 目前通过现有接口获取数据，后续可以添加专门的用户管理接口
    // 这里先展示当前用户信息和模拟数据
    users.value = [
      {
        id: 1,
        username: 'admin',
        email: 'admin@xiaoxueqi.local',
        role: 'admin',
        created_at: '2025-01-01 00:00:00',
      },
    ]
    if (currentUser.value && currentUser.value.id !== 1) {
      users.value.push(currentUser.value)
    }
  } catch (error) {
    errorMsg.value = error.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadUsers()
})
</script>

<style scoped>
.admin-page {
  width: min(1480px, calc(100vw - 48px));
  margin: 0 auto;
  padding: 28px 0 48px;
}

.admin-header {
  margin-bottom: 20px;
}

.admin-header h2 {
  font-size: 28px;
  margin-bottom: 4px;
  color: #0f172a;
}

.admin-header p {
  color: #64748b;
  margin: 0;
}

.admin-user-count {
  font-size: 13px;
  color: #64748b;
}

.admin-error {
  padding: 12px 14px;
  border-radius: 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
  font-size: 14px;
}

.admin-table {
  width: 100%;
  border-collapse: collapse;
}

.admin-table th,
.admin-table td {
  text-align: left;
  padding: 12px 16px;
  border-bottom: 1px solid #e2e8f0;
  font-size: 14px;
}

.admin-table th {
  font-weight: 700;
  color: #506177;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.admin-table tbody tr:hover {
  background: #f8fafc;
}

.role-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.role-admin {
  background: #ede9fe;
  color: #7c3aed;
}

.role-user {
  background: #dbeafe;
  color: #2563eb;
}

.admin-back {
  margin-top: 16px;
}

.admin-back a {
  color: #2563eb;
  text-decoration: none;
  font-weight: 600;
  font-size: 14px;
}

.admin-back a:hover {
  text-decoration: underline;
}
</style>
