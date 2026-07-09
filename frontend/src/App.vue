<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import OwnerCameraPanel from './components/OwnerCameraPanel.vue'
import AlertDashboard from './components/AlertDashboard.vue'
import NavBar from './components/NavBar.vue'
import { useAuth } from './useAuth'

const router = useRouter()
const { isAuthenticated, init: initAuth } = useAuth()

const API_BASE = 'http://127.0.0.1:8000'

const tabs = [
  { key: 'dashboard', label: '系统总览' },
  { key: 'fusion', label: '融合决策监控' },
  { key: 'sources', label: '视频源管理' },
  { key: 'test', label: '模型测试中心' },
  { key: 'performance', label: '性能测试' },
  { key: 'records', label: '记录中心' },
]

const activeTab = ref('dashboard')

const backendStatus = ref({
  status: 'checking',
  message: '正在检测后端状态',
})

const dashboardSummary = ref(null)
const vehicleStateRaw = ref(null)
const records = ref([])
const alerts = ref([])
const logs = ref([])
const alertAnalysis = ref(null)
const monitorStatus = ref(null)

const fusionLatest = ref(null)
const fusionHistory = ref([])
const fusionDecision = ref(null)

const performanceSummary = ref(null)
const latencyRecords = ref([])

const multistreamStatus = ref(null)
const multistreamLatest = ref([])

const videoSources = ref([])
const selectedFusionPlateSourceId = ref('')
const selectedFusionTrafficSourceId = ref('')
const videoSourceCheckResults = reactive({})

const videoSourceForm = reactive({
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


const realtimeFusionMonitor = reactive({
  running: false,
  interval_seconds: 5,
  cycle: 0,
  auto_save: true,
})

const realtimeFusionTimer = ref(null)

const realtimeFusionEvidence = reactive({
  plate: null,
  traffic: null,
  owner: null,
})

const realtimeFusionConfig = reactive({
  plate: {
    source_id: 'live12',
    source_url: '',
    use_mock_frame: true,
    frame_count: 20,
    sample_interval: 5,
  },
  traffic: {
    source_id: 'traffic_demo',
    source_url: '',
    use_mock_frame: true,
    demo_file: 'traffic.png',
    frame_count: 20,
    sample_interval: 5,
  },
})

const selectedFusionPlateSourceKey = ref('plate_mock_live12')
const selectedFusionTrafficSourceKey = ref('traffic_demo_file')

const fusionPlateSourceOptions = [
  {
    key: 'plate_mock_live12',
    label: '沙盘 / Mock 测试源 live12',
    description: '使用后端 mock stream 测试车牌视频流链路，适合本地演示。',
    config: {
      source_id: 'live12',
      source_url: '',
      use_mock_frame: true,
      frame_count: 20,
      sample_interval: 5,
    },
  },
  {
    key: 'plate_rtsp_live12',
    label: '后端 RTSP 源 live12',
    description: '使用后端配置的视频源 live12，适合沙盘 RTSP 测试。',
    config: {
      source_id: 'live12',
      source_url: '',
      use_mock_frame: false,
      frame_count: 20,
      sample_interval: 5,
    },
  },
  {
    key: 'plate_rtsp_live3',
    label: '后端 RTSP 源 live3',
    description: '使用后端配置的视频源 live3。',
    config: {
      source_id: 'live3',
      source_url: '',
      use_mock_frame: false,
      frame_count: 20,
      sample_interval: 5,
    },
  },
  {
    key: 'plate_mediamtx',
    label: 'MediaMTX 车牌流 plate',
    description: '通过 rtsp://127.0.0.1:8554/plate 接入车牌视频流。',
    config: {
      source_id: 'plate_rtsp',
      source_url: 'rtsp://127.0.0.1:8554/plate',
      use_mock_frame: false,
      frame_count: 20,
      sample_interval: 5,
    },
  },
  {
    key: 'plate_custom',
    label: '自定义车牌视频流',
    description: '手动填写 source_id 或 RTSP 地址。',
    config: null,
  },
]

const fusionTrafficSourceOptions = [
  {
    key: 'traffic_demo_file',
    label: 'Demo 测试图 traffic.png',
    description: '使用 demo/traffic.png 作为交警手势测试输入，适合本地演示。',
    config: {
      source_id: 'traffic_demo',
      source_url: '',
      use_mock_frame: true,
      demo_file: 'traffic.png',
      frame_count: 20,
      sample_interval: 5,
    },
  },
  {
    key: 'traffic_mediamtx',
    label: 'MediaMTX 交警手势流 traffic',
    description: '通过 rtsp://127.0.0.1:8554/traffic 接入交警手势视频流。',
    config: {
      source_id: 'traffic_rtsp',
      source_url: 'rtsp://127.0.0.1:8554/traffic',
      use_mock_frame: false,
      demo_file: 'traffic.png',
      frame_count: 20,
      sample_interval: 5,
    },
  },
  {
    key: 'traffic_custom',
    label: '自定义交警手势视频流',
    description: '手动填写 source_id、RTSP 地址或 demo 文件。',
    config: null,
  },
]



const realtimeFusionDecision = ref(null)
const realtimeFusionTimeline = ref([])


const loading = reactive({
  refresh: false,
  ownerImage: false,
  ownerVideo: false,
  plateImage: false,
  trafficImage: false,
  stream: false,
  monitor: false,
  fusion: false,
  performance: false,
  multistream: false,
  videoSource: false,
})

const errors = reactive({
  global: '',
  owner: '',
  plate: '',
  traffic: '',
  stream: '',
  fusion: '',
  performance: '',
  multistream: '',
  videoSource: '',
})

const ownerImageFile = ref(null)
const ownerVideoFile = ref(null)
const plateImageFile = ref(null)
const trafficImageFile = ref(null)

const ownerImageResult = ref(null)
const ownerVideoResult = ref(null)
const plateResult = ref(null)
const trafficResult = ref(null)
const streamResult = ref(null)

const ownerFrameInterval = ref(3)
const ownerStableThreshold = ref(3)

const streamForm = reactive({
  source_id: 'live12',
  task_type: 'plate',
  frame_count: 20,
  sample_interval: 5,
  use_mock_frame: true,
})

const monitorForm = reactive({
  task_type: 'plate',
  interval_seconds: 30,
  frame_count: 20,
  sample_interval: 5,
  use_mock_frame: true,
})

const performanceForm = reactive({
  repeat: 1,
  threshold_ms: 1000,
  targets: ['health', 'plate_image', 'owner_image', 'traffic_image', 'stream_mock', 'fusion_decision'],
})

const ownerLatestResult = computed(() => ownerVideoResult.value || ownerImageResult.value)
const ownerResult = computed(() => ownerLatestResult.value?.result || {})

const ownerVehicleState = computed(() => {
  return ownerResult.value?.vehicle_state || vehicleStateRaw.value || {
    system_awake: false,
    current_function: 'home',
    volume: 50,
    temperature: 24,
    phone_status: '空闲',
    updated_at: '',
  }
})

const ownerLandmarks = computed(() => {
  const result = ownerResult.value
  if (Array.isArray(result.landmarks)) return result.landmarks

  if (Array.isArray(result.frame_results)) {
    const item = result.frame_results.find((frame) => Array.isArray(frame.landmarks) && frame.landmarks.length > 0)
    return item?.landmarks || []
  }

  return []
})

const ownerKeyLandmarks = computed(() => {
  const keyIds = new Set([0, 4, 8, 12, 16, 20])
  return ownerLandmarks.value.filter((item) => keyIds.has(item.index))
})

const multistreamWorkers = computed(() => {
  return Object.values(multistreamStatus.value?.workers || {})
})

const plateVideoSources = computed(() => {
  return videoSources.value.filter((item) => item.enabled !== false && item.source_type === 'plate')
})

const trafficVideoSources = computed(() => {
  return videoSources.value.filter((item) => item.enabled !== false && item.source_type === 'traffic_gesture')
})

const selectedPlateVideoSource = computed(() => {
  return videoSources.value.find((item) => String(item.id) === String(selectedFusionPlateSourceId.value)) || null
})

const selectedTrafficVideoSource = computed(() => {
  return videoSources.value.find((item) => String(item.id) === String(selectedFusionTrafficSourceId.value)) || null
})


const latestFusionDecision = computed(() => {
  return fusionDecision.value?.decision || fusionDecision.value || fusionLatest.value?.latest || fusionLatest.value || null
})

const currentRealtimeDecision = computed(() => {
  return realtimeFusionDecision.value?.decision || realtimeFusionDecision.value || latestFusionDecision.value
})

const hasRealtimeFusionEvidence = computed(() => {
  return Boolean(realtimeFusionEvidence.plate || realtimeFusionEvidence.traffic || realtimeFusionEvidence.owner)
})


const supportedOwnerGestures = [
  { gesture: 'open_palm', name: '手掌张开', action: '唤醒系统 / 返回主页' },
  { gesture: 'fist', name: '握拳', action: '确认当前操作' },
  { gesture: 'one', name: '单指', action: '音量调高' },
  { gesture: 'two', name: '双指', action: '音量调低' },
  { gesture: 'thumb_up', name: '拇指向上', action: '接听电话' },
  { gesture: 'thumb_down', name: '拇指向下', action: '挂断电话' },
  { gesture: 'wave', name: '挥手', action: '返回主页' },
  { gesture: 'circle', name: '单指画圈', action: '大幅调节音量' },
]

function assetUrl(path) {
  if (!path) return ''
  if (path.startsWith('http')) return path
  return `${API_BASE}${path}`
}

function prettyJson(value) {
  if (!value) return ''
  return JSON.stringify(value, null, 2)
}

function percent(value) {
  const num = Number(value)
  if (Number.isNaN(num)) return '0%'
  if (num <= 1) return `${Math.round(num * 100)}%`
  return `${Math.round(num)}%`
}

function shortText(value, max = 80) {
  if (value === null || value === undefined) return '-'
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.length > max ? `${text.slice(0, max)}...` : text
}

function normalizeList(data) {
  return data?.items || data?.records || data?.alerts || data?.logs || []
}

async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options)
  const text = await response.text()
  let data = {}

  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { raw: text }
  }

  if (!response.ok) {
    throw new Error(data?.detail || data?.message || text || `HTTP ${response.status}`)
  }

  return data
}

