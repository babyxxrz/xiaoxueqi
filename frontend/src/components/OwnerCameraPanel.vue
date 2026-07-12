<template>
  <section class="camera-panel">
    <div class="camera-heading">
      <div>
        <p class="section-kicker">DRIVER GESTURE</p>
        <h3>车主摄像头实时识别</h3>
        <p>按照任务要求识别静态与动态手势，并联动车载控制面板。</p>
      </div>

      <div class="status-row">
        <span :class="['status-pill', cameraRunning && 'online']">
          {{ cameraRunning ? '摄像头在线' : '摄像头未启动' }}
        </span>
        <span :class="['status-pill', loopRunning && 'online']">
          {{ loopRunning ? '正在识别' : '识别未开始' }}
        </span>
      </div>
    </div>

    <div class="camera-grid">
      <div class="video-card">
        <div class="video-stage">
          <video ref="videoRef" autoplay muted playsinline></video>
          <canvas ref="overlayCanvasRef"></canvas>
          <canvas ref="captureCanvasRef" class="hidden-canvas"></canvas>
          <div v-if="!cameraRunning" class="video-placeholder">等待打开摄像头</div>
        </div>

        <div class="control-row">
          <button v-if="!cameraRunning" @click="startCamera">打开摄像头</button>
          <button v-else class="secondary" @click="stopCamera">关闭摄像头</button>
          <button :disabled="!cameraRunning || loopRunning" @click="startLoop">开始实时识别</button>
          <button class="danger" :disabled="!loopRunning" @click="stopLoop">停止识别</button>
          <button
            class="secondary single-recognition-button"
            :disabled="!cameraRunning || loopRunning || requesting"
            :aria-busy="requesting"
            @click="captureAndRecognize"
          >
            {{
              loopRunning
                ? '实时识别中'
                : requesting
                  ? '识别中...'
                  : '单次识别'
            }}
          </button>
        </div>

        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      </div>

      <div class="result-card">
        <div class="gesture-result" :class="`panel-${panelState}`">
          <span>已确认动作</span>
          <strong>{{ result.gesture_name || '等待下一个动作' }}</strong>
          <small>
            {{
              panelState === 'confirmed'
                ? result.gesture || '-'
                : '完成完整动作后显示结果'
            }}
          </small>
        </div>

        <div class="metric-grid">
          <div>
            <span>置信度</span>
            <strong>{{ confidenceText }}</strong>
          </div>
          <div>
            <span>识别延迟</span>
            <strong>{{ latencyText }}</strong>
          </div>
          <div>
            <span>车辆动作</span>
            <strong>{{ result.description || result.action || '-' }}</strong>
          </div>
          <div>
            <span>当前功能</span>
            <strong>{{ currentFunctionText }}</strong>
          </div>
          <div>
            <span>音量</span>
            <strong>{{ vehicleState.volume ?? 50 }}</strong>
          </div>
          <div>
            <span>电话状态</span>
            <strong>{{ vehicleState.phone_status || '空闲' }}</strong>
          </div>
          <div>
            <span>空调温度</span>
            <strong>{{ vehicleState.temperature ?? 24 }} ℃</strong>
          </div>
          <div>
            <span>动作状态</span>
            <strong :class="{ triggered: panelState === 'confirmed' }">
              {{ actionStatusText }}
            </strong>
          </div>
          <div>
            <span>识别流程</span>
            <strong>{{ panelStatusText }}</strong>
          </div>
          <div>
            <span>方向校正</span>
            <strong>{{ result.camera_mirror_corrected ? '镜像方向已校正' : '标准方向' }}</strong>
          </div>
        </div>

        <div class="volume-state">
          <div>
            <span>媒体音量</span>
            <strong>{{ vehicleState.volume ?? 50 }}</strong>
          </div>
          <div class="volume-track">
            <span :style="{ width: `${vehicleState.volume ?? 50}%` }"></span>
          </div>
        </div>

        <div class="vehicle-state">
          <span>车辆交互系统</span>
          <strong>{{ vehicleState.system_awake ? '已唤醒' : '待机' }}</strong>
        </div>
      </div>
    </div>

    <section class="mapping-panel">
      <div class="mapping-heading">
        <div>
          <span>任务要求映射</span>
          <strong>至少 6 类车主手势 · 当前支持 9 条控制指令</strong>
        </div>
        <small>
          摄像头镜像方向已校正。动作完成并确认后才更新右侧面板；
          显示结束后进入“等待下一个动作”。
        </small>
      </div>

      <div class="mapping-grid">
        <article
          v-for="item in gestureMappings"
          :key="item.gesture"
          :class="{ active: panelState === 'confirmed' && result.gesture === item.gesture }"
        >
          <span>{{ item.type === 'dynamic' ? '动态' : '静态' }}</span>
          <strong>{{ item.gestureName }}</strong>
          <small>{{ item.description }}</small>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import { API_BASE, authHeaders } from '../api'

