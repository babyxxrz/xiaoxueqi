<template>
  <div class="admin-shell">
    <aside class="admin-sidebar">
      <div class="admin-brand">
        <div class="admin-mark">A</div>
        <div>
          <strong>系统管理中心</strong>
          <span>Administrator Console</span>
        </div>
      </div>

      <nav>
        <button
          v-for="item in navItems"
          :key="item.key"
          :class="{ active: activeView === item.key }"
          @click="activeView = item.key"
        >
          {{ item.label }}
        </button>
      </nav>

      <div class="admin-sidebar-foot">
        <router-link to="/">返回用户业务端</router-link>
        <button @click="handleLogout">退出登录</button>
      </div>
    </aside>

    <main class="admin-main">
      <header class="admin-topbar">
        <div>
          <p>ADMINISTRATION</p>
          <h1>{{ currentNav.label }}</h1>
        </div>

        <div class="admin-account">
          <span :class="{ online: healthOk }">{{ healthOk ? '服务正常' : '服务异常' }}</span>
          <strong>{{ auth.user.value?.username || '管理员' }}</strong>
          <button :disabled="loading" @click="refreshAll">{{ loading ? '刷新中' : '刷新数据' }}</button>
        </div>
      </header>

      <p v-if="errorMessage" class="admin-error">{{ errorMessage }}</p>

      <section v-if="activeView === 'overview'" class="admin-grid">
        <article v-for="metric in overviewMetrics" :key="metric.label" class="admin-metric">
          <span>{{ metric.label }}</span>
          <strong>{{ metric.value }}</strong>
          <small>{{ metric.note }}</small>
        </article>

        <article class="admin-panel span-8">
          <header>
            <div>
              <p>SYSTEM ACTIVITY</p>
              <h2>最近系统操作</h2>
            </div>
          </header>
          <div class="compact-list">
            <div v-for="item in logs.slice(0, 8)" :key="item.id || item.log_id">
              <strong>{{ item.action || item.event_type || '系统操作' }}</strong>
              <span>{{ item.username || item.user_name || `用户 ${item.user_id || '-'}` }}</span>
              <small>{{ item.created_at || item.created_time || '-' }}</small>
            </div>
            <div v-if="!logs.length" class="admin-empty">暂无操作日志。</div>
          </div>
        </article>

        <article class="admin-panel span-4">
          <header>
            <div>
              <p>RISK STATUS</p>
              <h2>告警分布</h2>
            </div>
          </header>
          <div class="risk-overview">
            <div>
              <span>严重</span>
              <strong>{{ alertCounts.high }}</strong>
            </div>
            <div>
              <span>警告</span>
              <strong>{{ alertCounts.medium }}</strong>
            </div>
            <div>
              <span>提示</span>
              <strong>{{ alertCounts.low }}</strong>
            </div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'users'" class="admin-grid">
        <article class="admin-panel span-12">
          <header>
            <div>
              <p>USER MANAGEMENT</p>
              <h2>用户与角色</h2>
              <span>管理员负责查看账号、角色和使用状态。用户数据隔离仍需后端增加 user_id 过滤。</span>
            </div>
          </header>

          <div v-if="usersEndpointUnavailable" class="admin-notice">
            当前后端尚未提供可用的用户列表接口。建议实现
            <code>GET /api/admin/users</code> 后，本页会自动加载。
          </div>

          <div class="admin-table">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>用户名</th>
                  <th>邮箱</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>注册时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in users" :key="item.id || item.user_id">
                  <td>{{ item.id || item.user_id }}</td>
                  <td>{{ item.username || '-' }}</td>
                  <td>{{ item.email || '-' }}</td>
                  <td><span class="role-badge">{{ item.role || item.user_role || 'user' }}</span></td>
                  <td>{{ item.status || (item.enabled === false ? '已禁用' : '正常') }}</td>
                  <td>{{ item.created_at || '-' }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="!users.length && !usersEndpointUnavailable" class="admin-empty">暂无用户数据。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'records'" class="admin-grid">
        <article class="admin-panel span-12">
          <header>
            <div>
              <p>RECOGNITION DATA</p>
              <h2>全局识别记录</h2>
            </div>
          </header>

<div class="admin-table">
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>用户</th>
        <th>任务</th>
        <th>输入</th>
        <th>文件</th>
        <th>时间</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="item in records" :key="item.id || item.record_id">
        <td>{{ item.id || item.record_id }}</td>
        <td>{{ item.username || item.user_id || '-' }}</td>
        <td>{{ item.task_type || '-' }}</td>
        <td>{{ item.input_type || '-' }}</td>
        <td>{{ item.original_filename || item.saved_filename || '-' }}</td>
        <td>{{ item.created_at || item.created_time || '-' }}</td>
      </tr>
    </tbody>
  </table>
  <div v-if="!records.length" class="admin-empty">暂无识别记录。</div>
</div>
        </article>

        <article class="admin-panel span-12">
          <header>
            <div>
              <p>OPERATION LOG</p>
              <h2>用户操作日志</h2>
            </div>
          </header>

<div class="admin-table">
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>用户</th>
        <th>操作</th>
        <th>详情</th>
        <th>时间</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="item in logs" :key="item.id || item.log_id">
        <td>{{ item.id || item.log_id }}</td>
        <td>{{ item.username || item.user_id || '-' }}</td>
        <td>{{ item.action || item.event_type || '-' }}</td>
        <td>{{ item.detail || item.message || '-' }}</td>
        <td>{{ item.created_at || item.created_time || '-' }}</td>
      </tr>
    </tbody>
  </table>
  <div v-if="!logs.length" class="admin-empty">暂无操作日志。</div>
</div>
        </article>
      </section>

      <section v-if="activeView === 'alerts'" class="admin-grid">
        <article class="admin-panel span-12">
          <header>
            <div>
              <p>ALERT AGENT</p>
              <h2>告警事件与处理状态</h2>
            </div>
          </header>

          <div class="admin-table">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>级别</th>
                  <th>类型</th>
                  <th>摘要</th>
                  <th>用户</th>
                  <th>时间</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in alerts" :key="item.id || item.alert_id">
                  <td>{{ item.id || item.alert_id }}</td>
                  <td><span :class="['severity', alertLevel(item)]">{{ alertLevelLabel(item) }}</span></td>
                  <td>{{ item.alert_type || item.event_type || item.type || '-' }}</td>
                  <td>{{ item.summary || item.description || item.message || '-' }}</td>
                  <td>{{ item.username || item.user_id || '-' }}</td>
                  <td>{{ item.created_at || item.created_time || '-' }}</td>
                  <td>{{ item.status || (item.resolved ? '已处理' : '待处理') }}</td>
                  <td>
                    <button
                      class="table-button"
                      :disabled="item.resolved || item.status === 'resolved'"
                      @click="resolveAlert(item)"
                    >
                      标记处理
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="!alerts.length" class="admin-empty">暂无告警。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'sources'" class="admin-grid">
        <article class="admin-panel span-5">
          <header>
            <div>
              <p>VIDEO SOURCE FORM</p>
              <h2>{{ sourceForm.id ? '编辑视频源' : '新增视频源' }}</h2>
            </div>
            <button class="light-button" @click="resetSourceForm">清空</button>
          </header>

          <div class="source-form">
            <label>
              名称
              <input v-model.trim="sourceForm.name" placeholder="例如：行车记录仪车牌流" />
            </label>
            <label>
              用途
              <select v-model="sourceForm.source_type">
                <option value="plate">车牌识别</option>
                <option value="traffic_gesture">交警手势识别</option>
                <option value="owner_gesture">车主手势识别</option>
                <option value="general">通用视频源</option>
              </select>
            </label>
            <label>
              协议
              <select v-model="sourceForm.protocol">
                <option value="rtsp">RTSP</option>
                <option value="mediamtx">MediaMTX</option>
                <option value="mock">Mock</option>
                <option value="demo">Demo</option>
              </select>
            </label>
            <label>
              源 ID
              <input v-model.trim="sourceForm.source_id" placeholder="plate / traffic / live12" />
            </label>
            <label>
              视频流地址
              <input v-model.trim="sourceForm.source_url" placeholder="rtsp://127.0.0.1:8554/plate" />
            </label>
            <label>
              Demo 文件
              <input v-model.trim="sourceForm.demo_file" placeholder="traffic.png" />
            </label>
            <label class="checkbox-label">
              <input v-model="sourceForm.use_mock_frame" type="checkbox" />
              使用 Mock / Demo 输入
            </label>
            <label class="checkbox-label">
              <input v-model="sourceForm.enabled" type="checkbox" />
              启用视频源
            </label>
            <label>
              说明
              <textarea v-model.trim="sourceForm.description" rows="3"></textarea>
            </label>
            <button class="primary-button" :disabled="sourceSaving" @click="saveSource">
              {{ sourceSaving ? '保存中...' : sourceForm.id ? '保存修改' : '新增视频源' }}
            </button>
          </div>
        </article>

        <article class="admin-panel span-7">
          <header>
            <div>
              <p>VIDEO SOURCE LIST</p>
              <h2>视频源配置</h2>
            </div>
          </header>

          <div class="source-admin-list">
            <article v-for="item in sources" :key="item.id">
              <div>
                <span>{{ item.source_type }}</span>
                <h3>{{ item.name }}</h3>
                <p>{{ item.source_url || item.demo_file || item.source_id || '-' }}</p>
              </div>
              <div class="source-list-actions">
                <button @click="testSource(item)">
                  {{ sourceCheckingId === item.id ? '检测中' : '检测' }}
                </button>
                <button @click="editSource(item)">编辑</button>
                <button class="danger" @click="removeSource(item)">删除</button>
                <small v-if="sourceCheckResults[item.id]">
                  {{ sourceCheckResults[item.id].online ? '可用' : '不可用' }}：
                  {{ sourceCheckResults[item.id].message }}
                </small>
              </div>
            </article>
            <div v-if="!sources.length" class="admin-empty">暂无视频源。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'runtime'" class="admin-grid">
        <article v-for="metric in runtimeMetrics" :key="metric.label" class="admin-metric">
          <span>{{ metric.label }}</span>
          <strong>{{ metric.value }}</strong>
          <small>{{ metric.note }}</small>
        </article>

        <article class="admin-panel span-12">
          <header>
            <div>
              <p>WORKER STATUS</p>
              <h2>多路处理状态</h2>
            </div>
          </header>
          <div class="worker-grid">
            <article v-for="worker in workers" :key="worker.worker_id">
              <span>{{ worker.task_type || 'worker' }}</span>
              <strong>{{ worker.worker_id }}</strong>
              <p>状态：{{ worker.status || '-' }}</p>
              <p>成功：{{ worker.success_count || 0 }} / 失败：{{ worker.fail_count || 0 }}</p>
              <p>延迟：{{ worker.last_latency_ms ?? '-' }} ms</p>
            </article>
            <div v-if="!workers.length" class="admin-empty">当前没有运行中的 Worker。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'api'" class="admin-grid">
        <article class="admin-panel span-12 api-panel">
          <p>OPENAPI DOCUMENTATION</p>
          <h2>接口文档与系统配置</h2>
          <p>FastAPI 自动生成 Swagger 和 OpenAPI 文档，结题时可导出并放入设计文档。</p>
          <div>
            <a href="http://127.0.0.1:8000/docs" target="_blank">打开 Swagger UI</a>
            <a href="http://127.0.0.1:8000/redoc" target="_blank">打开 ReDoc</a>
            <a href="http://127.0.0.1:8000/openapi.json" target="_blank">查看 OpenAPI JSON</a>
          </div>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiDelete, apiGet, apiPost, apiPut } from '../api'
