<template>
  <div class="user-shell">
    <aside class="user-sidebar">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <strong>智能交通识别</strong>
          <span>用户业务端</span>
        </div>
      </div>

      <nav>
        <button
          v-for="item in navItems"
          :key="item.key"
          :class="{ active: activeView === item.key }"
          @click="activeView = item.key"
        >
          <span>{{ item.icon }}</span>
          {{ item.label }}
        </button>
      </nav>

      <div class="sidebar-foot">
        <router-link v-if="auth.isAdmin.value" to="/admin">进入管理员后台</router-link>
        <button class="logout" @click="handleLogout">退出登录</button>
      </div>
    </aside>

    <main class="user-main">
      <header class="user-topbar">
        <div>
          <p class="eyebrow">INTELLIGENT TRAFFIC VISION</p>
          <h1>{{ currentNav.label }}</h1>
        </div>

        <div class="topbar-actions">
          <span :class="['system-state', backendStatus.ok && 'online']">
            {{ backendStatus.ok ? '系统在线' : '系统异常' }}
          </span>
          <div class="user-chip">
            <strong>{{ auth.user.value?.username || auth.user.value?.email || '用户' }}</strong>
            <span>{{ auth.role.value }}</span>
          </div>
          <button class="refresh-button" :disabled="loading.refresh" @click="refreshAll">
            {{ loading.refresh ? '刷新中' : '刷新' }}
          </button>
        </div>
      </header>

      <p v-if="globalError" class="page-error">{{ globalError }}</p>

      <section v-if="activeView === 'overview'" class="content-grid">
        <article class="hero-card span-12">
          <div>
            <p class="eyebrow">MULTIMODAL PERCEPTION</p>
            <h2>道路感知、手势交互与融合决策</h2>
            <p>
              用户端聚焦识别任务和实时监控。调试参数、用户管理、系统日志等内容已移至管理员后台。
            </p>
          </div>
          <button @click="activeView = 'fusion'">进入融合监控</button>
        </article>

        <article v-for="metric in overviewMetrics" :key="metric.label" class="metric-card">
          <span>{{ metric.label }}</span>
          <strong>{{ metric.value }}</strong>
          <small>{{ metric.note }}</small>
        </article>

        <article class="panel span-7">
          <header class="panel-title">
            <div>
              <p class="eyebrow">LATEST DECISION</p>
              <h2>最近融合建议</h2>
            </div>
          </header>
          <div v-if="latestDecision" class="decision-card">
            <span :class="['risk-tag', `risk-${latestDecision.risk_level || 'low'}`]">
              {{ riskLabel(latestDecision.risk_level) }}
            </span>
            <h3>{{ latestDecision.scenario || '当前未检测到明确风险场景' }}</h3>
            <p>{{ latestDecision.suggestion || '继续保持安全驾驶并监控道路环境。' }}</p>
            <div class="decision-score">
              <span>风险分数</span>
              <strong>{{ latestDecision.risk_score ?? '-' }}</strong>
            </div>
          </div>
          <div v-else class="empty-state">尚无融合决策记录。</div>
        </article>

        <article class="panel span-5">
          <header class="panel-title">
            <div>
              <p class="eyebrow">RECENT ACTIVITY</p>
              <h2>最近识别</h2>
            </div>
          </header>
          <div class="timeline">
            <div v-for="item in records.slice(0, 6)" :key="item.id || item.record_id">
              <i></i>
              <div>
                <strong>{{ taskLabel(item.task_type) }}</strong>
                <span>{{ item.created_at || item.created_time || '-' }}</span>
              </div>
            </div>
            <div v-if="!records.length" class="empty-state">暂无识别记录。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'fusion'" class="content-grid">
        <article class="panel span-12">
          <header class="panel-title">
            <div>
              <p class="eyebrow">REALTIME FUSION</p>
              <h2>三路融合决策监控</h2>
              <p>选择车牌流、交警手势流，并接入车主摄像头，系统自动完成风险分析。</p>
            </div>
            <div class="button-row">
              <button
                :disabled="fusionMonitor.running || loading.fusion"
                @click="startFusionMonitor"
              >
                开始融合监控
              </button>
              <button
                class="secondary"
                :disabled="!fusionMonitor.running"
                @click="stopFusionMonitor"
              >
                停止监控
              </button>
            </div>
          </header>

          <div class="source-select-grid">
            <label>
              <span>车牌视频流</span>
              <select v-model="selectedPlateSourceId">
                <option value="">请选择车牌视频源</option>
                <option v-for="item in plateSources" :key="item.id" :value="item.id">
                  {{ item.name }}
                </option>
              </select>
            </label>

            <label>
              <span>交警手势视频流</span>
              <select v-model="selectedTrafficSourceId">
                <option value="">请选择交警手势视频源</option>
                <option v-for="item in trafficSources" :key="item.id" :value="item.id">
                  {{ item.name }}
                </option>
              </select>
            </label>

            <label>
              <span>自动分析间隔</span>
              <select v-model.number="fusionMonitor.intervalSeconds">
                <option :value="3">3 秒</option>
                <option :value="5">5 秒</option>
                <option :value="10">10 秒</option>
              </select>
            </label>
          </div>

          <div class="channel-grid">
            <div class="channel-card">
              <span>车牌感知通道</span>
              <strong>{{ plateEvidenceText }}</strong>
              <small>{{ evidence.plate?.latency_ms ? `${evidence.plate.latency_ms} ms` : '等待识别' }}</small>
            </div>
            <div class="channel-card">
              <span>交警手势通道</span>
              <strong>{{ trafficEvidenceText }}</strong>
              <small>{{ evidence.traffic?.latency_ms ? `${evidence.traffic.latency_ms} ms` : '等待识别' }}</small>
            </div>
            <div class="channel-card">
              <span>车主摄像头通道</span>
              <strong>{{ ownerEvidenceText }}</strong>
              <small>{{ evidence.owner?.latency_ms ? `${Math.round(evidence.owner.latency_ms)} ms` : '等待摄像头输入' }}</small>
            </div>
          </div>
        </article>

        <article class="panel span-12">
          <OwnerCameraPanel @recognized="handleOwnerEvidence" />
        </article>

        <article class="panel span-5">
          <header class="panel-title">
            <div>
              <p class="eyebrow">RISK ANALYSIS</p>
              <h2>实时风险分析</h2>
            </div>
          </header>

          <div v-if="currentDecision" class="decision-card">
            <span :class="['risk-tag', `risk-${currentDecision.risk_level || 'low'}`]">
              {{ riskLabel(currentDecision.risk_level) }}
            </span>
            <h3>{{ currentDecision.scenario || '-' }}</h3>
            <p>{{ currentDecision.suggestion || '-' }}</p>
            <dl>
              <div>
                <dt>风险分数</dt>
                <dd>{{ currentDecision.risk_score ?? '-' }}</dd>
              </div>
              <div>
                <dt>控制建议</dt>
                <dd>{{ currentDecision.control_advice || '-' }}</dd>
              </div>
            </dl>
          </div>
          <div v-else class="empty-state">启动融合监控后显示分析结果。</div>
        </article>

        <article class="panel span-7">
          <header class="panel-title">
            <div>
              <p class="eyebrow">MONITOR TIMELINE</p>
              <h2>融合监控时间线</h2>
            </div>
          </header>

          <div class="event-list">
            <div v-for="item in fusionTimeline" :key="item.id">
              <span>{{ item.time }}</span>
              <strong>{{ item.message }}</strong>
            </div>
            <div v-if="!fusionTimeline.length" class="empty-state">暂无监控事件。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'recognition'" class="content-grid">
        <article class="panel span-12">
          <header class="panel-title">
            <div>
              <p class="eyebrow">VISION RECOGNITION</p>
              <h2>视觉识别中心</h2>
            </div>
          </header>

          <div class="sub-tabs">
            <button
              v-for="item in recognitionTabs"
              :key="item.key"
              :class="{ active: recognitionTab === item.key }"
              @click="recognitionTab = item.key"
            >
              {{ item.label }}
            </button>
          </div>
        </article>

        <template v-if="recognitionTab === 'plate'">
          <article class="panel span-5">
            <header class="panel-title">
              <h2>道路车牌图片识别</h2>
            </header>
            <div class="upload-area">
              <input type="file" accept="image/*" @change="pickRecognitionFile($event, 'plate')" />
              <button :disabled="loading.plate" @click="recognizePlate">
                {{ loading.plate ? '识别中...' : '开始识别' }}
              </button>
            </div>
            <p v-if="recognitionError" class="page-error">{{ recognitionError }}</p>
          </article>

          <article class="panel span-7">
            <header class="panel-title">
              <h2>车牌识别结果</h2>
            </header>
            <div v-if="plateResult" class="recognition-result">
              <img
                v-if="plateResult.output_image_url"
                :src="assetUrl(plateResult.output_image_url)"
                alt="车牌标注结果"
              />
              <div class="structured-result">
                <div v-for="(plate, index) in plateList" :key="index">
                  <strong>{{ plate.plate || plate.plate_number || plate.text || '未解析号码' }}</strong>
                  <span>{{ plate.color || plate.plate_color || '颜色未知' }}</span>
                  <small>置信度 {{ percent(plate.confidence) }}</small>
                </div>
                <div v-if="!plateList.length" class="empty-state">当前图片未检测到车牌。</div>
              </div>
            </div>
            <div v-else class="empty-state">上传道路场景图片后显示定位框和 OCR 结果。</div>
          </article>
        </template>

        <template v-if="recognitionTab === 'traffic'">
          <article class="panel span-5">
            <header class="panel-title">
              <h2>交警手势图片识别</h2>
            </header>
            <div class="upload-area">
              <input type="file" accept="image/*" @change="pickRecognitionFile($event, 'traffic')" />
              <button :disabled="loading.traffic" @click="recognizeTraffic">
                {{ loading.traffic ? '识别中...' : '开始识别' }}
              </button>
            </div>
            <p v-if="recognitionError" class="page-error">{{ recognitionError }}</p>
          </article>

          <article class="panel span-7">
            <header class="panel-title">
              <h2>交警指挥信号</h2>
            </header>
            <div v-if="trafficResult" class="recognition-result">
              <img
                v-if="trafficResult.output_image_url"
                :src="assetUrl(trafficResult.output_image_url)"
                alt="交警手势骨架结果"
              />
              <div class="large-result">
                <span>识别手势</span>
                <strong>{{ trafficPayload.gesture_name || trafficPayload.gesture || '-' }}</strong>
                <p>{{ trafficPayload.traffic_command || trafficPayload.command || '暂无交通指令' }}</p>
                <small>置信度 {{ percent(trafficPayload.confidence) }}</small>
              </div>
            </div>
            <div v-else class="empty-state">上传交警手势图片后显示关键点和交通指令。</div>
          </article>
        </template>

        <template v-if="recognitionTab === 'owner'">
          <article class="panel span-12">
            <OwnerCameraPanel @recognized="handleOwnerEvidence" />
          </article>
        </template>
      </section>

      <section v-if="activeView === 'sources'" class="content-grid">
        <article class="panel span-12">
          <header class="panel-title">
            <div>
              <p class="eyebrow">VIDEO SOURCES</p>
              <h2>可用视频源</h2>
              <p>用户端仅负责选择和检测视频源；新增、编辑、删除由管理员后台完成。</p>
            </div>
          </header>

          <div class="source-list">
            <article v-for="item in videoSources" :key="item.id">
              <div>
                <span>{{ sourceTypeLabel(item.source_type) }}</span>
                <h3>{{ item.name }}</h3>
                <p>{{ maskUrl(item.source_url || item.demo_file || item.source_id || '-') }}</p>
              </div>
              <div class="source-actions">
                <strong :class="{ online: sourceChecks[item.id]?.online }">
                  {{ sourceChecks[item.id] ? (sourceChecks[item.id].online ? '可用' : '不可用') : '未检测' }}
                </strong>
                <button :disabled="checkingSourceId === item.id" @click="checkSource(item)">
                  {{ checkingSourceId === item.id ? '检测中...' : '检测连接' }}
                </button>
              </div>
            </article>
            <div v-if="!videoSources.length" class="empty-state">暂无已启用视频源。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'alerts'" class="content-grid">
        <article class="panel span-12">
          <header class="panel-title">
            <div>
              <p class="eyebrow">ALERT CENTER</p>
              <h2>风险与告警中心</h2>
            </div>
          </header>

          <div class="alert-summary">
            <div>
              <span>告警总数</span>
              <strong>{{ alerts.length }}</strong>
            </div>
            <div>
              <span>严重告警</span>
              <strong>{{ criticalAlertCount }}</strong>
            </div>
            <div>
              <span>未处理</span>
              <strong>{{ unresolvedAlertCount }}</strong>
            </div>
          </div>

          <div class="data-table">
            <table>
              <thead>
                <tr>
                  <th>级别</th>
                  <th>告警类型</th>
                  <th>摘要</th>
                  <th>时间</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in alerts" :key="item.id || item.alert_id">
                  <td>
                    <span :class="['risk-tag', `risk-${alertLevel(item)}`]">
                      {{ riskLabel(alertLevel(item)) }}
                    </span>
                  </td>
                  <td>{{ item.alert_type || item.event_type || item.type || '-' }}</td>
                  <td>{{ item.summary || item.description || item.message || '-' }}</td>
                  <td>{{ item.created_at || item.created_time || '-' }}</td>
                  <td>{{ item.status || (item.resolved ? '已处理' : '待处理') }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="!alerts.length" class="empty-state">暂无告警记录。</div>
          </div>
        </article>
      </section>

      <section v-if="activeView === 'records'" class="content-grid">
        <article class="panel span-12">
          <header class="panel-title">
            <div>
              <p class="eyebrow">MY RECORDS</p>
              <h2>识别记录</h2>
              <p>后端完成用户数据隔离后，此处将只展示当前账号产生的数据。</p>
            </div>
          </header>

          <div class="data-table">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>任务</th>
                  <th>输入方式</th>
                  <th>文件</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in records" :key="item.id || item.record_id">
                  <td>{{ item.id || item.record_id }}</td>
                  <td>{{ taskLabel(item.task_type) }}</td>
                  <td>{{ item.input_type || '-' }}</td>
                  <td>{{ item.original_filename || item.saved_filename || '-' }}</td>
                  <td>{{ item.created_at || item.created_time || '-' }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="!records.length" class="empty-state">暂无识别记录。</div>
          </div>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import OwnerCameraPanel from './components/OwnerCameraPanel.vue'
import { API_BASE, apiGet, apiPost, uploadFile } from './api'
import { useAuth } from './useAuth'

const router = useRouter()
const auth = useAuth()

const navItems = [
  { key: 'overview', label: '系统总览', icon: '⌂' },
  { key: 'fusion', label: '融合决策监控', icon: '◎' },
  { key: 'recognition', label: '视觉识别中心', icon: '◈' },
  { key: 'sources', label: '视频源', icon: '▣' },
  { key: 'alerts', label: '告警中心', icon: '!' },
  { key: 'records', label: '识别记录', icon: '≡' },
]

const recognitionTabs = [
  { key: 'plate', label: '车牌识别' },
  { key: 'traffic', label: '交警手势' },
  { key: 'owner', label: '车主手势控车' },
]

const activeView = ref('overview')
const recognitionTab = ref('plate')
const globalError = ref('')
const recognitionError = ref('')

const backendStatus = reactive({
  ok: false,
  message: '检测中',
})

const loading = reactive({
  refresh: false,
  fusion: false,
  plate: false,
  traffic: false,
})

const dashboard = ref({})
const records = ref([])
const alerts = ref([])
const videoSources = ref([])
const latestDecisionRaw = ref(null)

const selectedPlateSourceId = ref('')
const selectedTrafficSourceId = ref('')
const sourceChecks = reactive({})
const checkingSourceId = ref(null)

const plateFile = ref(null)
const trafficFile = ref(null)
const plateResult = ref(null)
const trafficResult = ref(null)

const fusionMonitor = reactive({
  running: false,
  intervalSeconds: 5,
  cycle: 0,
})

const fusionTimer = ref(null)
const evidence = reactive({
  plate: null,
  traffic: null,
  owner: null,
})

const currentDecisionRaw = ref(null)
const fusionTimeline = ref([])

const currentNav = computed(
  () => navItems.find((item) => item.key === activeView.value) || navItems[0],
)

const plateSources = computed(() =>
  videoSources.value.filter((item) => item.enabled !== false && item.source_type === 'plate'),
)

const trafficSources = computed(() =>
  videoSources.value.filter(
    (item) => item.enabled !== false && item.source_type === 'traffic_gesture',
  ),
)

const selectedPlateSource = computed(() =>
  videoSources.value.find((item) => String(item.id) === String(selectedPlateSourceId.value)),
)

const selectedTrafficSource = computed(() =>
  videoSources.value.find((item) => String(item.id) === String(selectedTrafficSourceId.value)),
)

const latestDecision = computed(() =>
  latestDecisionRaw.value?.decision ||
  latestDecisionRaw.value?.latest?.decision ||
  latestDecisionRaw.value?.latest ||
  latestDecisionRaw.value ||
  null,
)

const currentDecision = computed(() =>
  currentDecisionRaw.value?.decision ||
  currentDecisionRaw.value ||
  latestDecision.value,
)

const platePayload = computed(() =>
  plateResult.value?.result || plateResult.value || {},
)

const plateList = computed(() => {
  const payload = platePayload.value
  if (Array.isArray(payload.plates)) return payload.plates
  if (payload.plate_number || payload.plate || payload.text) return [payload]
  return []
})

const trafficPayload = computed(() =>
  trafficResult.value?.result || trafficResult.value || {},
)

const criticalAlertCount = computed(() =>
  alerts.value.filter((item) => alertLevel(item) === 'high').length,
)

const unresolvedAlertCount = computed(() =>
  alerts.value.filter((item) => !item.resolved && item.status !== 'resolved').length,
)

const overviewMetrics = computed(() => [
  {
    label: '今日识别',
    value: dashboard.value.today_recognition_count ?? dashboard.value.total_records ?? records.value.length,
    note: '当前账号可见数据',
  },
  {
    label: '可用视频源',
    value: videoSources.value.filter((item) => item.enabled !== false).length,
    note: '车牌与交警视频流',
  },
  {
    label: '未处理告警',
    value: unresolvedAlertCount.value,
    note: '需要关注的系统事件',
  },
  {
    label: '当前风险',
    value: riskLabel(latestDecision.value?.risk_level),
    note: `风险分数 ${latestDecision.value?.risk_score ?? '-'}`,
  },
])

const plateEvidenceText = computed(() => {
  const payload = evidence.plate?.result || evidence.plate || {}
  const plates = payload.plates || []
  if (Array.isArray(plates) && plates.length) {
    const first = plates[0]
    return first.plate || first.plate_number || first.text || `检测到 ${plates.length} 个车牌`
  }
  return payload.plate_number || payload.plate || '未检测到车牌'
})

const trafficEvidenceText = computed(() => {
  const payload = evidence.traffic?.result || evidence.traffic || {}
  return payload.gesture_name || payload.gesture || '未识别到手势'
})

const ownerEvidenceText = computed(() => {
  const payload = evidence.owner?.result || evidence.owner || {}
  return payload.gesture_name || payload.gesture || '等待摄像头输入'
})

function normalizeList(data) {
  if (Array.isArray(data)) return data
  return data?.items || data?.records || data?.alerts || data?.logs || []
}

function assetUrl(path) {
  if (!path) return ''
  if (/^https?:\/\//.test(path)) return path
  return `${API_BASE}${path}`
}

function percent(value) {
  const number = Number(value)
  if (Number.isNaN(number)) return '-'
  return `${Math.round(number <= 1 ? number * 100 : number)}%`
}

function taskLabel(value) {
  const map = {
    plate: '车牌识别',
    owner_gesture: '车主手势',
    traffic_gesture: '交警手势',
    fusion_decision: '融合决策',
  }
  return map[value] || value || '-'
}

function sourceTypeLabel(value) {
  const map = {
    plate: '车牌视频流',
    traffic_gesture: '交警手势视频流',
    owner_gesture: '车主手势视频流',
    general: '通用视频流',
  }
  return map[value] || value || '视频源'
}

function riskLabel(value) {
  const map = {
    high: '严重',
    critical: '严重',
    medium: '警告',
    warning: '警告',
    low: '正常',
    info: '提示',
  }
  return map[String(value || '').toLowerCase()] || '暂无'
}

function alertLevel(item) {
  const value = String(item.level || item.severity || item.risk_level || 'low').toLowerCase()
  if (['critical', 'high', '严重'].includes(value)) return 'high'
  if (['warning', 'medium', '警告'].includes(value)) return 'medium'
  return 'low'
}

function maskUrl(value) {
  if (!value) return '-'
  return String(value).replace(/:\/\/([^/@]+)@/, '://***@')
}

async function refreshAll() {
  loading.refresh = true
  globalError.value = ''

  const tasks = await Promise.allSettled([
    apiGet('/api/health'),
    apiGet('/api/dashboard/summary'),
    apiGet('/api/records?limit=30'),
    apiGet('/api/alerts?limit=30'),
    apiGet('/api/video-sources?enabled_only=true'),
    apiGet('/api/fusion/monitor/latest'),
  ])

  const [health, summary, recordData, alertData, sourceData, decisionData] = tasks

  if (health.status === 'fulfilled') {
    backendStatus.ok = ['ok', 'success'].includes(health.value.status)
    backendStatus.message = health.value.message || '后端运行正常'
  } else {
    backendStatus.ok = false
    backendStatus.message = health.reason?.message || '后端连接失败'
  }

  if (summary.status === 'fulfilled') dashboard.value = summary.value
  if (recordData.status === 'fulfilled') records.value = normalizeList(recordData.value)
  if (alertData.status === 'fulfilled') alerts.value = normalizeList(alertData.value)

  if (sourceData.status === 'fulfilled') {
    videoSources.value = normalizeList(sourceData.value)

    if (!selectedPlateSourceId.value && plateSources.value.length) {
      selectedPlateSourceId.value = plateSources.value[0].id
    }

    if (!selectedTrafficSourceId.value && trafficSources.value.length) {
      selectedTrafficSourceId.value = trafficSources.value[0].id
    }
  }

  if (decisionData.status === 'fulfilled') {
    latestDecisionRaw.value = decisionData.value
  }

  loading.refresh = false
}

function pickRecognitionFile(event, type) {
  const file = event.target.files?.[0] || null
  if (type === 'plate') plateFile.value = file
  if (type === 'traffic') trafficFile.value = file
  recognitionError.value = ''
}

async function recognizePlate() {
  if (!plateFile.value) {
    recognitionError.value = '请先选择道路场景图片'
    return
  }

  loading.plate = true
  recognitionError.value = ''

  try {
    plateResult.value = await uploadFile('/api/plate/image', plateFile.value)
    await refreshAll()
  } catch (error) {
    recognitionError.value = error.message
  } finally {
    loading.plate = false
  }
}

async function recognizeTraffic() {
  if (!trafficFile.value) {
    recognitionError.value = '请先选择交警手势图片'
    return
  }

  loading.traffic = true
  recognitionError.value = ''

  try {
    trafficResult.value = await uploadFile('/api/gesture/traffic/image', trafficFile.value)
    await refreshAll()
  } catch (error) {
    recognitionError.value = error.message
  } finally {
    loading.traffic = false
  }
}

function sourceToPayload(source, taskType) {
  return {
    source_id: source?.source_id || '',
    source_url: source?.source_url || '',
    task_type: taskType,
    use_mock_frame: Boolean(source?.use_mock_frame),
    demo_file: source?.demo_file || '',
    frame_count: Number(source?.frame_count || 20),
    sample_interval: Number(source?.sample_interval || 5),
    warmup_frames: Number(source?.warmup_frames || 3),
  }
}

function addFusionEvent(message) {
  fusionTimeline.value.unshift({
    id: `${Date.now()}_${Math.random()}`,
    time: new Date().toLocaleTimeString(),
    message,
  })

  fusionTimeline.value = fusionTimeline.value.slice(0, 20)
}

async function runFusionCycle() {
  if (!fusionMonitor.running || loading.fusion) return

  loading.fusion = true
  fusionMonitor.cycle += 1

  try {
    const tasks = []

    if (selectedPlateSource.value) {
      tasks.push(
        apiPost(
          '/api/fusion/monitor/channel/recognize',
          sourceToPayload(selectedPlateSource.value, 'plate'),
        ).then((data) => {
          evidence.plate = data
          addFusionEvent(`车牌通道：${plateEvidenceText.value}`)
        }),
      )
    }

    if (selectedTrafficSource.value) {
      tasks.push(
        apiPost(
          '/api/fusion/monitor/channel/recognize',
          sourceToPayload(selectedTrafficSource.value, 'traffic_gesture'),
        ).then((data) => {
          evidence.traffic = data
          addFusionEvent(`交警通道：${trafficEvidenceText.value}`)
        }),
      )
    }

    await Promise.allSettled(tasks)

    const data = await apiPost('/api/fusion/monitor/decision', {
      save: true,
      evidence: {
        plate: evidence.plate,
        traffic: evidence.traffic,
        owner: evidence.owner,
      },
    })

    currentDecisionRaw.value = data?.decision || data
    addFusionEvent(
      `融合分析：${riskLabel(currentDecision.value?.risk_level)}，${currentDecision.value?.scenario || '完成'}`,
    )
  } catch (error) {
    addFusionEvent(`融合监控异常：${error.message}`)
  } finally {
    loading.fusion = false
  }
}

function startFusionMonitor() {
  if (!selectedPlateSourceId.value || !selectedTrafficSourceId.value) {
    globalError.value = '请先选择车牌视频源和交警手势视频源'
    return
  }

  globalError.value = ''
  stopFusionMonitor()
  fusionMonitor.running = true
  fusionMonitor.cycle = 0
  addFusionEvent('融合监控已启动')
  runFusionCycle()

  fusionTimer.value = window.setInterval(
    runFusionCycle,
    Math.max(3, Number(fusionMonitor.intervalSeconds || 5)) * 1000,
  )
}

function stopFusionMonitor() {
  if (fusionTimer.value) {
    window.clearInterval(fusionTimer.value)
    fusionTimer.value = null
  }

  if (fusionMonitor.running) {
    addFusionEvent('融合监控已停止')
  }

  fusionMonitor.running = false
}

function handleOwnerEvidence(payload) {
  evidence.owner = payload
  addFusionEvent(`车主摄像头：${ownerEvidenceText.value}`)
}

async function checkSource(item) {
  checkingSourceId.value = item.id

  try {
    sourceChecks[item.id] = await apiPost(`/api/video-sources/${item.id}/check`, {})
  } catch (error) {
    sourceChecks[item.id] = {
      online: false,
      message: error.message,
    }
  } finally {
    checkingSourceId.value = null
  }
}

async function handleLogout() {
  stopFusionMonitor()
  await auth.logout()
  await router.replace('/login')
}

onMounted(() => {
  refreshAll()
})

onBeforeUnmount(() => {
  stopFusionMonitor()
})
</script>

<style scoped>
.user-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  background:
    radial-gradient(circle at 78% 8%, rgba(8, 145, 178, 0.14), transparent 30%),
    #07111f;
  color: #e2e8f0;
}

.user-sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 22px 16px;
  border-right: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(5, 14, 27, 0.94);
}

.brand {
  display: flex;
  gap: 11px;
  align-items: center;
  padding: 5px 7px 24px;
}

.brand-mark {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 13px;
  background: linear-gradient(135deg, #06b6d4, #2563eb);
  color: #ffffff;
  font-weight: 900;
}

.brand strong,
.brand span {
  display: block;
}

.brand span {
  margin-top: 3px;
  color: #64748b;
  font-size: 12px;
}

.user-sidebar nav {
  display: grid;
  gap: 7px;
}

.user-sidebar nav button {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 11px 12px;
  border: 1px solid transparent;
  border-radius: 11px;
  background: transparent;
  color: #94a3b8;
  text-align: left;
  font-weight: 700;
}

.user-sidebar nav button.active {
  border-color: rgba(34, 211, 238, 0.22);
  background: rgba(8, 145, 178, 0.14);
  color: #67e8f9;
}

.sidebar-foot {
  display: grid;
  gap: 9px;
  margin-top: auto;
}

.sidebar-foot a,
.sidebar-foot button {
  padding: 10px;
  border: 1px solid #26354d;
  border-radius: 10px;
  background: #0b1729;
  color: #cbd5e1;
  text-align: center;
  text-decoration: none;
  font-weight: 700;
}

.sidebar-foot button.logout {
  color: #fca5a5;
}

.user-main {
  min-width: 0;
  padding: 24px;
}

.user-topbar {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
  margin-bottom: 20px;
}

.user-topbar h1 {
  margin: 4px 0 0;
  font-size: 29px;
}

.eyebrow {
  margin: 0;
  color: #22d3ee;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.14em;
}

.topbar-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.system-state,
.user-chip,
.refresh-button {
  padding: 9px 11px;
  border: 1px solid #26354d;
  border-radius: 11px;
  background: #0b1729;
}

.system-state {
  color: #fca5a5;
  font-size: 13px;
}

.system-state.online {
  color: #6ee7b7;
}

.user-chip strong,
.user-chip span {
  display: block;
}

.user-chip span {
  color: #64748b;
  font-size: 11px;
}

.refresh-button {
  color: #e2e8f0;
  font-weight: 800;
}

.content-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 15px;
}

.span-12 {
  grid-column: span 12;
}

.span-7 {
  grid-column: span 7;
}

.span-5 {
  grid-column: span 5;
}

.panel,
.metric-card,
.hero-card {
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 18px;
  background: rgba(10, 22, 40, 0.88);
  box-shadow: 0 18px 46px rgba(0, 0, 0, 0.15);
}

.panel {
  padding: 18px;
}

.hero-card {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  padding: 24px;
  background:
    linear-gradient(135deg, rgba(8, 145, 178, 0.22), rgba(37, 99, 235, 0.11)),
    #0a1628;
}

.hero-card h2 {
  margin: 7px 0 9px;
  font-size: 26px;
}

.hero-card p {
  max-width: 760px;
  margin: 0;
  color: #94a3b8;
  line-height: 1.7;
}

.hero-card button,
.panel button,
.upload-area button,
.source-actions button {
  padding: 10px 13px;
  border: 0;
  border-radius: 10px;
  background: #0891b2;
  color: #ffffff;
  font-weight: 800;
}

.metric-card {
  grid-column: span 3;
  padding: 17px;
}

.metric-card span,
.metric-card small {
  display: block;
  color: #64748b;
}

.metric-card strong {
  display: block;
  margin: 7px 0;
  color: #f8fafc;
  font-size: 25px;
}

.panel-title {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
  margin-bottom: 15px;
}

.panel-title h2 {
  margin: 4px 0 0;
  font-size: 21px;
}

.panel-title p:not(.eyebrow) {
  margin: 5px 0 0;
  color: #94a3b8;
}

.button-row {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

button.secondary {
  background: #334155 !important;
}

.source-select-grid,
.channel-grid,
.alert-summary {
  display: grid;
  gap: 12px;
}

.source-select-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 14px;
}

.source-select-grid label {
  display: grid;
  gap: 7px;
  color: #94a3b8;
  font-size: 13px;
  font-weight: 700;
}

.source-select-grid select,
.upload-area input {
  width: 100%;
  padding: 10px;
  border: 1px solid #334155;
  border-radius: 10px;
  background: #0b1729;
  color: #e2e8f0;
}

.channel-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.channel-card {
  padding: 15px;
  border: 1px solid #26354d;
  border-radius: 14px;
  background: #0b1729;
}

.channel-card span,
.channel-card small {
  display: block;
  color: #64748b;
}

.channel-card strong {
  display: block;
  margin: 8px 0;
  color: #f8fafc;
  font-size: 18px;
}

.decision-card {
  padding: 17px;
  border: 1px solid #26354d;
  border-radius: 15px;
  background: #0b1729;
}

.decision-card h3 {
  margin: 13px 0 8px;
  font-size: 20px;
}

.decision-card p {
  color: #cbd5e1;
  line-height: 1.7;
}

.risk-tag {
  display: inline-flex;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 900;
}

.risk-high {
  background: rgba(239, 68, 68, 0.18);
  color: #fca5a5;
}

.risk-medium {
  background: rgba(245, 158, 11, 0.18);
  color: #fcd34d;
}

.risk-low {
  background: rgba(16, 185, 129, 0.16);
  color: #6ee7b7;
}

.decision-score,
.decision-card dl div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid #26354d;
}