const emit = defineEmits(['recognized'])

const videoRef = ref(null)
const overlayCanvasRef = ref(null)
const captureCanvasRef = ref(null)
const streamRef = ref(null)
const timerRef = ref(null)
const abortControllerRef = ref(null)
const drawFrameRef = ref(null)
const displayTimerRef = ref(null)
const lastCommittedEventId = ref(0)
const panelState = ref('waiting')

const cameraRunning = ref(false)
const loopRunning = ref(false)
const requesting = ref(false)
const errorMessage = ref('')

const result = reactive({
  gesture: '',
  gesture_name: '',
  confidence: null,
  action: '',
  description: '',
  latency_ms: null,
  landmarks: [],
  triggered: false,
  stable_count: 0,
  required_stable_frames: 2,
  dynamic_gesture: '',
  cooldown_remaining_ms: 0,
  gesture_mapping_version: '',
  dynamic_phase: 'idle',
  motion_state: 'idle',
  motion_debug: {},
  event_id: 0,
  camera_mirror_corrected: true,
})

const vehicleState = reactive({
  system_awake: false,
  current_function: 'home',
  volume: 50,
  temperature: 24,
  phone_status: '空闲',
  last_action: 'none',
  last_description: '未触发车辆控制',
})

const functionLabels = {
  home: '主页',
  media: '媒体',
  climate: '空调',
  phone: '电话',
  navigation: '导航',
}

const gestureMappings = [
  { gesture: 'open_palm', gestureName: '手掌张开', description: '启动 / 唤醒系统', type: 'static' },
  { gesture: 'fist', gestureName: '握拳', description: '确认 / 执行', type: 'static' },
  { gesture: 'circle_clockwise', gestureName: '单指顺时针画圈', description: '调高音量', type: 'dynamic' },
  { gesture: 'circle_counterclockwise', gestureName: '单指逆时针画圈', description: '调低音量', type: 'dynamic' },
  { gesture: 'swipe_left', gestureName: '向左滑动', description: '切换到上一功能', type: 'dynamic' },
  { gesture: 'swipe_right', gestureName: '向右滑动', description: '切换到下一功能', type: 'dynamic' },
  { gesture: 'thumb_up', gestureName: '拇指向上', description: '接听电话', type: 'static' },
  { gesture: 'thumb_down', gestureName: '拇指向下', description: '挂断电话', type: 'static' },
  { gesture: 'wave', gestureName: '挥手', description: '返回主页', type: 'dynamic' },
]

const currentFunctionText = computed(() =>
  functionLabels[vehicleState.current_function] ||
  vehicleState.current_function ||
  '主页',
)

const actionStatusText = computed(() =>
  panelState.value === 'confirmed'
    ? '动作已确认并执行'
    : '等待动作完成',
)

const panelStatusText = computed(() => {
  if (panelState.value === 'confirmed') {
    return '结果稳定显示中'
  }
  return '等待下一个完整动作'
})


const confidenceText = computed(() =>
  result.confidence === null || result.confidence === undefined
    ? '-'
    : `${Math.round(Number(result.confidence) * 100)}%`,
)

const latencyText = computed(() =>
  result.latency_ms === null || result.latency_ms === undefined
    ? '-'
    : `${Math.round(result.latency_ms)} ms`,
)

async function startCamera() {
  errorMessage.value = ''

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 640 },
        height: { ideal: 480 },
        facingMode: 'user',
      },
      audio: false,
    })

    streamRef.value = stream
    videoRef.value.srcObject = stream
    cameraRunning.value = true

    await nextTick()
    resizeOverlay()
  } catch (error) {
    errorMessage.value = `摄像头启动失败：${error.message}`
  }
}

function stopCamera() {
  stopLoop()

  streamRef.value?.getTracks().forEach((track) => track.stop())
  streamRef.value = null

  if (videoRef.value) {
    videoRef.value.srcObject = null
  }

  cameraRunning.value = false
  clearOverlay()
  resetCommittedPanel()
}

function startLoop() {
  if (!cameraRunning.value || loopRunning.value) return
  stopLoop()
  loopRunning.value = true
  runLoopStep()
}