import { useAuth } from '../useAuth'

const router = useRouter()
const auth = useAuth()

const navItems = [
  { key: 'overview', label: '管理总览' },
  { key: 'users', label: '用户与角色' },
  { key: 'records', label: '数据与日志' },
  { key: 'alerts', label: '告警管理' },
  { key: 'sources', label: '视频源管理' },
  { key: 'runtime', label: '运行监控' },
  { key: 'api', label: '接口文档' },
]

const activeView = ref('overview')
const loading = ref(false)
const errorMessage = ref('')
const healthOk = ref(false)

const users = ref([])
const usersEndpointUnavailable = ref(false)
const records = ref([])
const alerts = ref([])
const logs = ref([])
const sources = ref([])
const performanceSummary = ref({})
const multistreamStatus = ref({})

const sourceSaving = ref(false)
const sourceCheckingId = ref(null)
const sourceCheckResults = reactive({})

const sourceForm = reactive({
  id: null,
  source_key: '',
  name: '',
  source_type: 'plate',
  source_id: '',
  source_url: '',
  protocol: 'rtsp',
  use_mock_frame: false,
  demo_file: '',
  frame_count: 20,
  sample_interval: 5,
  warmup_frames: 3,
  enabled: true,
  description: '',
})

const currentNav = computed(
  () => navItems.find((item) => item.key === activeView.value) || navItems[0],
)

