import { createApp, h } from 'vue'
import { RouterView } from 'vue-router'
import './style.css'
import router from './router'
import { useAuth } from './useAuth'

const app = createApp({
  render() {
    return h(RouterView)
  },
})

app.use(router)

const { init } = useAuth()
init()

app.mount('#app')