async function runLoopStep() {
  if (!loopRunning.value || !cameraRunning.value) return

  await captureAndRecognize()

  if (loopRunning.value && cameraRunning.value) {
    timerRef.value = window.setTimeout(runLoopStep, 85)
  }
}

function stopLoop() {
  loopRunning.value = false

  if (timerRef.value) {
    window.clearTimeout(timerRef.value)
    timerRef.value = null
  }

  abortControllerRef.value?.abort()
  abortControllerRef.value = null
}

function canvasToBlob(canvas) {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, 'image/jpeg', 0.54)
  })
}

async function captureAndRecognize() {
  if (!cameraRunning.value || requesting.value) return

  const video = videoRef.value
  const canvas = captureCanvasRef.value

  if (!video?.videoWidth || !video?.videoHeight || !canvas) return

  requesting.value = true
  errorMessage.value = ''

  try {
    const scale = Math.min(1, 384 / video.videoWidth)
    const targetWidth = Math.round(video.videoWidth * scale)
    const targetHeight = Math.round(video.videoHeight * scale)

    if (canvas.width !== targetWidth) canvas.width = targetWidth
    if (canvas.height !== targetHeight) canvas.height = targetHeight

    const captureContext = canvas.getContext('2d', { alpha: false })
    captureContext.drawImage(video, 0, 0, canvas.width, canvas.height)

    const blob = await canvasToBlob(canvas)
    if (!blob) throw new Error('摄像头帧生成失败')

    const formData = new FormData()
    formData.append('file', blob, `owner_camera_${Date.now()}.jpg`)

    const controller = new AbortController()
    abortControllerRef.value = controller
    const timeoutId = window.setTimeout(() => controller.abort(), 5000)

    const started = performance.now()
    let response
    try {
      response = await fetch(`${API_BASE}/api/gesture/owner/camera-fast-frame`, {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
        signal: controller.signal,
      })
    } finally {
      window.clearTimeout(timeoutId)
      if (abortControllerRef.value === controller) {
        abortControllerRef.value = null
      }
    }

    const data = await response.json()
    const clientLatency = performance.now() - started

    if (!response.ok || data.status === 'error') {
      throw new Error(data?.detail || data?.message || '识别失败')
    }

    updateResult(data, clientLatency)
  } catch (error) {
    if (error?.name !== 'AbortError') {
      errorMessage.value = `识别失败：${error.message}`
    }
  } finally {
    requesting.value = false
  }
}

function clearDisplayTimer() {
  if (displayTimerRef.value) {
    window.clearTimeout(displayTimerRef.value)
    displayTimerRef.value = null
  }
}

function resetCommittedPanel() {
  clearDisplayTimer()
  panelState.value = 'waiting'

  Object.assign(result, {
    gesture: '',
    gesture_name: '',
    confidence: null,
    action: '',
    description: '',
    latency_ms: null,
    triggered: false,
    stable_count: 0,
    required_stable_frames: 3,
    dynamic_gesture: '',
    cooldown_remaining_ms: 0,
    dynamic_phase: 'idle',
    motion_state: 'waiting',
    motion_debug: {},
    event_id: 0,
  })
}

function scheduleWaitingState(eventId) {
  clearDisplayTimer()

  displayTimerRef.value = window.setTimeout(() => {
    // 新动作已经提交时，旧定时器不能清空新结果。
    if (Number(result.event_id || 0) !== Number(eventId || 0)) {
      return
    }

    panelState.value = 'waiting'
    result.gesture = ''
    result.gesture_name = ''
    result.confidence = null
    result.action = ''
    result.description = ''
    result.triggered = false
    result.dynamic_gesture = ''
    result.dynamic_phase = 'waiting'
    result.motion_state = 'waiting'
  }, 1800)
}

function shouldCommitPayload(payload) {
  const eventId = Number(payload.event_id || 0)
  const committed = Boolean(
    payload.display_committed
    || payload.triggered
    || payload.dynamic_phase === 'event'
  )

  if (!committed) return false

  if (
    eventId > 0
    && eventId === Number(lastCommittedEventId.value || 0)
  ) {
    return false
  }

  return true
}