const workers = computed(() =>
  Object.values(multistreamStatus.value?.workers || multistreamStatus.value?.state?.workers || {}),
)

const alertCounts = computed(() => {
  const result = { high: 0, medium: 0, low: 0 }

  alerts.value.forEach((item) => {
    result[alertLevel(item)] += 1
  })

  return result
})

const overviewMetrics = computed(() => [
  {
    label: '注册用户',
    value: users.value.length || '-',
    note: usersEndpointUnavailable.value ? '等待用户管理接口' : '系统账号总量',
  },
  {
    label: '识别记录',
    value: records.value.length,
    note: '最近加载的全局记录',
  },
  {
    label: '告警事件',
    value: alerts.value.length,
    note: `${alertCounts.value.high} 条严重告警`,
  },
  {
    label: '视频源',
    value: sources.value.length,
    note: `${sources.value.filter((item) => item.enabled !== false).length} 个已启用`,
  },
])

const runtimeMetrics = computed(() => [
  {
    label: '后端服务',
    value: healthOk.value ? '正常' : '异常',
    note: 'FastAPI 健康检查',
  },
  {
    label: '平均延迟',
    value: `${performanceSummary.value.avg_latency_ms ?? '-'} ms`,
    note: '端到端平均响应时间',
  },
  {
    label: 'P95 延迟',
    value: `${performanceSummary.value.p95_latency_ms ?? '-'} ms`,
    note: '95% 请求延迟',
  },
  {
    label: '并发 Worker',
    value: workers.value.length,
    note: '当前多路处理通道',
  },
])