function pickFile(event, targetName) {
  const file = event.target.files?.[0] || null

  if (targetName === 'ownerImage') ownerImageFile.value = file
  if (targetName === 'ownerVideo') ownerVideoFile.value = file
  if (targetName === 'plateImage') plateImageFile.value = file
  if (targetName === 'trafficImage') trafficImageFile.value = file

  if (targetName === 'ownerImage' || targetName === 'ownerVideo') errors.owner = ''
  if (targetName === 'plateImage') errors.plate = ''
  if (targetName === 'trafficImage') errors.traffic = ''
}


function videoSourceToFusionConfig(item) {
  return {
    source_id: item?.source_id || '',
    source_url: item?.source_url || '',
    use_mock_frame: Boolean(item?.use_mock_frame),
    demo_file: item?.demo_file || '',
    frame_count: Number(item?.frame_count || 20),
    sample_interval: Number(item?.sample_interval || 5),
    warmup_frames: Number(item?.warmup_frames || 3),
  }
}

async function loadVideoSources() {
  try {
    const data = await requestJson('/api/video-sources?enabled_only=false')
    videoSources.value = data.items || []

    if (!selectedFusionPlateSourceId.value) {
      const defaultPlate =
        videoSources.value.find((item) => item.source_key === 'plate_mock_live12') ||
        videoSources.value.find((item) => item.source_type === 'plate')

      if (defaultPlate) {
        selectedFusionPlateSourceId.value = defaultPlate.id
        applySelectedFusionVideoSource('plate')
      }
    }

    if (!selectedFusionTrafficSourceId.value) {
      const defaultTraffic =
        videoSources.value.find((item) => item.source_key === 'traffic_demo_file') ||
        videoSources.value.find((item) => item.source_type === 'traffic_gesture')

      if (defaultTraffic) {
        selectedFusionTrafficSourceId.value = defaultTraffic.id
        applySelectedFusionVideoSource('traffic')
      }
    }
  } catch (error) {
    errors.videoSource = error.message
  }
}

function applySelectedFusionVideoSource(kind) {
  if (kind === 'plate') {
    const item = selectedPlateVideoSource.value
    if (!item) return
    Object.assign(realtimeFusionConfig.plate, videoSourceToFusionConfig(item))
    return
  }

  if (kind === 'traffic') {
    const item = selectedTrafficVideoSource.value
    if (!item) return
    Object.assign(realtimeFusionConfig.traffic, videoSourceToFusionConfig(item))
  }
}