.decision-card dl {
  margin-bottom: 0;
}

.decision-card dt {
  color: #94a3b8;
}

.decision-card dd {
  margin: 0;
  font-weight: 800;
}

.timeline,
.event-list {
  display: grid;
  gap: 9px;
}

.timeline > div,
.event-list > div {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px;
  border-radius: 11px;
  background: #0b1729;
}

.timeline i {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 50%;
  background: #22d3ee;
}

.timeline strong,
.timeline span,
.event-list strong,
.event-list span {
  display: block;
}

.timeline span,
.event-list span {
  color: #64748b;
  font-size: 12px;
}

.sub-tabs {
  display: flex;
  gap: 9px;
}

.sub-tabs button {
  background: #1e293b;
}

.sub-tabs button.active {
  background: #0891b2;
}

.upload-area {
  display: grid;
  gap: 12px;
}

.recognition-result {
  display: grid;
  grid-template-columns: minmax(250px, 1fr) minmax(220px, 0.8fr);
  gap: 14px;
}

.recognition-result img {
  width: 100%;
  max-height: 380px;
  object-fit: contain;
  border-radius: 13px;
  background: #020617;
}

.structured-result {
  display: grid;
  gap: 9px;
  align-content: start;
}

.structured-result > div,
.large-result {
  padding: 14px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1729;
}

