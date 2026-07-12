<template>
  <section class="camera-panel">
    <div class="camera-heading">
      <div>
        <p class="section-kicker">TRAFFIC GESTURE CAMERA</p>
        <h3>交警手势实时摄像头识别</h3>
        <p>使用 MediaPipe Pose 进行全身姿态检测，实时识别 8 类交警手势并标注人体骨架。</p>
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
            <span>交通指令</span>
            <strong>{{ result.traffic_command || '-' }}</strong>
          </div>
          <div>
            <span>分类器</span>
            <strong>{{ result.classifier_type || '-' }}</strong>
          </div>
          <div>
            <span>命中规则</span>
            <strong>{{ result.matched_rule || '-' }}</strong>
          </div>
          <div>
            <span>姿态状态</span>
            <strong :class="{ detected: hasPose }">
              {{ hasPose ? '已检测到人体' : '未检测到' }}
            </strong>
          </div>
        </div>
      </div>
    </div>

    <section class="mapping-panel">
      <div class="mapping-heading">
        <div>
          <span>交警手势映射表</span>
          <strong>8 类交警手势 · 基于 MediaPipe Pose 规则引擎</strong>
        </div>
        <small>基于身体关键点几何关系（手臂角度、位置、比例）进行规则评分分类。</small>
      </div>

      <div class="mapping-grid">
        <article
          v-for="item in gestureMappings"
          :key="item.gesture"
          :class="{ active: result.gesture === item.gesture }"
        >
          <strong>{{ item.gestureName }}</strong>
          <small>{{ item.command }}</small>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import { API_BASE, authHeaders } from '../api'

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
  traffic_command: '',
  confidence: null,
  latency_ms: null,
  landmarks: [],
  classifier_type: '',
  matched_rule: '',
})

// MediaPipe Pose POSE_CONNECTIONS: 33 landmarks, 31 connections
const POSE_CONNECTIONS = [
  [0, 1], [1, 2], [2, 3], [3, 7], [0, 4], [4, 5], [5, 6], [6, 8],
  [9, 10],
  [11, 12],
  [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [27, 29], [29, 31], [27, 31],
  [24, 26], [26, 28], [28, 30], [30, 32], [28, 32],
]

const gestureMappings = [
  { gesture: 'stop', gestureName: '停止信号', command: '车辆停止通行' },
  { gesture: 'straight', gestureName: '直行信号', command: '车辆允许直行' },
  { gesture: 'left_turn', gestureName: '左转弯信号', command: '车辆允许左转' },
  { gesture: 'right_turn', gestureName: '右转弯信号', command: '车辆允许右转' },
  { gesture: 'lane_change', gestureName: '变道信号', command: '车辆按指令变道' },
  { gesture: 'left_turn_wait', gestureName: '左转弯待转信号', command: '车辆进入待转区' },
  { gesture: 'slow_down', gestureName: '减速慢行信号', command: '车辆减速慢行' },
  { gesture: 'pull_over', gestureName: '靠边停车信号', command: '车辆靠边停车' },
]

const hasPose = computed(() =>
  Array.isArray(result.landmarks) && result.landmarks.length > 0,
)

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
    formData.append('file', blob, `traffic_camera_${Date.now()}.jpg`)

    const started = performance.now()
    const response = await fetch(`${API_BASE}/api/gesture/traffic/camera-fast-frame`, {
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

  result.gesture = payload.gesture || ''
  result.gesture_name = payload.gesture_name || ''
  result.traffic_command = payload.traffic_command || ''
  result.confidence = payload.confidence
  result.latency_ms = data.latency_ms ?? clientLatency
  result.landmarks = payload.landmarks || []
  result.classifier_type = payload.classifier_type || ''
  result.matched_rule = payload.matched_rule || ''

  drawSkeleton(result.landmarks)
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

function drawSkeleton(landmarks) {
  resizeOverlay()

  const canvas = overlayCanvasRef.value
  if (!canvas) return

  const context = canvas.getContext('2d')
  context.clearRect(0, 0, canvas.width, canvas.height)

  if (!Array.isArray(landmarks) || !landmarks.length) return

  const point = (index) => {
    const item = landmarks.find((landmark) => landmark.index === index)
    if (!item) return null
    return {
      x: item.x * canvas.width,
      y: item.y * canvas.height,
    }
  }

  // Draw connections (skeleton lines)
  context.lineWidth = 3
  context.strokeStyle = '#22d3ee'
  context.lineCap = 'round'

  POSE_CONNECTIONS.forEach(([from, to]) => {
    const start = point(from)
    const end = point(to)
    if (!start || !end) return

    context.beginPath()
    context.moveTo(start.x, start.y)
    context.lineTo(end.x, end.y)
    context.stroke()
  })

  // Draw landmarks (keypoints)
  context.fillStyle = '#fb923c'
  landmarks.forEach((landmark) => {
    const x = landmark.x * canvas.width
    const y = landmark.y * canvas.height
    const radius = landmark.visibility > 0.5 ? 5 : 3

    context.beginPath()
    context.arc(x, y, radius, 0, Math.PI * 2)
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
.metric-grid span {
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

.metric-grid div {
  padding: 11px;
  border: 1px solid #26354d;
  border-radius: 12px;
  background: #0d1b30;
}

.metric-grid strong {
  display: block;
  margin-top: 5px;
  color: #e2e8f0;
  word-break: break-word;
}

.metric-grid .detected {
  color: #6ee7b7;
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.mapping-grid article {
  display: grid;
  gap: 5px;
  min-height: 72px;
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

.mapping-grid article strong {
  color: #f8fafc;
  font-size: 14px;
}

.mapping-grid article small {
  color: #8fa9c6;
  line-height: 1.5;
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

  .mapping-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