function resetVideoSourceForm() {
  Object.assign(videoSourceForm, {
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

function editVideoSource(item) {
  Object.assign(videoSourceForm, {
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

  activeTab.value = 'sources'
}

async function saveVideoSource() {
  loading.videoSource = true
  errors.videoSource = ''

  try {
    const body = {
      source_key: videoSourceForm.source_key,
      name: videoSourceForm.name,
      source_type: videoSourceForm.source_type,
      source_id: videoSourceForm.source_id,
      source_url: videoSourceForm.source_url,
      protocol: videoSourceForm.protocol,
      use_mock_frame: videoSourceForm.use_mock_frame,
      demo_file: videoSourceForm.demo_file,
      frame_count: Number(videoSourceForm.frame_count || 20),
      sample_interval: Number(videoSourceForm.sample_interval || 5),
      warmup_frames: Number(videoSourceForm.warmup_frames || 3),
      enabled: videoSourceForm.enabled,
      description: videoSourceForm.description,
    }

    if (videoSourceForm.id) {
      await requestJson(`/api/video-sources/${videoSourceForm.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
    } else {
      await requestJson('/api/video-sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
    }

    resetVideoSourceForm()
    await loadVideoSources()
  } catch (error) {
    errors.videoSource = error.message
  } finally {
    loading.videoSource = false
  }
}

async function deleteVideoSource(item) {
  if (!window.confirm(`确定删除视频源“${item.name}”吗？`)) {
    return
  }

  loading.videoSource = true
  errors.videoSource = ''

  try {
    await requestJson(`/api/video-sources/${item.id}`, {
      method: 'DELETE',
    })

    if (String(selectedFusionPlateSourceId.value) === String(item.id)) {
      selectedFusionPlateSourceId.value = ''
    }

    if (String(selectedFusionTrafficSourceId.value) === String(item.id)) {
      selectedFusionTrafficSourceId.value = ''
    }

    await loadVideoSources()
  } catch (error) {
    errors.videoSource = error.message
  } finally {
    loading.videoSource = false
  }
}

async function checkVideoSource(item) {
  loading.videoSource = true
  errors.videoSource = ''

  try {
    const result = await requestJson(`/api/video-sources/${item.id}/check`, {
      method: 'POST',
    })

    videoSourceCheckResults[item.id] = result
  } catch (error) {
    videoSourceCheckResults[item.id] = {
      status: 'error',
      online: false,
      message: error.message,
    }
  } finally {
    loading.videoSource = false
  }
}

const testAlertResult = ref('')

async function testAlertAgent() {
  testAlertResult.value = '评估中...'
  try {
    const resp = await requestJson('/api/alerts/agent/evaluate', { method: 'POST' })
    if (resp.status === 'success') {
      testAlertResult.value = `✅ 评估完成：触发 ${resp.triggered_count} 条告警，历史累计 ${resp.stats.total} 条`
    } else {
      testAlertResult.value = `⚠️ ${resp.detail || '未知错误'}`
    }
  } catch (e) {
    testAlertResult.value = `❌ 请求失败：${e.message || e}`
  }
  setTimeout(() => { testAlertResult.value = '' }, 5000)
}

async function refreshAll() {
  loading.refresh = true
  errors.global = ''

  try {
    const tasks = await Promise.allSettled([
      requestJson('/api/health'),
      requestJson('/api/dashboard/summary'),
      requestJson('/api/vehicle/state'),
      requestJson('/api/records?limit=20'),
      requestJson('/api/alerts?limit=20'),
      requestJson('/api/logs?limit=20'),
      requestJson('/api/alerts/analysis'),
      requestJson('/api/monitor/status'),
      requestJson('/api/fusion/latest'),
      requestJson('/api/fusion/history?limit=10'),
      requestJson('/api/performance/summary'),
      requestJson('/api/performance/latency-records?limit=20'),
      requestJson('/api/multistream/status'),
      requestJson('/api/multistream/latest?limit=20'),
    ])

    const [
      health,
      summary,
      vehicle,
      recordData,
      alertData,
      logData,
      analysis,
      monitor,
      fusionLatestData,
      fusionHistoryData,
      performanceSummaryData,
      latencyRecordData,
      multistreamStatusData,
      multistreamLatestData,
    ] = tasks

    if (health.status === 'fulfilled') {
      backendStatus.value = health.value
    } else {
      backendStatus.value = {
        status: 'error',
        message: health.reason?.message || '后端连接失败',
      }
    }

    if (summary.status === 'fulfilled') dashboardSummary.value = summary.value
    if (vehicle.status === 'fulfilled') vehicleStateRaw.value = vehicle.value?.vehicle_state || vehicle.value
    if (recordData.status === 'fulfilled') records.value = normalizeList(recordData.value)
    if (alertData.status === 'fulfilled') alerts.value = normalizeList(alertData.value)
    if (logData.status === 'fulfilled') logs.value = normalizeList(logData.value)
    if (analysis.status === 'fulfilled') alertAnalysis.value = analysis.value
    if (monitor.status === 'fulfilled') monitorStatus.value = monitor.value?.monitor || monitor.value
    if (fusionLatestData.status === 'fulfilled') fusionLatest.value = fusionLatestData.value
    if (fusionHistoryData.status === 'fulfilled') fusionHistory.value = normalizeList(fusionHistoryData.value)
    if (performanceSummaryData.status === 'fulfilled') performanceSummary.value = performanceSummaryData.value?.summary || performanceSummaryData.value
    if (latencyRecordData.status === 'fulfilled') latencyRecords.value = normalizeList(latencyRecordData.value)
    if (multistreamStatusData.status === 'fulfilled') multistreamStatus.value = multistreamStatusData.value?.state || multistreamStatusData.value
    if (multistreamLatestData.status === 'fulfilled') multistreamLatest.value = normalizeList(multistreamLatestData.value)
  } catch (error) {
    errors.global = error.message
  } finally {
    loading.refresh = false
  }
}

async function uploadOwnerImage() {
  if (!ownerImageFile.value) {
    errors.owner = '请先选择车主手势图片'
    return
  }

  loading.ownerImage = true
  errors.owner = ''

  try {
    const formData = new FormData()
    formData.append('file', ownerImageFile.value)

    ownerImageResult.value = await requestJson('/api/gesture/owner/image', {
      method: 'POST',
      body: formData,
    })

    ownerVideoResult.value = null
    await refreshAll()
  } catch (error) {
    errors.owner = error.message
  } finally {
    loading.ownerImage = false
  }
}

async function uploadOwnerVideo() {
  if (!ownerVideoFile.value) {
    errors.owner = '请先选择车主手势视频'
    return
  }

  loading.ownerVideo = true
  errors.owner = ''

  try {
    const formData = new FormData()
    formData.append('file', ownerVideoFile.value)

    const query = new URLSearchParams({
      frame_sample_interval: String(ownerFrameInterval.value),
      stable_threshold: String(ownerStableThreshold.value),
    })

    ownerVideoResult.value = await requestJson(`/api/gesture/owner/video?${query.toString()}`, {
      method: 'POST',
      body: formData,
    })

    ownerImageResult.value = null
    await refreshAll()
  } catch (error) {
    errors.owner = error.message
  } finally {
    loading.ownerVideo = false
  }
}

async function uploadPlateImage() {
  if (!plateImageFile.value) {
    errors.plate = '请先选择车牌图片'
    return
  }

  loading.plateImage = true
  errors.plate = ''

  try {
    const formData = new FormData()
    formData.append('file', plateImageFile.value)

    plateResult.value = await requestJson('/api/plate/image', {
      method: 'POST',
      body: formData,
    })

    await refreshAll()
  } catch (error) {
    errors.plate = error.message
  } finally {
    loading.plateImage = false
  }
}

async function uploadTrafficImage() {
  if (!trafficImageFile.value) {
    errors.traffic = '请先选择交警手势图片'
    return
  }

  loading.trafficImage = true
  errors.traffic = ''

  try {
    const formData = new FormData()
    formData.append('file', trafficImageFile.value)

    trafficResult.value = await requestJson('/api/gesture/traffic/image', {
      method: 'POST',
      body: formData,
    })

    await refreshAll()
  } catch (error) {
    errors.traffic = error.message
  } finally {
    loading.trafficImage = false
  }
}

async function recognizeStream() {
  loading.stream = true
  errors.stream = ''

  try {
    streamResult.value = await requestJson('/api/stream/recognize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(streamForm),
    })

    await refreshAll()
  } catch (error) {
    errors.stream = error.message
  } finally {
    loading.stream = false
  }
}

async function startMonitor() {
  loading.monitor = true
  errors.stream = ''

  try {
    const body = {
      task_type: monitorForm.task_type,
      interval_seconds: Number(monitorForm.interval_seconds),
      frame_count: Number(monitorForm.frame_count),
      sample_interval: Number(monitorForm.sample_interval),
      use_mock_frame: Boolean(monitorForm.use_mock_frame),
      source_ids: ['all'],
    }

    const result = await requestJson('/api/monitor/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    monitorStatus.value = result.monitor || result
    await refreshAll()
  } catch (error) {
    errors.stream = error.message
  } finally {
    loading.monitor = false
  }
}

async function stopMonitor() {
  loading.monitor = true
  errors.stream = ''

  try {
    const result = await requestJson('/api/monitor/stop', {
      method: 'POST',
    })

    monitorStatus.value = result.monitor || result
    await refreshAll()
  } catch (error) {
    errors.stream = error.message
  } finally {
    loading.monitor = false
  }
}


function currentPlateSourceOption() {
  return fusionPlateSourceOptions.find((item) => item.key === selectedFusionPlateSourceKey.value)
}

function currentTrafficSourceOption() {
  return fusionTrafficSourceOptions.find((item) => item.key === selectedFusionTrafficSourceKey.value)
}

function applyRealtimeFusionPlatePreset() {
  const option = currentPlateSourceOption()

  if (!option || !option.config) {
    return
  }

  Object.assign(realtimeFusionConfig.plate, option.config)
}

function applyRealtimeFusionTrafficPreset() {
  const option = currentTrafficSourceOption()

  if (!option || !option.config) {
    return
  }

  Object.assign(realtimeFusionConfig.traffic, option.config)
}

function pushRealtimeFusionLog(type, message, payload = null) {
  realtimeFusionTimeline.value.unshift({
    id: `${Date.now()}_${Math.random()}`,
    type,
    message,
    payload,
    created_at: new Date().toLocaleTimeString(),
  })

  if (realtimeFusionTimeline.value.length > 30) {
    realtimeFusionTimeline.value = realtimeFusionTimeline.value.slice(0, 30)
  }
}

function summarizePlateChannel(data, latencyMs = null) {
  const result = data?.result || data?.summary || data?.result_summary || data || {}
  return {
    status: data?.status || result.status || 'success',
    source_id: data?.source_id || result.source_id || 'live12',
    task_type: 'plate',
    input_type: data?.input_type || result.input_type || 'video_stream',
    latency_ms: latencyMs ?? data?.latency_ms ?? result.latency_ms ?? null,
    result,
    created_at: new Date().toISOString(),
  }
}

function summarizeTrafficChannel(data, latencyMs = null) {
  const result = data?.result || data?.summary || data?.result_summary || data || {}
  return {
    status: data?.status || result.status || 'success',
    source_id: data?.source_id || result.source_id || 'traffic_stream',
    task_type: 'traffic_gesture',
    input_type: data?.input_type || result.input_type || 'video_stream',
    latency_ms: latencyMs ?? data?.latency_ms ?? result.latency_ms ?? null,
    result,
    created_at: new Date().toISOString(),
  }
}

function handleOwnerCameraRecognized(payload) {
  realtimeFusionEvidence.owner = {
    ...payload,
    source_id: 'owner_camera',
    task_type: 'owner_gesture',
    input_type: payload.input_type || 'camera_fast_frame',
    created_at: payload.created_at || new Date().toISOString(),
  }

  const gestureName = payload?.result?.gesture_name || payload?.result?.gesture || '未知手势'
  pushRealtimeFusionLog('owner', `车主摄像头识别：${gestureName}`, realtimeFusionEvidence.owner)
}

async function recognizeRealtimePlate() {
  const body = {
    source_id: realtimeFusionConfig.plate.source_id || 'live12',
    source_url: realtimeFusionConfig.plate.source_url || '',
    task_type: 'plate',
    frame_count: Number(realtimeFusionConfig.plate.frame_count || 20),
    sample_interval: Number(realtimeFusionConfig.plate.sample_interval || 5),
    use_mock_frame: Boolean(realtimeFusionConfig.plate.use_mock_frame),
  }

  const data = await requestJson('/api/fusion/monitor/channel/recognize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  realtimeFusionEvidence.plate = summarizePlateChannel(data, data.latency_ms)

  const result = realtimeFusionEvidence.plate.result || {}
  const count = result.plate_count ?? result.result?.plate_count ?? 0
  pushRealtimeFusionLog(
    'plate',
    `车牌视频流识别完成，source=${body.source_id || body.source_url || '-'}，车牌数量：${count}，延迟：${data.latency_ms}ms`,
    realtimeFusionEvidence.plate
  )
}

async function recognizeRealtimeTraffic() {
  const body = {
    source_id: realtimeFusionConfig.traffic.source_id || 'traffic_demo',
    source_url: realtimeFusionConfig.traffic.source_url || '',
    task_type: 'traffic_gesture',
    frame_count: Number(realtimeFusionConfig.traffic.frame_count || 20),
    sample_interval: Number(realtimeFusionConfig.traffic.sample_interval || 5),
    use_mock_frame: Boolean(realtimeFusionConfig.traffic.use_mock_frame),
    demo_file: realtimeFusionConfig.traffic.demo_file || 'traffic.png',
  }

  const data = await requestJson('/api/fusion/monitor/channel/recognize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  realtimeFusionEvidence.traffic = summarizeTrafficChannel(data, data.latency_ms)

  const result = realtimeFusionEvidence.traffic.result || {}
  const gesture = result.gesture_name || result.gesture || '-'
  pushRealtimeFusionLog(
    'traffic',
    `交警手势视频流识别完成，source=${body.source_id || body.source_url || '-'}，结果：${gesture}，延迟：${data.latency_ms}ms`,
    realtimeFusionEvidence.traffic
  )
}

async function runRealtimeFusionDecision() {
  const payload = {
    save: realtimeFusionMonitor.auto_save,
    evidence: {
      plate: realtimeFusionEvidence.plate,
      traffic: realtimeFusionEvidence.traffic,
      owner: realtimeFusionEvidence.owner,
    },
  }

  const data = await requestJson('/api/fusion/monitor/decision', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  realtimeFusionDecision.value = data.decision || data
  const decision = data.decision || data
  pushRealtimeFusionLog('fusion', `融合决策：${decision.risk_level} / ${decision.risk_score} 分，${decision.scenario}`, decision)
}

async function runRealtimeFusionCycle() {
  if (!realtimeFusionMonitor.running) return

  loading.fusion = true
  errors.fusion = ''

  try {
    realtimeFusionMonitor.cycle += 1

    await Promise.allSettled([
      recognizeRealtimePlate(),
      recognizeRealtimeTraffic(),
    ])

    await runRealtimeFusionDecision()
    await refreshAll()
  } catch (error) {
    errors.fusion = error.message
    pushRealtimeFusionLog('error', `融合监控异常：${error.message}`)
  } finally {
    loading.fusion = false
  }
}

function startRealtimeFusionMonitor() {
  stopRealtimeFusionMonitor()

  realtimeFusionMonitor.running = true
  realtimeFusionMonitor.cycle = 0

  pushRealtimeFusionLog('system', '实时融合监控已启动')

  runRealtimeFusionCycle()

  const interval = Math.max(3, Number(realtimeFusionMonitor.interval_seconds || 5)) * 1000
  realtimeFusionTimer.value = window.setInterval(runRealtimeFusionCycle, interval)
}

function stopRealtimeFusionMonitor() {
  if (realtimeFusionTimer.value) {
    window.clearInterval(realtimeFusionTimer.value)
    realtimeFusionTimer.value = null
  }

  if (realtimeFusionMonitor.running) {
    pushRealtimeFusionLog('system', '实时融合监控已停止')
  }

  realtimeFusionMonitor.running = false
}

async function runFusionDecision() {
  loading.fusion = true
  errors.fusion = ''

  try {
    fusionDecision.value = await requestJson('/api/fusion/decision?save=true', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })

    await refreshAll()
  } catch (error) {
    errors.fusion = error.message
  } finally {
    loading.fusion = false
  }
}

async function runPerformanceTest() {
  loading.performance = true
  errors.performance = ''

  try {
    const body = {
      repeat: Number(performanceForm.repeat),
      threshold_ms: Number(performanceForm.threshold_ms),
      targets: performanceForm.targets,
    }

    const result = await requestJson('/api/performance/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    performanceSummary.value = result.overall || result.summary || result
    await refreshAll()
  } catch (error) {
    errors.performance = error.message
  } finally {
    loading.performance = false
  }
}

async function startMultistream() {
  loading.multistream = true
  errors.multistream = ''

  try {
    const body = {
      threshold_ms: 1000,
      enable_fusion: true,
      fusion_interval_seconds: 5,
      workers: [
        {
          worker_id: 'plate_stream_worker',
          source_id: 'live12',
          task_type: 'plate',
          use_mock_frame: true,
          interval_seconds: 5,
          frame_count: 20,
          sample_interval: 5,
        },
        {
          worker_id: 'traffic_stream_worker',
          source_id: 'traffic_demo',
          task_type: 'traffic_gesture',
          use_mock_frame: true,
          demo_file: 'traffic.png',
          interval_seconds: 5,
        },
        {
          worker_id: 'owner_stream_worker',
          source_id: 'owner_demo',
          task_type: 'owner_gesture',
          use_mock_frame: true,
          demo_file: 'hand.jpg',
          interval_seconds: 5,
        },
      ],
    }

    const result = await requestJson('/api/multistream/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    multistreamStatus.value = result.state || result
    await refreshAll()
  } catch (error) {
    errors.multistream = error.message
  } finally {
    loading.multistream = false
  }
}

async function stopMultistream() {
  loading.multistream = true
  errors.multistream = ''

  try {
    const result = await requestJson('/api/multistream/stop', {
      method: 'POST',
    })

    multistreamStatus.value = result.state || result
    await refreshAll()
  } catch (error) {
    errors.multistream = error.message
  } finally {
    loading.multistream = false
  }
}

onMounted(() => {
  loadVideoSources()
  refreshAll()
})

onBeforeUnmount(() => {
  stopRealtimeFusionMonitor()
})
</script>

<template>
  <div class="app-shell">
    <NavBar />

    <!-- 后端状态条 -->
    <div class="status-bar">
      <span
        class="status-dot"
        :class="{ ok: backendStatus.status === 'ok' || backendStatus.status === 'success' }"
      ></span>
      <span class="status-text">{{ backendStatus.message || '后端状态未知' }}</span>
      <button class="small-btn" :disabled="loading.refresh" @click="refreshAll">
        {{ loading.refresh ? '刷新中...' : '刷新' }}
      </button>
    </div>

    <nav class="nav-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <p v-if="errors.global" class="error-line">{{ errors.global }}</p>

    <main>
      <section v-if="activeTab === 'dashboard'" class="page-grid">
        <div class="panel span-12 hero-panel">
          <div>
            <p class="eyebrow">System Overview</p>
            <h2>系统总览</h2>
            <p>
              当前系统后端已包含融合决策、多路并发处理、性能测试、车主摄像头实时识别、图片识别和记录告警能力。
            </p>
          </div>
          <div class="badge-row">
            <span>多模态识别</span>
            <span>融合决策</span>
            <span>多路并发</span>
            <span>1 秒延迟测试</span>
          </div>
        </div>

        <div class="metric-card">
          <span>后端状态</span>
          <strong>{{ backendStatus.status }}</strong>
          <p>{{ backendStatus.message }}</p>
        </div>
        <div class="metric-card">
          <span>识别记录</span>
          <strong>{{ records.length }}</strong>
          <p>最近加载记录数</p>
        </div>
        <div class="metric-card">
          <span>告警记录</span>
          <strong>{{ alerts.length }}</strong>
          <p>最近加载告警数</p>
        </div>
        <div class="metric-card">
          <span>融合风险</span>
          <strong>{{ latestFusionDecision?.risk_level || '-' }}</strong>
          <p>分数：{{ latestFusionDecision?.risk_score ?? '-' }}</p>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>最近融合建议</h2>
          </div>
          <div v-if="latestFusionDecision" class="decision-card">
            <strong>{{ latestFusionDecision.scenario || '暂无场景' }}</strong>
            <p>{{ latestFusionDecision.suggestion || '-' }}</p>
            <small>{{ latestFusionDecision.reason || '' }}</small>
          </div>
          <div v-else class="empty-state">暂无融合决策结果。</div>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>最近识别记录</h2>
          </div>
          <div class="simple-list">
            <div v-for="item in records.slice(0, 6)" :key="item.id || item.record_id" class="list-item">
              <strong>#{{ item.id || item.record_id }} {{ item.task_type }}</strong>
              <span>{{ item.input_type }} | {{ item.created_at || item.created_time || '-' }}</span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'fusion'" class="page-grid">
        <div class="panel span-12 hero-panel">
          <div>
            <p class="eyebrow">Realtime Fusion Decision Monitor</p>
            <h2>融合决策监控</h2>
            <p>
              同时接入车牌视频流、交警手势视频流和车主摄像头识别结果，自动生成融合决策、风险等级和驾驶建议。
            </p>
          </div>
          <div class="button-row">
            <button :disabled="loading.fusion || realtimeFusionMonitor.running" @click="startRealtimeFusionMonitor">
              启动融合监控
            </button>
            <button class="ghost-btn" :disabled="loading.fusion || !realtimeFusionMonitor.running" @click="stopRealtimeFusionMonitor">
              停止融合监控
            </button>
            <button class="ghost-btn" :disabled="loading.fusion" @click="runFusionDecision">
              手动生成一次
            </button>
          </div>
        </div>

        <p v-if="errors.fusion" class="error-line span-12">{{ errors.fusion }}</p>

        <div class="panel span-12">
          <div class="panel-header">
            <div>
              <h2>融合监控控制台</h2>
              <p>当前周期：{{ realtimeFusionMonitor.cycle }}；运行状态：{{ realtimeFusionMonitor.running ? '运行中' : '未运行' }}</p>
            </div>
            <div class="form-row">
              <label>
                决策间隔秒
                <input v-model.number="realtimeFusionMonitor.interval_seconds" type="number" min="3" max="60" />
              </label>
              <label class="checkbox-line">
                <input v-model="realtimeFusionMonitor.auto_save" type="checkbox" />
                自动保存决策历史
              </label>
            </div>
          </div>

          <div class="source-config-grid">
            <div class="source-config-card">
              <h3>车牌视频流输入</h3>

              <label>
                选择车牌视频源
                <select v-model="selectedFusionPlateSourceId" @change="applySelectedFusionVideoSource('plate')">
                  <option value="">请选择车牌视频源</option>
                  <option
                    v-for="item in plateVideoSources"
                    :key="item.id"
                    :value="item.id"
                  >
                    {{ item.name }}
                  </option>
                </select>
              </label>

              <div class="selected-source-summary">
                <strong>当前选择：</strong>
                <span>{{ selectedPlateVideoSource?.name || '未选择' }}</span>
                <p>{{ selectedPlateVideoSource?.description || '可在视频源管理中新增车牌视频流。' }}</p>
                <p v-if="selectedPlateVideoSource?.source_url">
                  地址：{{ selectedPlateVideoSource.source_url }}
                </p>
                <p v-else>
                  源 ID：{{ selectedPlateVideoSource?.source_id || '-' }}
                </p>
              </div>

              <details class="advanced-source" :open="!selectedFusionPlateSourceId">
                <summary>当前应用参数</summary>
                <pre class="json-box">{{ prettyJson(realtimeFusionConfig.plate) }}</pre>
              </details>
            </div>

            <div class="source-config-card">
              <h3>交警手势视频流输入</h3>

              <label>
                选择交警手势视频源
                <select v-model="selectedFusionTrafficSourceId" @change="applySelectedFusionVideoSource('traffic')">
                  <option value="">请选择交警手势视频源</option>
                  <option
                    v-for="item in trafficVideoSources"
                    :key="item.id"
                    :value="item.id"
                  >
                    {{ item.name }}
                  </option>
                </select>
              </label>

              <div class="selected-source-summary">
                <strong>当前选择：</strong>
                <span>{{ selectedTrafficVideoSource?.name || '未选择' }}</span>
                <p>{{ selectedTrafficVideoSource?.description || '可在视频源管理中新增交警手势视频流。' }}</p>
                <p v-if="selectedTrafficVideoSource?.source_url">
                  地址：{{ selectedTrafficVideoSource.source_url }}
                </p>
                <p v-else>
                  源 ID：{{ selectedTrafficVideoSource?.source_id || '-' }}；
                  Demo：{{ selectedTrafficVideoSource?.demo_file || '-' }}
                </p>
              </div>

              <details class="advanced-source" :open="!selectedFusionTrafficSourceId">
                <summary>当前应用参数</summary>
                <pre class="json-box">{{ prettyJson(realtimeFusionConfig.traffic) }}</pre>
              </details>
            </div>
          </div>

          <div class="metric-grid">
            <div class="metric-card">
              <span>车牌视频流</span>
              <strong>{{ realtimeFusionEvidence.plate ? '已接入' : '等待输入' }}</strong>
              <p>
                车牌数：
                {{ realtimeFusionEvidence.plate?.result?.plate_count ?? realtimeFusionEvidence.plate?.result?.result?.plate_count ?? '-' }}
              </p>
            </div>
            <div class="metric-card">
              <span>交警手势视频流</span>
              <strong>
                {{ realtimeFusionEvidence.traffic?.result?.gesture_name || realtimeFusionEvidence.traffic?.result?.gesture || '等待输入' }}
              </strong>
              <p>{{ realtimeFusionEvidence.traffic?.result?.traffic_command || '暂无交通指令' }}</p>
            </div>
            <div class="metric-card">
              <span>车主摄像头</span>
              <strong>
                {{ realtimeFusionEvidence.owner?.result?.gesture_name || realtimeFusionEvidence.owner?.result?.gesture || '等待输入' }}
              </strong>
              <p>{{ realtimeFusionEvidence.owner?.result?.description || realtimeFusionEvidence.owner?.result?.action || '暂无车主手势' }}</p>
            </div>
            <div class="metric-card">
              <span>融合智能体</span>
              <strong>{{ currentRealtimeDecision?.risk_level || '-' }}</strong>
              <p>风险分数：{{ currentRealtimeDecision?.risk_score ?? '-' }}</p>
            </div>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <div>
              <h2>车主摄像头实时输入</h2>
              <p>这里的识别结果会自动进入融合决策证据池。</p>
            </div>
          </div>
          <OwnerCameraPanel @recognized="handleOwnerCameraRecognized" />
        </div>

        <div class="panel span-5">
          <div class="panel-header">
            <h2>实时风险分析</h2>
          </div>

          <div v-if="currentRealtimeDecision" class="decision-card">
            <span class="risk-badge">{{ currentRealtimeDecision.risk_level || '-' }}</span>
            <h3>{{ currentRealtimeDecision.scenario || '暂无场景' }}</h3>
            <p>{{ currentRealtimeDecision.suggestion || '-' }}</p>

            <div class="kv-grid">
              <div>
                <span>风险分数</span>
                <strong>{{ currentRealtimeDecision.risk_score ?? '-' }}</strong>
              </div>
              <div>
                <span>控制建议</span>
                <strong>{{ currentRealtimeDecision.control_advice || '-' }}</strong>
              </div>
            </div>

            <div class="policy-box">
              <p><b>分析依据：</b>{{ currentRealtimeDecision.reason || '-' }}</p>
            </div>
          </div>

          <div v-else class="empty-state">
            启动融合监控后，系统会自动采集三路证据并生成风险分析。
          </div>
        </div>

        <div class="panel span-7">
          <div class="panel-header">
            <h2>当前三路融合证据</h2>
          </div>
          <pre class="json-box">{{ prettyJson({
            plate: realtimeFusionEvidence.plate,
            traffic: realtimeFusionEvidence.traffic,
            owner: realtimeFusionEvidence.owner,
            decision: currentRealtimeDecision
          }) }}</pre>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <h2>监控时间线</h2>
          </div>

          <div class="simple-list">
            <div v-for="item in realtimeFusionTimeline" :key="item.id" class="list-item">
              <strong>{{ item.created_at }} | {{ item.type }}</strong>
              <span>{{ item.message }}</span>
            </div>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <h2>决策历史</h2>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>场景</th>
                  <th>风险</th>
                  <th>分数</th>
                  <th>建议</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in fusionHistory" :key="item.id || item.decision_id">
                  <td>{{ item.id || '-' }}</td>
                  <td>{{ item.scenario || item.decision?.scenario || '-' }}</td>
                  <td>{{ item.risk_level || item.decision?.risk_level || '-' }}</td>
                  <td>{{ item.risk_score ?? item.decision?.risk_score ?? '-' }}</td>
                  <td>{{ shortText(item.suggestion || item.decision?.suggestion, 80) }}</td>
                  <td>{{ item.created_at || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>


      <section v-if="activeTab === 'sources'" class="page-grid">
        <div class="panel span-12 hero-panel">
          <div>
            <p class="eyebrow">Video Source Management</p>
            <h2>视频源管理</h2>
            <p>
              管理单路 RTSP / mock 视频流识别、全局自动监控，以及三路 worker 并发处理。
            </p>
          </div>
          <div class="badge-row">
            <span>RTSP</span>
            <span>Mock Stream</span>
            <span>MediaMTX 预留</span>
            <span>Worker 并发</span>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <div>
              <h2>新增 / 编辑视频源</h2>
              <p>保存车牌、交警手势、MediaMTX、自定义 RTSP 等视频源，供融合决策监控页直接选择。</p>
            </div>
            <button class="ghost-btn" @click="resetVideoSourceForm">清空表单</button>
          </div>

          <p v-if="errors.videoSource" class="error-line">{{ errors.videoSource }}</p>

          <div class="form-grid">
            <label>
              视频源名称
              <input v-model="videoSourceForm.name" placeholder="例如：行车记录仪车牌流" />
            </label>

            <label>
              视频源类型
              <select v-model="videoSourceForm.source_type">
                <option value="plate">车牌识别</option>
                <option value="traffic_gesture">交警手势识别</option>
                <option value="owner_gesture">车主手势识别</option>
                <option value="general">通用视频源</option>
              </select>
            </label>

            <label>
              Source Key
              <input v-model="videoSourceForm.source_key" placeholder="可留空，后端自动生成" />
            </label>

            <label>
              协议
              <select v-model="videoSourceForm.protocol">
                <option value="rtsp">RTSP</option>
                <option value="mediamtx">MediaMTX</option>
                <option value="mock">Mock</option>
                <option value="demo">Demo</option>
                <option value="file">File</option>
              </select>
            </label>

            <label>
              源 ID
              <input v-model="videoSourceForm.source_id" placeholder="live12 / plate_rtsp / traffic_rtsp" />
            </label>

            <label>
              RTSP / 视频流地址
              <input v-model="videoSourceForm.source_url" placeholder="rtsp://127.0.0.1:8554/plate" />
            </label>

            <label>
              Demo 文件
              <input v-model="videoSourceForm.demo_file" placeholder="traffic.png，可选" />
            </label>

            <label>
              读取帧数
              <input v-model.number="videoSourceForm.frame_count" type="number" min="1" />
            </label>

            <label>
              抽样间隔
              <input v-model.number="videoSourceForm.sample_interval" type="number" min="1" />
            </label>

            <label>
              预热帧数
              <input v-model.number="videoSourceForm.warmup_frames" type="number" min="0" />
            </label>

            <label class="checkbox-line">
              <input v-model="videoSourceForm.use_mock_frame" type="checkbox" />
              使用 mock / demo
            </label>

            <label class="checkbox-line">
              <input v-model="videoSourceForm.enabled" type="checkbox" />
              启用
            </label>
          </div>

          <label>
            描述
            <input v-model="videoSourceForm.description" placeholder="说明这个视频源用于什么场景" />
          </label>

          <div class="button-row source-form-buttons">
            <button :disabled="loading.videoSource" @click="saveVideoSource">
              {{ videoSourceForm.id ? '保存修改' : '新增视频源' }}
            </button>
            <button class="ghost-btn" @click="loadVideoSources">刷新视频源列表</button>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <h2>已保存视频源列表</h2>
          </div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>名称</th>
                  <th>类型</th>
                  <th>源 ID</th>
                  <th>地址 / Demo</th>
                  <th>Mock</th>
                  <th>检测</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in videoSources" :key="item.id">
                  <td>{{ item.id }}</td>
                  <td>{{ item.name }}</td>
                  <td>{{ item.source_type }}</td>
                  <td>{{ item.source_id || '-' }}</td>
                  <td>{{ shortText(item.source_url || item.demo_file || '-', 60) }}</td>
                  <td>{{ item.use_mock_frame ? '是' : '否' }}</td>
                  <td>
                    <button class="small-btn" :disabled="loading.videoSource" @click="checkVideoSource(item)">
                      检测
                    </button>
                    <p class="source-check-text" v-if="videoSourceCheckResults[item.id]">
                      {{ videoSourceCheckResults[item.id].online ? '在线' : '不可用' }}：
                      {{ videoSourceCheckResults[item.id].message }}
                    </p>
                  </td>
                  <td>
                    <div class="button-row">
                      <button class="small-btn" @click="editVideoSource(item)">编辑</button>
                      <button class="small-btn ghost-btn" @click="deleteVideoSource(item)">删除</button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>


        <div class="panel span-6">
          <div class="panel-header">
            <h2>单路视频流识别</h2>
          </div>

          <div class="form-grid">
            <label>
              视频源 ID
              <input v-model="streamForm.source_id" />
            </label>
            <label>
              任务类型
              <select v-model="streamForm.task_type">
                <option value="plate">车牌识别</option>
                <option value="owner_gesture">车主手势</option>
                <option value="traffic_gesture">交警手势</option>
              </select>
            </label>
            <label>
              读取帧数
              <input v-model.number="streamForm.frame_count" type="number" min="1" />
            </label>
            <label>
              抽样间隔
              <input v-model.number="streamForm.sample_interval" type="number" min="1" />
            </label>
            <label class="checkbox-line">
              <input v-model="streamForm.use_mock_frame" type="checkbox" />
              使用 mock 帧
            </label>
          </div>

          <button :disabled="loading.stream" @click="recognizeStream">
            {{ loading.stream ? '识别中...' : '开始视频流识别' }}
          </button>

          <p v-if="errors.stream" class="error-line">{{ errors.stream }}</p>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>全局自动监控</h2>
          </div>

          <div class="form-grid">
            <label>
              任务类型
              <select v-model="monitorForm.task_type">
                <option value="plate">车牌识别</option>
                <option value="owner_gesture">车主手势</option>
                <option value="traffic_gesture">交警手势</option>
              </select>
            </label>
            <label>
              周期秒数
              <input v-model.number="monitorForm.interval_seconds" type="number" min="5" />
            </label>
            <label>
              每次读取帧数
              <input v-model.number="monitorForm.frame_count" type="number" min="1" />
            </label>
            <label>
              抽样间隔
              <input v-model.number="monitorForm.sample_interval" type="number" min="1" />
            </label>
            <label class="checkbox-line">
              <input v-model="monitorForm.use_mock_frame" type="checkbox" />
              使用 mock 帧
            </label>
          </div>

          <div class="button-row">
            <button :disabled="loading.monitor" @click="startMonitor">启动监控</button>
            <button class="ghost-btn" :disabled="loading.monitor" @click="stopMonitor">停止监控</button>
          </div>

          <div class="policy-box">
            <p><b>当前状态：</b>{{ monitorStatus?.running ? '运行中' : '未运行' }}</p>
            <p><b>任务类型：</b>{{ monitorStatus?.task_type || '-' }}</p>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <div>
              <h2>多路视频流并发处理</h2>
              <p>默认启动车牌、交警手势、车主手势三个 worker，并开启融合决策线程。</p>
            </div>
            <div class="button-row">
              <button :disabled="loading.multistream" @click="startMultistream">启动三路并发</button>
              <button class="ghost-btn" :disabled="loading.multistream" @click="stopMultistream">停止并发</button>
            </div>
          </div>

          <p v-if="errors.multistream" class="error-line">{{ errors.multistream }}</p>

          <div class="worker-grid">
            <div v-for="worker in multistreamWorkers" :key="worker.worker_id" class="worker-card">
              <strong>{{ worker.worker_id }}</strong>
              <span>{{ worker.task_type }} / {{ worker.status }}</span>
              <p>周期：{{ worker.cycle_count || 0 }} | 成功：{{ worker.success_count || 0 }} | 失败：{{ worker.fail_count || 0 }}</p>
              <p>延迟：{{ worker.last_latency_ms ?? '-' }} ms | 实时：{{ worker.is_realtime === false ? '否' : '是' }}</p>
            </div>
          </div>

          <h3>最近并发记录</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Worker</th>
                  <th>任务</th>
                  <th>输入</th>
                  <th>成功</th>
                  <th>延迟</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in multistreamLatest.slice(0, 10)" :key="item.id">
                  <td>{{ item.id }}</td>
                  <td>{{ item.worker_id }}</td>
                  <td>{{ item.task_type }}</td>
                  <td>{{ item.input_type }}</td>
                  <td>{{ item.success ? '是' : '否' }}</td>
                  <td>{{ item.latency_ms }} ms</td>
                  <td>{{ item.created_at }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel span-12" v-if="streamResult">
          <div class="panel-header">
            <h2>单路视频流识别结果</h2>
          </div>
          <pre class="json-box">{{ prettyJson(streamResult.result || streamResult) }}</pre>
        </div>
      </section>

      <section v-if="activeTab === 'test'" class="page-grid">
        <div class="panel span-12 hero-panel">
          <div>
            <p class="eyebrow">Model Test Center</p>
            <h2>模型测试中心</h2>
            <p>
              保留图片、视频、视频流和摄像头输入，方便单独验证每个识别模块。
            </p>
          </div>
        </div>

        <div class="panel span-12">
          <OwnerCameraPanel />
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>车主手势图片识别</h2>
          </div>
          <div class="upload-box">
            <input type="file" accept="image/*" @change="pickFile($event, 'ownerImage')" />
            <button :disabled="loading.ownerImage" @click="uploadOwnerImage">
              {{ loading.ownerImage ? '识别中...' : '开始图片识别' }}
            </button>
          </div>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>车主手势视频识别</h2>
          </div>
          <div class="upload-box">
            <input type="file" accept="video/*" @change="pickFile($event, 'ownerVideo')" />
            <div class="form-row">
              <label>
                抽帧间隔
                <input v-model.number="ownerFrameInterval" type="number" min="1" max="30" />
              </label>
              <label>
                触发阈值
                <input v-model.number="ownerStableThreshold" type="number" min="1" max="20" />
              </label>
            </div>
            <button :disabled="loading.ownerVideo" @click="uploadOwnerVideo">
              {{ loading.ownerVideo ? '识别中...' : '开始视频识别' }}
            </button>
          </div>
        </div>

        <p v-if="errors.owner" class="error-line span-12">{{ errors.owner }}</p>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>车牌图片识别</h2>
          </div>
          <div class="upload-box">
            <input type="file" accept="image/*" @change="pickFile($event, 'plateImage')" />
            <button :disabled="loading.plateImage" @click="uploadPlateImage">
              {{ loading.plateImage ? '识别中...' : '开始识别' }}
            </button>
          </div>
          <p v-if="errors.plate" class="error-line">{{ errors.plate }}</p>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>交警手势图片识别</h2>
          </div>
          <div class="upload-box">
            <input type="file" accept="image/*" @change="pickFile($event, 'trafficImage')" />
            <button :disabled="loading.trafficImage" @click="uploadTrafficImage">
              {{ loading.trafficImage ? '识别中...' : '开始识别' }}
            </button>
          </div>
          <p v-if="errors.traffic" class="error-line">{{ errors.traffic }}</p>
        </div>

        <div class="panel span-5">
          <div class="panel-header">
            <h2>车主手势结果</h2>
          </div>
          <div v-if="ownerLatestResult" class="result-card">
            <div class="result-main">
              <span>{{ ownerResult.gesture_name || '暂无结果' }}</span>
              <strong>{{ ownerResult.gesture || '-' }}</strong>
            </div>
            <div class="kv-grid">
              <div>
                <span>置信度</span>
                <strong>{{ percent(ownerResult.confidence) }}</strong>
              </div>
              <div>
                <span>车辆动作</span>
                <strong>{{ ownerResult.description || ownerResult.action || '-' }}</strong>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">上传车主手势图片或视频后显示结果。</div>
        </div>

        <div class="panel span-7">
          <div class="panel-header">
            <h2>手部关键点骨架</h2>
          </div>
          <div v-if="ownerLatestResult?.output_image_url" class="image-preview">
            <img :src="assetUrl(ownerLatestResult.output_image_url)" alt="车主手势识别标注图" />
          </div>
          <div v-else class="empty-preview">识别后显示 MediaPipe 手部骨架标注图。</div>

          <div v-if="ownerKeyLandmarks.length" class="landmark-strip">
            <div v-for="point in ownerKeyLandmarks" :key="point.index">
              <span>#{{ point.index }}</span>
              <strong>x {{ point.x }}</strong>
              <strong>y {{ point.y }}</strong>
            </div>
          </div>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>车牌识别结果</h2>
          </div>
          <div v-if="plateResult" class="result-card">
            <div class="image-preview">
              <img v-if="plateResult.output_image_url" :src="assetUrl(plateResult.output_image_url)" />
            </div>
            <pre class="json-box">{{ prettyJson(plateResult.result) }}</pre>
          </div>
          <div v-else class="empty-state">上传车牌图片后显示结果。</div>
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>交警手势结果</h2>
          </div>
          <div v-if="trafficResult" class="result-card">
            <div class="image-preview">
              <img v-if="trafficResult.output_image_url" :src="assetUrl(trafficResult.output_image_url)" />
            </div>
            <pre class="json-box">{{ prettyJson(trafficResult.result) }}</pre>
          </div>
          <div v-else class="empty-state">上传交警手势图片后显示结果。</div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <h2>预定义车主手势映射</h2>
          </div>
          <div class="gesture-grid">
            <div v-for="item in supportedOwnerGestures" :key="item.gesture" class="gesture-item">
              <strong>{{ item.name }}</strong>
              <span>{{ item.gesture }}</span>
              <p>{{ item.action }}</p>
            </div>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'performance'" class="page-grid">
        <div class="panel span-12 hero-panel">
          <div>
            <p class="eyebrow">Performance Test</p>
            <h2>性能测试</h2>
            <p>
              对健康检查、图片识别、视频流 mock、融合决策等链路进行端到端延迟测试。
            </p>
          </div>
          <button :disabled="loading.performance" @click="runPerformanceTest">
            {{ loading.performance ? '测试中...' : '运行端到端延迟测试' }}
          </button>
        </div>

        <p v-if="errors.performance" class="error-line span-12">{{ errors.performance }}</p>

        <div class="panel span-4">
          <div class="panel-header">
            <h2>测试参数</h2>
          </div>
          <div class="form-grid one-col">
            <label>
              重复次数
              <input v-model.number="performanceForm.repeat" type="number" min="1" max="10" />
            </label>
            <label>
              实时阈值 ms
              <input v-model.number="performanceForm.threshold_ms" type="number" min="100" />
            </label>
          </div>
          <div class="policy-box">
            <p><b>测试目标：</b>{{ performanceForm.targets.join(', ') }}</p>
          </div>
        </div>

        <div class="panel span-8">
          <div class="panel-header">
            <h2>性能摘要</h2>
          </div>
          <div class="metric-grid">
            <div class="metric-card">
              <span>平均延迟</span>
              <strong>{{ performanceSummary?.avg_latency_ms ?? '-' }} ms</strong>
              <p>avg latency</p>
            </div>
            <div class="metric-card">
              <span>P95 延迟</span>
              <strong>{{ performanceSummary?.p95_latency_ms ?? '-' }} ms</strong>
              <p>p95 latency</p>
            </div>
            <div class="metric-card">
              <span>通过率</span>
              <strong>{{ percent(performanceSummary?.pass_rate ?? 0) }}</strong>
              <p>{{ performanceSummary?.pass_count ?? '-' }} / {{ performanceSummary?.count ?? '-' }}</p>
            </div>
            <div class="metric-card">
              <span>实时性</span>
              <strong>{{ performanceSummary?.is_realtime === false ? '未完全满足' : '满足或未测试' }}</strong>
              <p>阈值 {{ performanceForm.threshold_ms }} ms</p>
            </div>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <h2>延迟记录</h2>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>目标</th>
                  <th>成功</th>
                  <th>延迟</th>
                  <th>是否实时</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in latencyRecords" :key="item.id || item.record_id">
                  <td>{{ item.id || item.record_id || '-' }}</td>
                  <td>{{ item.target || item.target_name || item.task_type || '-' }}</td>
                  <td>{{ item.success === false ? '否' : '是' }}</td>
                  <td>{{ item.latency_ms ?? '-' }} ms</td>
                  <td>{{ item.is_realtime === false ? '否' : '是' }}</td>
                  <td>{{ item.created_at || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'records'" class="page-grid">
        <div class="panel span-12">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Records</p>
              <h2>识别记录</h2>
            </div>
            <button class="small-btn" @click="refreshAll">刷新</button>
          </div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>任务</th>
                  <th>输入</th>
                  <th>文件</th>
                  <th>结果摘要</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in records" :key="item.id || item.record_id">
                  <td>{{ item.id || item.record_id }}</td>
                  <td>{{ item.task_type }}</td>
                  <td>{{ item.input_type }}</td>
                  <td>{{ shortText(item.original_filename || item.saved_filename, 30) }}</td>
                  <td>{{ shortText(item.result, 100) }}</td>
                  <td>{{ item.created_at || item.created_time || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel span-12">
          <div class="panel-header">
            <h2>告警中心</h2>
            <button class="small-btn" @click="testAlertAgent">触发评估</button>
            <span v-if="testAlertResult" class="test-alert-result">{{ testAlertResult }}</span>
          </div>
          <AlertDashboard />
        </div>

        <div class="panel span-6">
          <div class="panel-header">
            <h2>操作日志</h2>
          </div>

          <div class="simple-list">
            <div v-for="item in logs" :key="item.id || item.log_id" class="list-item">
              <strong>#{{ item.id || item.log_id }} {{ item.action || item.event_type || 'log' }}</strong>
              <span>{{ shortText(item.detail || item.message || item.created_at || item.created_time, 120) }}</span>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  padding: 28px;
  background: #f1f5f9;
  color: #0f172a;
}

.topbar {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  margin-bottom: 18px;
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  margin-bottom: 16px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(211, 222, 235, 0.9);
  border-radius: 12px;
  font-size: 13px;
  color: #506177;
}

.status-bar .status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f97316;
  box-shadow: 0 0 0 4px rgba(249, 115, 22, 0.10);
}

.status-bar .status-dot.ok {
  background: #22c55e;
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.12);
}

.status-bar .status-text {
  flex: 1;
}

.status-bar .small-btn {
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12px;
}

.eyebrow {
  margin: 0 0 6px;
  color: #2563eb;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 8px;
  font-size: 30px;
}

h2 {
  margin-bottom: 8px;
  font-size: 22px;
}

.subtitle {
  max-width: 820px;
  color: #475569;
  line-height: 1.7;
}

.status-card {
  min-width: 280px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 14px;
  border-radius: 16px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
}

.status-card p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 13px;
}

.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  background: #ef4444;
}

.status-dot.ok {
  background: #22c55e;
}

.nav-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
}

.nav-tabs button,
button {
  border: none;
  border-radius: 12px;
  padding: 10px 14px;
  background: #2563eb;
  color: #ffffff;
  font-weight: 800;
  cursor: pointer;
}

.nav-tabs button {
  background: #ffffff;
  color: #334155;
  border: 1px solid #e2e8f0;
}

.nav-tabs button.active {
  background: #2563eb;
  color: #ffffff;
  border-color: #2563eb;
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.small-btn {
  padding: 8px 10px;
  font-size: 12px;
}

.ghost-btn {
  background: #475569;
}

.page-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 16px;
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

.span-6 {
  grid-column: span 6;
}

.span-5 {
  grid-column: span 5;
}

.span-4 {
  grid-column: span 4;
}

.panel,
.metric-card {
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  padding: 18px;
  background: #ffffff;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
}

.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  background: linear-gradient(135deg, #ffffff, #eff6ff);
}

.hero-panel p {
  color: #475569;
  line-height: 1.7;
}

.badge-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.badge-row span,
.risk-badge {
  display: inline-flex;
  padding: 6px 10px;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 800;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.panel-header p {
  margin: 4px 0 0;
  color: #64748b;
}


.source-config-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

.source-config-card {
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 14px;
  background: #f8fafc;
}


.selected-source-summary {
  margin: 10px 0 12px;
  padding: 10px;
  border-radius: 12px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
}

.selected-source-summary strong,
.selected-source-summary span {
  display: inline;
}

.selected-source-summary p {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}

.advanced-source {
  margin-top: 10px;
}

.advanced-source summary {
  cursor: pointer;
  color: #2563eb;
  font-weight: 800;
  margin-bottom: 10px;
}

.source-config-card h3 {
  margin-bottom: 12px;
  font-size: 17px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-card span {
  display: block;
  color: #64748b;
  font-size: 13px;
  margin-bottom: 6px;
}

.metric-card strong {
  display: block;
  font-size: 24px;
  margin-bottom: 4px;
}

.metric-card p {
  color: #64748b;
  font-size: 13px;
  margin: 0;
}

.upload-box,
.form-grid {
  display: grid;
  gap: 12px;
}

.form-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-bottom: 12px;
}

.form-grid.one-col {
  grid-template-columns: 1fr;
}

.form-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

label {
  display: grid;
  gap: 6px;
  color: #475569;
  font-size: 13px;
  font-weight: 700;
}

input,
select {
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 9px 10px;
  background: #ffffff;
  color: #0f172a;
}

.checkbox-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.checkbox-line input {
  width: auto;
}

.button-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.error-line {
  padding: 10px 12px;
  border-radius: 12px;
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.result-card,
.decision-card,
.policy-box {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 14px;
  background: #f8fafc;
}

.result-main {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;
}

.result-main span {
  color: #64748b;
}

.result-main strong {
  font-size: 24px;
}

.kv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.kv-grid div {
  padding: 10px;
  border-radius: 12px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
}

.kv-grid span {
  display: block;
  color: #64748b;
  font-size: 12px;
  margin-bottom: 5px;
}

.kv-grid strong {
  display: block;
  word-break: break-word;
}

.image-preview {
  border-radius: 14px;
  overflow: hidden;
  background: #020617;
  border: 1px solid #cbd5e1;
  margin-bottom: 12px;
}

.image-preview img {
  display: block;
  width: 100%;
  max-height: 360px;
  object-fit: contain;
}

.empty-state,
.empty-preview {
  padding: 22px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  color: #64748b;
}

.landmark-strip,
.gesture-grid,
.worker-grid {
  display: grid;
  gap: 10px;
}

.landmark-strip {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

.gesture-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.worker-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 16px;
}

.landmark-strip div,
.gesture-item,
.worker-card {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 10px;
  background: #f8fafc;
}

.gesture-item strong,
.worker-card strong {
  display: block;
  margin-bottom: 4px;
}

.gesture-item span,
.worker-card span {
  display: block;
  color: #2563eb;
  font-size: 13px;
  font-weight: 800;
}

.gesture-item p,
.worker-card p {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
}

.simple-list {
  display: grid;
  gap: 10px;
}

.list-item {
  display: grid;
  gap: 4px;
  padding: 10px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.list-item span {
  color: #64748b;
  font-size: 13px;
}

.json-box {
  overflow: auto;
  max-height: 460px;
  padding: 12px;
  border-radius: 12px;
  background: #0f172a;
  color: #dbeafe;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
}


.source-form-buttons {
  margin-top: 14px;
}

.source-check-text {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.table-wrap {
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  border-bottom: 1px solid #e2e8f0;
  padding: 10px;
  text-align: left;
  vertical-align: top;
}

th {
  color: #475569;
  background: #f8fafc;
}

@media (max-width: 1080px) {
  .topbar,
  .hero-panel {
    flex-direction: column;
  }

  .span-8,
  .span-7,
  .span-6,
  .span-5,
  .span-4 {
    grid-column: span 12;
  }

  .metric-grid,
  .source-config-grid,
  .gesture-grid,
  .worker-grid,
  .landmark-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .form-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .app-shell {
    padding: 16px;
  }

  .metric-grid,
  .source-config-grid,
  .gesture-grid,
  .worker-grid,
  .landmark-strip,
  .kv-grid {
    grid-template-columns: 1fr;
  }
}
</style>
