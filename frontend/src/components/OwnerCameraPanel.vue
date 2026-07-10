<template>
  <section class="camera-panel">
    <div class="camera-heading">
      <div>
        <p class="section-kicker">DRIVER GESTURE</p>
        <h3>车主摄像头实时识别</h3>
        <p>采集手部关键点并映射车辆控制操作。</p>
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
          <button class="secondary" :disabled="!cameraRunning || requesting" @click="captureAndRecognize">
            单次识别
          </button>
        </div>

        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      </div>

      <div class="result-card">
        <div class="gesture-result">
          <span>当前手势</span>
          <strong>{{ result.gesture_name || '等待识别' }}</strong>
          <small>{{ result.gesture || '-' }}</small>
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
            <strong>{{ vehicleState.current_function || 'home' }}</strong>
          </div>
          <div>
            <span>音量</span>
            <strong>{{ vehicleState.volume ?? 50 }}</strong>
          </div>
          <div>
            <span>电话状态</span>
            <strong>{{ vehicleState.phone_status || '空闲' }}</strong>
          </div>
        </div>

        <div class="vehicle-state">
          <span>车辆交互系统</span>
          <strong>{{ vehicleState.system_awake ? '已唤醒' : '待机' }}</strong>
        </div>
      </div>
    </div>
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
})

const vehicleState = reactive({
  system_awake: false,
  current_function: 'home',
  volume: 50,
  phone_status: '空闲',
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
}

function startLoop() {
  if (!cameraRunning.value) return
  stopLoop()
  loopRunning.value = true
  captureAndRecognize()
  timerRef.value = window.setInterval(captureAndRecognize, 400)
}

function stopLoop() {
  if (timerRef.value) {
    window.clearInterval(timerRef.value)
    timerRef.value = null
  }
  loopRunning.value = false
}

function canvasToBlob(canvas) {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, 'image/jpeg', 0.55)
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
    const scale = Math.min(1, 320 / video.videoWidth)
    canvas.width = Math.round(video.videoWidth * scale)
    canvas.height = Math.round(video.videoHeight * scale)
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)

    const blob = await canvasToBlob(canvas)
    if (!blob) throw new Error('摄像头帧生成失败')

    const formData = new FormData()
    formData.append('file', blob, `owner_camera_${Date.now()}.jpg`)

    const started = performance.now()
    const response = await fetch(`${API_BASE}/api/gesture/owner/camera-fast-frame`, {
      method: 'POST',
      headers: authHeaders(),
      body: formData,
    })

    const data = await response.json()
    const clientLatency = performance.now() - started

    if (!response.ok || data.status === 'error') {
      throw new Error(data?.detail || data?.message || '识别失败')
    }

    updateResult(data, clientLatency)
  } catch (error) {
    errorMessage.value = `识别失败：${error.message}`
  } finally {
    requesting.value = false
  }
}

function updateResult(data, clientLatency) {
  const payload = data.result || data
  const state = payload.vehicle_state || {}

  result.gesture = payload.gesture || ''
  result.gesture_name = payload.gesture_name || ''
  result.confidence = payload.confidence
  result.action = payload.action || ''
  result.description = payload.description || ''
  result.latency_ms = data.latency_ms ?? clientLatency
  result.landmarks = payload.landmarks || []

  Object.assign(vehicleState, state)
  drawLandmarks(result.landmarks)

  emit('recognized', {
    status: 'success',
    task_type: 'owner_gesture',
    input_type: data.input_type || 'camera_fast_frame',
    latency_ms: result.latency_ms,
    created_at: new Date().toISOString(),
    result: {
      ...payload,
      vehicle_state: { ...vehicleState },
    },
  })
}

function resizeOverlay() {
  const video = videoRef.value
  const canvas = overlayCanvasRef.value
  if (!video || !canvas) return

  const rect = video.getBoundingClientRect()
  canvas.width = Math.round(rect.width)
  canvas.height = Math.round(rect.height)
}

function clearOverlay() {
  const canvas = overlayCanvasRef.value
  if (!canvas) return
  canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height)
}

function drawLandmarks(landmarks) {
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

  const point = (index) => {
    const item = landmarks.find((landmark) => landmark.index === index)
    if (!item) return null
    return {
      x: item.x * canvas.width,
      y: item.y * canvas.height,
    }
  }

  context.lineWidth = 3
  context.strokeStyle = '#22d3ee'
  context.fillStyle = '#fb923c'

  connections.forEach(([from, to]) => {
    const start = point(from)
    const end = point(to)
    if (!start || !end) return

    context.beginPath()
    context.moveTo(start.x, start.y)
    context.lineTo(end.x, end.y)
    context.stroke()
  })

  landmarks.forEach((landmark) => {
    context.beginPath()
    context.arc(
      landmark.x * canvas.width,
      landmark.y * canvas.height,
      4,
      0,
      Math.PI * 2,
    )
    context.fill()
  })
}

onBeforeUnmount(() => {
  stopCamera()
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
</style>