function commitPayload(payload, latency) {
  const eventId = Number(payload.event_id || Date.now())

  lastCommittedEventId.value = eventId
  panelState.value = 'confirmed'

  result.gesture = payload.gesture || ''
  result.gesture_name = payload.gesture_name || ''
  result.confidence = payload.confidence
  result.action = payload.action || ''
  result.description = payload.description || ''
  result.latency_ms = latency
  result.triggered = true
  result.stable_count = Number(payload.stable_count || 0)
  result.required_stable_frames = Number(
    payload.required_stable_frames || 3,
  )
  result.dynamic_gesture = payload.dynamic_gesture || ''
  result.cooldown_remaining_ms = Number(
    payload.cooldown_remaining_ms || 0,
  )
  result.gesture_mapping_version =
    payload.gesture_mapping_version || ''
  result.dynamic_phase = payload.dynamic_phase || 'event'
  result.motion_state = 'confirmed'
  result.motion_debug = payload.motion_debug || {}
  result.event_id = eventId
  result.camera_mirror_corrected =
    payload.camera_mirror_corrected !== false

  scheduleWaitingState(eventId)

  emit('recognized', {
    status: 'success',
    task_type: 'owner_gesture',
    input_type: 'camera_fast_frame',
    latency_ms: latency,
    created_at: new Date().toISOString(),
    result: {
      ...payload,
      vehicle_state: { ...vehicleState },
    },
  })
}

function updateResult(data, clientLatency) {
  const payload = data.result || data
  const state = payload.vehicle_state || {}
  const latency = data.latency_ms ?? clientLatency

  // 骨架和车辆状态仍然实时更新；右侧主结果只在动作确认时更新。
  result.landmarks = payload.landmarks || []
  Object.assign(vehicleState, state)
  drawLandmarks(result.landmarks)

  if (shouldCommitPayload(payload)) {
    commitPayload(payload, latency)
  }
}


function resizeOverlay() {
  const video = videoRef.value
  const canvas = overlayCanvasRef.value
  if (!video || !canvas) return

  const rect = video.getBoundingClientRect()
  const width = Math.max(1, Math.round(rect.width))
  const height = Math.max(1, Math.round(rect.height))
  if (canvas.width !== width) canvas.width = width
  if (canvas.height !== height) canvas.height = height
}

function clearOverlay() {
  const canvas = overlayCanvasRef.value
  if (!canvas) return
  canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height)
}

function drawLandmarks(landmarks) {
  if (drawFrameRef.value) {
    window.cancelAnimationFrame(drawFrameRef.value)
  }

  drawFrameRef.value = window.requestAnimationFrame(() => {
    resizeOverlay()

    const canvas = overlayCanvasRef.value
    if (!canvas) return

    const context = canvas.getContext('2d')
    context.clearRect(0, 0, canvas.width, canvas.height)

    if (!Array.isArray(landmarks) || !landmarks.length) return

    const connections = [
      [0, 1], [1, 2], [2, 3], [3, 4],
      [0, 5], [5, 6], [6, 7], [7, 8],
      [5, 9], [9, 10], [10, 11], [11, 12],
      [9, 13], [13, 14], [14, 15], [15, 16],
      [13, 17], [17, 18], [18, 19], [19, 20],
      [0, 17],
    ]

    const pointMap = new Map(
      landmarks.map((landmark) => [
        Number(landmark.index),
        {
          x: Number(landmark.x) * canvas.width,
          y: Number(landmark.y) * canvas.height,
        },
      ]),
    )

    context.lineWidth = 3
    context.strokeStyle = '#22d3ee'
    context.fillStyle = '#fb923c'
    context.lineCap = 'round'

    connections.forEach(([from, to]) => {
      const startPoint = pointMap.get(from)
      const endPoint = pointMap.get(to)
      if (!startPoint || !endPoint) return

      context.beginPath()
      context.moveTo(startPoint.x, startPoint.y)
      context.lineTo(endPoint.x, endPoint.y)
      context.stroke()
    })

    pointMap.forEach((point) => {
      context.beginPath()
      context.arc(point.x, point.y, 4, 0, Math.PI * 2)
      context.fill()
    })
  })
}

onBeforeUnmount(() => {
  clearDisplayTimer()
  stopCamera()
  if (drawFrameRef.value) window.cancelAnimationFrame(drawFrameRef.value)
  window.removeEventListener('resize', resizeOverlay)
})

window.addEventListener('resize', resizeOverlay)
</script>

<style scoped>
.camera-panel {
  padding: 20px;
  border: 1px solid rgba(34, 211, 238, 0.18);
  border-radius: 18px;
  background: rgba(8, 20, 38, 0.88);
}

.camera-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.camera-heading h3 {
  margin: 3px 0 6px;
  color: #f8fafc;
  font-size: 21px;
}

.camera-heading p {
  margin: 0;
  color: #94a3b8;
}

.section-kicker {
  color: #22d3ee !important;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.14em;
}