function normalizeList(data) {
  if (Array.isArray(data)) return data
  return data?.items || data?.records || data?.alerts || data?.logs || data?.users || []
}

async function loadUsers() {
  usersEndpointUnavailable.value = false

  const paths = ['/api/admin/users', '/api/users']

  for (const path of paths) {
    try {
      const data = await apiGet(path)
      users.value = normalizeList(data)
      return
    } catch (error) {
      if (![404, 405].includes(error.status)) {
        throw error
      }
    }
  }

  users.value = []
  usersEndpointUnavailable.value = true
}

async function refreshAll() {
  loading.value = true
  errorMessage.value = ''

  const tasks = await Promise.allSettled([
    apiGet('/api/health'),
    loadUsers(),
    apiGet('/api/records?limit=100'),
    apiGet('/api/alerts?limit=100'),
    apiGet('/api/logs?limit=100'),
    apiGet('/api/video-sources?enabled_only=false'),
    apiGet('/api/performance/summary'),
    apiGet('/api/multistream/status'),
  ])

  const [health, , recordData, alertData, logData, sourceData, performance, multistream] = tasks

  if (health.status === 'fulfilled') {
    healthOk.value = ['ok', 'success'].includes(health.value.status)
  }

  if (recordData.status === 'fulfilled') records.value = normalizeList(recordData.value)
  if (alertData.status === 'fulfilled') alerts.value = normalizeList(alertData.value)
  if (logData.status === 'fulfilled') logs.value = normalizeList(logData.value)
  if (sourceData.status === 'fulfilled') sources.value = normalizeList(sourceData.value)

  if (performance.status === 'fulfilled') {
    performanceSummary.value = performance.value?.summary || performance.value
  }

  if (multistream.status === 'fulfilled') {
    multistreamStatus.value = multistream.value?.state || multistream.value
  }

  const rejected = tasks.find(
    (result, index) => result.status === 'rejected' && index !== 1,
  )

  if (rejected) {
    errorMessage.value = `部分管理数据加载失败：${rejected.reason?.message || '未知错误'}`
  }

  loading.value = false
}