.structured-result strong,
.structured-result span,
.structured-result small {
  display: block;
}

.structured-result strong,
.large-result strong {
  color: #f8fafc;
  font-size: 22px;
}

.structured-result span,
.structured-result small,
.large-result span,
.large-result small {
  margin-top: 5px;
  color: #94a3b8;
}

.large-result p {
  color: #67e8f9;
}

.source-list {
  display: grid;
  gap: 10px;
}

.source-list > article {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1729;
}

.source-list h3 {
  margin: 5px 0;
}

.source-list p,
.source-list span {
  margin: 0;
  color: #64748b;
  font-size: 12px;
}

.source-actions {
  display: grid;
  justify-items: end;
  gap: 8px;
}

.source-actions strong {
  color: #94a3b8;
}

.source-actions strong.online {
  color: #6ee7b7;
}

.alert-summary {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 14px;
}

.alert-summary div {
  padding: 14px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1729;
}

.alert-summary span,
.alert-summary strong {
  display: block;
}

.alert-summary span {
  color: #64748b;
}

.alert-summary strong {
  margin-top: 7px;
  font-size: 24px;
}

.data-table {
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  padding: 11px;
  border-bottom: 1px solid #26354d;
  text-align: left;
  vertical-align: top;
}

th {
  color: #94a3b8;
  font-weight: 700;
}

.empty-state {
  padding: 20px;
  border: 1px dashed #334155;
  border-radius: 13px;
  color: #64748b;
  text-align: center;
}

.page-error {
  padding: 10px 12px;
  border: 1px solid rgba(248, 113, 113, 0.32);
  border-radius: 11px;
  background: rgba(127, 29, 29, 0.2);
  color: #fecaca;
}

@media (max-width: 1050px) {
  .user-shell {
    grid-template-columns: 1fr;
  }

  .user-sidebar {
    position: static;
    height: auto;
  }

  .user-sidebar nav {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .sidebar-foot {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    margin-top: 15px;
  }

  .metric-card {
    grid-column: span 6;
  }

  .span-7,
  .span-5 {
    grid-column: span 12;
  }
}

@media (max-width: 720px) {
  .user-main {
    padding: 15px;
  }

  .user-topbar,
  .hero-card,
  .panel-title,
  .source-list > article {
    flex-direction: column;
  }

  .user-sidebar nav,
  .source-select-grid,
  .channel-grid,
  .alert-summary,
  .recognition-result {
    grid-template-columns: 1fr;
  }

  .metric-card {
    grid-column: span 12;
  }

  .topbar-actions {
    flex-wrap: wrap;
  }
}
</style>
