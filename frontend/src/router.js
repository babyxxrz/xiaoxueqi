/**
 * 路由配置
 * 使用 hash 模式避免开发环境和静态部署刷新 404。
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