function alertLevel(item) {
  const value = String(item.level || item.severity || item.risk_level || 'low').toLowerCase()
  if (['critical', 'high', '严重'].includes(value)) return 'high'
  if (['warning', 'medium', '警告'].includes(value)) return 'medium'
  return 'low'
}

function alertLevelLabel(item) {
  const map = {
    high: '严重',
    medium: '警告',
    low: '提示',
  }
  return map[alertLevel(item)]
}

async function resolveAlert(item) {
  const id = item.id || item.alert_id
  if (!id) return

  try {
    await apiPost(`/api/alerts/${id}/resolve`, {})
    await refreshAll()
  } catch (error) {
    errorMessage.value = error.message
  }
}

function resetSourceForm() {
  Object.assign(sourceForm, {
    id: null,
    source_key: '',
    name: '',
    source_type: 'plate',
    source_id: '',
    source_url: '',
    protocol: 'rtsp',
    use_mock_frame: false,
    demo_file: '',
    frame_count: 20,
    sample_interval: 5,
    warmup_frames: 3,
    enabled: true,
    description: '',
  })
}

function editSource(item) {
  Object.assign(sourceForm, {
    id: item.id,
    source_key: item.source_key || '',
    name: item.name || '',
    source_type: item.source_type || 'plate',
    source_id: item.source_id || '',
    source_url: item.source_url || '',
    protocol: item.protocol || 'rtsp',
    use_mock_frame: Boolean(item.use_mock_frame),
    demo_file: item.demo_file || '',
    frame_count: Number(item.frame_count || 20),
    sample_interval: Number(item.sample_interval || 5),
    warmup_frames: Number(item.warmup_frames || 3),
    enabled: item.enabled !== false,
    description: item.description || '',
  })
}

async function saveSource() {
  if (!sourceForm.name || (!sourceForm.source_id && !sourceForm.source_url && !sourceForm.demo_file)) {
    errorMessage.value = '视频源名称不能为空，源 ID、地址或 Demo 文件至少填写一项'
    return
  }

  sourceSaving.value = true
  errorMessage.value = ''

  const payload = {
    source_key: sourceForm.source_key,
    name: sourceForm.name,
    source_type: sourceForm.source_type,
    source_id: sourceForm.source_id,
    source_url: sourceForm.source_url,
    protocol: sourceForm.protocol,
    use_mock_frame: sourceForm.use_mock_frame,
    demo_file: sourceForm.demo_file,
    frame_count: Number(sourceForm.frame_count || 20),
    sample_interval: Number(sourceForm.sample_interval || 5),
    warmup_frames: Number(sourceForm.warmup_frames || 3),
    enabled: sourceForm.enabled,
    description: sourceForm.description,
  }

  try {
    if (sourceForm.id) {
      await apiPut(`/api/video-sources/${sourceForm.id}`, payload)
    } else {
      await apiPost('/api/video-sources', payload)
    }

    resetSourceForm()
    await refreshAll()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    sourceSaving.value = false
  }
}