.status-row,
.control-row {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

.status-pill {
  height: fit-content;
  padding: 6px 10px;
  border-radius: 999px;
  background: #273449;
  color: #94a3b8;
  font-size: 12px;
}

.status-pill.online {
  background: rgba(16, 185, 129, 0.16);
  color: #6ee7b7;
}

.camera-grid {
  display: grid;
  grid-template-columns: minmax(340px, 1.25fr) minmax(300px, 0.75fr);
  gap: 16px;
}

.video-card,
.result-card {
  padding: 14px;
  border: 1px solid #26354d;
  border-radius: 16px;
  background: #0a1628;
}

.video-stage {
  position: relative;
  min-height: 330px;
  overflow: hidden;
  border-radius: 13px;
  background: #020617;
}

.video-stage video {
  display: block;
  width: 100%;
  min-height: 330px;
  object-fit: cover;
}

.video-stage canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.video-stage .hidden-canvas {
  display: none;
}

.video-placeholder {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #64748b;
}

.control-row {
  margin-top: 12px;
}

.control-row button {
  padding: 9px 12px;
  border: 0;
  border-radius: 10px;
  background: #0891b2;
  color: #ffffff;
  font-weight: 800;
}

.control-row button.secondary {
  background: #334155;
}

.control-row button.danger {
  background: #b91c1c;
}

.control-row button:disabled {
  opacity: 0.48;
  cursor: not-allowed;
}

.gesture-result {
  display: grid;
  gap: 6px;
  padding: 16px;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(8, 145, 178, 0.18), rgba(37, 99, 235, 0.1));
}

.gesture-result span,
.metric-grid span,
.vehicle-state span {
  color: #94a3b8;
  font-size: 12px;
}

.gesture-result strong {
  color: #f8fafc;
  font-size: 28px;
}

.gesture-result small {
  color: #67e8f9;
}

.gesture-result.panel-waiting {
  background: linear-gradient(
    135deg,
    rgba(51, 65, 85, 0.22),
    rgba(15, 23, 42, 0.18)
  );
}

.gesture-result.panel-confirmed {
  border: 1px solid rgba(45, 212, 191, 0.26);
  background: linear-gradient(
    135deg,
    rgba(13, 148, 136, 0.22),
    rgba(37, 99, 235, 0.12)
  );
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.metric-grid div,
.vehicle-state {
  padding: 11px;
  border: 1px solid #26354d;
  border-radius: 12px;
  background: #0d1b30;
}

.metric-grid strong,
.vehicle-state strong {
  display: block;
  margin-top: 5px;
  color: #e2e8f0;
  word-break: break-word;
}

.volume-state {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid rgba(34, 211, 238, 0.14);
  border-radius: 14px;
  background: rgba(8, 20, 38, 0.72);
}

.volume-state > div:first-child {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.volume-state span {
  color: #94a3b8;
  font-size: 13px;
}

.volume-state strong {
  color: #e2e8f0;
}

.volume-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}

.volume-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #22d3ee, #5eead4);
  transition: width 0.25s ease;
}

.triggered {
  color: #5eead4 !important;
}

.mapping-panel {
  margin-top: 18px;
  padding: 18px;
  border: 1px solid rgba(94, 234, 212, 0.16);
  border-radius: 16px;
  background: rgba(6, 18, 34, 0.76);
}

.mapping-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.mapping-heading > div {
  display: grid;
  gap: 5px;
}

.mapping-heading span,
.mapping-heading small {
  color: #8fa9c6;
  font-size: 12px;
}

.mapping-heading strong {
  color: #e2e8f0;
}

.mapping-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.mapping-grid article {
  display: grid;
  gap: 5px;
  min-height: 88px;
  padding: 12px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 12px;
  background: rgba(15, 29, 49, 0.76);
  transition: border-color 0.2s ease, background 0.2s ease;
}

.mapping-grid article.active {
  border-color: rgba(94, 234, 212, 0.72);
  background: rgba(13, 148, 136, 0.14);
}

.mapping-grid article > span {
  width: fit-content;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(34, 211, 238, 0.1);
  color: #67e8f9;
  font-size: 11px;
}

.mapping-grid article strong {
  color: #f8fafc;
  font-size: 14px;
}

.mapping-grid article small {
  color: #8fa9c6;
  line-height: 1.5;
}

.vehicle-state {
  display: flex;
  justify-content: space-between;
  margin-top: 12px;
}

.error-text {
  color: #fca5a5;
}

@media (max-width: 980px) {
  .camera-heading,
  .camera-grid {
    grid-template-columns: 1fr;
  }

  .camera-heading {
    flex-direction: column;
  }
}

.single-recognition-button {
  min-width: 112px;
  white-space: nowrap;
}
.single-recognition-button:disabled { transform: none; }
</style>
