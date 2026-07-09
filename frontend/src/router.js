/**
 * 路由配置
 * 使用 hash 模式避免开发环境下刷新 404
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuth } from './useAuth'

// 懒加载页面组件
const LoginPage = () => import('./views/Login.vue')
const RegisterPage = () => import('./views/Register.vue')
const AdminPage = () => import('./views/Admin.vue')
const ForbiddenPage = () => import('./views/Forbidden.vue')
const MainApp = () => import('./App.vue')

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: LoginPage,
    meta: { guest: true, title: '登录' },
  },
  {
    path: '/register',
    name: 'Register',
    component: RegisterPage,
    meta: { guest: true, title: '注册' },
  },
  {
    path: '/',
    name: 'Home',
    component: MainApp,
    meta: { requiresAuth: true, title: '智能车载视觉感知与告警系统' },
  },
  {
    path: '/admin',
    name: 'Admin',
    component: AdminPage,
    meta: { requiresAuth: true, requiresAdmin: true, title: '管理后台' },
  },
  {
    path: '/403',
    name: 'Forbidden',
    component: ForbiddenPage,
    meta: { title: '403 无权限' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to, from) => {
  // 更新页面标题
  document.title = to.meta.title || '智能车载视觉感知与告警系统'

  const { isAuthenticated, isAdmin } = useAuth()

  // 已登录用户访问登录页 → 跳转首页
  if (to.meta.guest && isAuthenticated.value) {
    return '/'
  }

  // 需要登录的页面 → 未登录跳转登录页
  if (to.meta.requiresAuth && !isAuthenticated.value) {
    return {
      path: '/login',
      query: { redirect: to.fullPath },
    }
  }

  // 需要 admin 权限 → 非 admin 跳转 403
  if (to.meta.requiresAdmin && !isAdmin.value) {
    return '/403'
  }

  // 允许通过
  return true
})

export default router