async function removeSource(item) {
  if (!window.confirm(`确定删除视频源“${item.name}”吗？`)) return

  try {
    await apiDelete(`/api/video-sources/${item.id}`)
    await refreshAll()
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function testSource(item) {
  sourceCheckingId.value = item.id

  try {
    sourceCheckResults[item.id] = await apiPost(`/api/video-sources/${item.id}/check`, {})
  } catch (error) {
    sourceCheckResults[item.id] = {
      online: false,
      message: error.message,
    }
  } finally {
    sourceCheckingId.value = null
  }
}

async function handleLogout() {
  await auth.logout()
  await router.replace('/login')
}

onMounted(() => {
  refreshAll()
})
</script>

<style scoped>
.admin-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  background: #f2f5f9;
  color: #172033;
}

.admin-sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 22px 15px;
  background: #172033;
  color: #ffffff;
}

.admin-brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 4px 7px 24px;
}

.admin-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  background: #6366f1;
  font-weight: 900;
}

.admin-brand strong,
.admin-brand span {
  display: block;
}

.admin-brand span {
  margin-top: 3px;
  color: #8f9bb3;
  font-size: 10px;
}

.admin-sidebar nav {
  display: grid;
  gap: 6px;
}

.admin-sidebar nav button {
  padding: 11px 12px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #aeb8ca;
  text-align: left;
  font-weight: 700;
}

.admin-sidebar nav button.active {
  background: #6366f1;
  color: #ffffff;
}

.admin-sidebar-foot {
  display: grid;
  gap: 8px;
  margin-top: auto;
}

.admin-sidebar-foot a,
.admin-sidebar-foot button {
  padding: 9px;
  border: 1px solid #34405a;
  border-radius: 9px;
  background: transparent;
  color: #dbe3ef;
  text-align: center;
  text-decoration: none;
}

.admin-main {
  min-width: 0;
  padding: 24px;
}

.admin-topbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 18px;
}

.admin-topbar p,
.admin-panel header p,
.api-panel > p:first-child {
  margin: 0;
  color: #6366f1;
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.15em;
}

.admin-topbar h1 {
  margin: 4px 0 0;
  font-size: 27px;
}

.admin-account {
  display: flex;
  gap: 9px;
  align-items: center;
}

.admin-account span,
.admin-account strong,
.admin-account button {
  padding: 9px 11px;
  border: 1px solid #d8deea;
  border-radius: 9px;
  background: #ffffff;
}

.admin-account span {
  color: #dc2626;
}

.admin-account span.online {
  color: #059669;
}

.admin-account button {
  color: #172033;
  font-weight: 800;
}

.admin-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 14px;
}

.span-12 {
  grid-column: span 12;
}

.span-8 {
  grid-column: span 8;
}

.span-7 {
  grid-column: span 7;
}

.span-5 {
  grid-column: span 5;
}

.span-4 {
  grid-column: span 4;
}

.admin-metric,
.admin-panel {
  border: 1px solid #dfe5ef;
  border-radius: 14px;
  background: #ffffff;
  box-shadow: 0 10px 28px rgba(23, 32, 51, 0.05);
}

.admin-metric {
  grid-column: span 3;
  padding: 17px;
}

.admin-metric span,
.admin-metric small {
  display: block;
  color: #7a879b;
}

.admin-metric strong {
  display: block;
  margin: 7px 0;
  font-size: 24px;
}

.admin-panel {
  padding: 18px;
}

.admin-panel header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.admin-panel h2 {
  margin: 4px 0;
  font-size: 20px;
}

.admin-panel header span {
  color: #7a879b;
  font-size: 13px;
}

.compact-list {
  display: grid;
  gap: 8px;
}

.compact-list > div {
  display: grid;
  grid-template-columns: 1.3fr 0.8fr auto;
  gap: 10px;
  padding: 10px;
  border-radius: 9px;
  background: #f7f9fc;
}

.compact-list span,
.compact-list small {
  color: #7a879b;
}

.risk-overview {
  display: grid;
  gap: 10px;
}

.risk-overview div {
  display: flex;
  justify-content: space-between;
  padding: 12px;
  border-radius: 10px;
  background: #f7f9fc;
}

.risk-overview strong {
  font-size: 21px;
}

.admin-table {
  overflow: auto;
}

.admin-table table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.admin-table th,
.admin-table td {
  padding: 10px;
  border-bottom: 1px solid #e8edf4;
  text-align: left;
  vertical-align: top;
}

