/**
 * 前端路由配置
 *
 * 路由表：
 *   /           → 首页（需登录）
 *   /login      → 登录页
 *   /register   → 注册页
 *   /forbidden  → 权限不足提示页
 *   /admin      → 管理后台（需 admin 角色）
 *
 * 使用 hash 模式，避免静态部署时刷新出现 404。
 * 路由守卫校验：未登录 → 跳转 /login；角色不足 → 跳转 /forbidden
 */
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuth } from './useAuth'

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
    meta: { requiresAuth: true, title: '智能交通识别系统' },
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
    meta: { requiresAuth: true, title: '403 无权限' },
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

router.beforeEach(async (to) => {
  document.title = to.meta.title || '智能交通识别系统'

  const auth = useAuth()
  await auth.init()

  if (to.meta.guest && auth.isAuthenticated.value) {
    return auth.isAdmin.value ? '/admin' : '/'
  }

  if (to.meta.requiresAuth && !auth.isAuthenticated.value) {
    return {
      path: '/login',
      query: { redirect: to.fullPath },
    }
  }

  if (to.meta.requiresAdmin && !auth.isAdmin.value) {
    return '/403'
  }

  return true
})

export default router
