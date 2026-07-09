import { createApp, h } from 'vue'
import { RouterView } from 'vue-router'
import './style.css'
import router from './router'
import { useAuth } from './useAuth'

// 根组件仅包含 <router-view />，路由决定渲染哪个页面
const app = createApp({
  render() {
    return h(RouterView)
  },
})

app.use(router)

// 初始化认证状态（从 localStorage 恢复会话）
const { init } = useAuth()
init()

app.mount('#app')