.admin-table th {
  color: #718096;
  background: #f7f9fc;
}

.role-badge,
.severity {
  display: inline-flex;
  padding: 4px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 11px;
  font-weight: 800;
}

.severity.high {
  background: #fee2e2;
  color: #b91c1c;
}

.severity.medium {
  background: #fef3c7;
  color: #b45309;
}

.severity.low {
  background: #dcfce7;
  color: #15803d;
}

.table-button,
.light-button,
.primary-button,
.source-list-actions button {
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: #6366f1;
  color: #ffffff;
  font-weight: 700;
}

.light-button {
  background: #eef2ff;
  color: #4f46e5;
}

.admin-notice,
.admin-error {
  padding: 11px 12px;
  border-radius: 9px;
}

.admin-notice {
  margin-bottom: 13px;
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
}

.admin-error {
  margin-bottom: 14px;
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #b91c1c;
}

.source-form {
  display: grid;
  gap: 11px;
}

.source-form label {
  display: grid;
  gap: 6px;
  color: #58667a;
  font-size: 13px;
  font-weight: 700;
}

.source-form input,
.source-form select,
.source-form textarea {
  width: 100%;
  padding: 10px;
  border: 1px solid #d6deea;
  border-radius: 9px;
  outline: none;
  background: #ffffff;
}

.source-form .checkbox-label {
  display: flex;
  align-items: center;
}

.source-form .checkbox-label input {
  width: auto;
}

.source-admin-list {
  display: grid;
  gap: 9px;
}

.source-admin-list > article {
  display: flex;
  justify-content: space-between;
  gap: 13px;
  padding: 12px;
  border: 1px solid #e1e7f0;
  border-radius: 11px;
  background: #fafbfd;
}

.source-admin-list h3 {
  margin: 5px 0;
}

.source-admin-list p,
.source-admin-list span {
  margin: 0;
  color: #7a879b;
  font-size: 12px;
}

.source-list-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  justify-content: flex-end;
  align-items: flex-start;
}

.source-list-actions button.danger {
  background: #dc2626;
}

.source-list-actions small {
  flex-basis: 100%;
  color: #7a879b;
  text-align: right;
}

.worker-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.worker-grid article {
  padding: 13px;
  border: 1px solid #e1e7f0;
  border-radius: 11px;
  background: #fafbfd;
}

.worker-grid span {
  color: #6366f1;
  font-size: 11px;
  font-weight: 800;
}

.worker-grid strong {
  display: block;
  margin: 5px 0 9px;
}

.worker-grid p {
  margin: 4px 0;
  color: #7a879b;
  font-size: 13px;
}

.api-panel {
  padding: 28px;
}

.api-panel > p:not(:first-child) {
  color: #7a879b;
  line-height: 1.7;
}

.api-panel > div {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.api-panel a {
  padding: 10px 13px;
  border-radius: 9px;
  background: #6366f1;
  color: #ffffff;
  font-weight: 800;
  text-decoration: none;
}

.admin-empty {
  padding: 18px;
  color: #8a96a8;
  text-align: center;
}

@media (max-width: 1050px) {
  .admin-shell {
    grid-template-columns: 1fr;
  }

  .admin-sidebar {
    position: static;
    height: auto;
  }

  .admin-sidebar nav {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .admin-sidebar-foot {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    margin-top: 14px;
  }

  .admin-metric {
    grid-column: span 6;
  }

  .span-8,
  .span-7,
  .span-5,
  .span-4 {
    grid-column: span 12;
  }
}

@media (max-width: 720px) {
  .admin-main {
    padding: 14px;
  }

  .admin-topbar,
  .admin-panel header,
  .source-admin-list > article {
    flex-direction: column;
  }

  .admin-account {
    flex-wrap: wrap;
  }

  .admin-sidebar nav,
  .worker-grid {
    grid-template-columns: 1fr;
  }

  .admin-metric {
    grid-column: span 12;
  }

  .compact-list > div {
    grid-template-columns: 1fr;
  }
}
</style>
