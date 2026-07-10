<template>
  <header class="topbar">
    <div class="topbar-left">
      <h1 class="topbar-title">&#x1f698; 智能车载视觉感知与告警系统</h1>
      <p class="topbar-subtitle">多源视频流融合 · 车牌识别 · 手势识别 · 实时告警</p>
    </div>
    <div class="topbar-right">
      <!-- 已登录：用户菜单 -->
      <div v-if="isAuthenticated" class="user-menu-wrap">
        <button class="user-menu-trigger" @click="toggleMenu">
          <span class="user-avatar">{{ userInitial }}</span>
          <span class="user-name">{{ currentUser?.username }}</span>
          <span :class="['role-tag', currentUser?.role === 'admin' ? 'role-admin' : 'role-user']">
            {{ currentUser?.role === 'admin' ? '管理员' : '用户' }}
          </span>
          <span class="user-arrow">▾</span>
        </button>

        <!-- 下拉菜单 -->
        <div v-if="menuOpen" class="user-dropdown" @click.stop>
          <router-link to="/admin" class="dropdown-item" v-if="isAdmin" @click="closeMenu">
            &#x2699;&#xfe0f; 管理后台
          </router-link>
          <div class="dropdown-divider" v-if="isAdmin"></div>
          <button class="dropdown-item dropdown-logout" @click="handleLogout">
            &#x1f6aa; 退出登录
          </button>
        </div>
      </div>

      <!-- 未登录：登录入口 -->
      <router-link v-else to="/login" class="topbar-login-btn">登录</router-link>
    </div>
  </header>

  <!-- 点击外部关闭下拉菜单 -->
  <div v-if="menuOpen" class="menu-overlay" @click="closeMenu"></div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../useAuth'

const router = useRouter()
const { currentUser, isAuthenticated, isAdmin, logout } = useAuth()

const menuOpen = ref(false)

const userInitial = computed(() => {
  const name = currentUser.value?.username || ''
  return name.charAt(0).toUpperCase()
})

function toggleMenu() {
  menuOpen.value = !menuOpen.value
}

function closeMenu() {
  menuOpen.value = false
}

async function handleLogout() {
  closeMenu()
  await logout()
  router.push('/login')
}

// Esc 关闭下拉菜单
function onKeydown(e) {
  if (e.key === 'Escape') closeMenu()
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
/* 下拉菜单覆盖层 */
.menu-overlay {
  position: fixed;
  inset: 0;
  z-index: 99;
}

/* 用户菜单 */
.user-menu-wrap {
  position: relative;
}

.user-menu-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.84);
  border: 1px solid rgba(211, 222, 235, 0.9);
  border-radius: 14px;
  padding: 8px 16px 8px 10px;
  cursor: pointer;
  color: #0f172a;
  font-weight: 600;
  font-size: 14px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
  transition: box-shadow 0.15s ease;
}

.user-menu-trigger:hover {
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.10);
}

.user-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #2563eb;
  color: white;
  font-size: 14px;
  font-weight: 800;
}

.user-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.role-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
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

.user-arrow {
  font-size: 11px;
  color: #94a3b8;
  margin-left: 2px;
}

/* 下拉菜单 */
.user-dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 180px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(211, 222, 235, 0.9);
  border-radius: 16px;
  padding: 8px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.14);
  backdrop-filter: blur(18px);
  z-index: 100;
}

.dropdown-item {
  display: block;
  width: 100%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  color: #162033;
  text-decoration: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-weight: 500;
  border: 0;
  transition: background 0.12s ease;
}

.dropdown-item:hover {
  background: #f1f5f9;
}

.dropdown-logout {
  color: #dc2626;
}

.dropdown-logout:hover {
  background: #fef2f2;
}

.dropdown-divider {
  height: 1px;
  background: #e2e8f0;
  margin: 6px 0;
}

/* 登录按钮 */
.topbar-login-btn {
  display: inline-block;
  padding: 10px 22px;
  background: #2563eb;
  color: white;
  border-radius: 12px;
  text-decoration: none;
  font-weight: 700;
  font-size: 14px;
  box-shadow: 0 10px 22px rgba(37, 99, 235, 0.18);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.topbar-login-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 26px rgba(37, 99, 235, 0.22);
}
</style>
