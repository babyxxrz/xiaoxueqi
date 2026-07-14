<!--
  HLS 视频播放器组件
  基于 hls.js 实现 HLS 流播放，支持：
  - 自动重连与错误恢复
  - 加载状态指示
  - 自适应码率
  用于沙盘多路监控视频的实时播放。
-->
<template>
  <div class="hls-video-wrapper">
    <video
      ref="videoRef"
      muted
      autoplay
      playsinline
      controls
    ></video>

    <div
      v-if="statusMessage"
      class="hls-status-overlay"
      :class="{ fatal: fatalError }"
    >
      <span>{{ statusMessage }}</span>
      <button v-if="fatalError" @click="manualRetry">
        重新连接
      </button>
    </div>
  </div>
</template>

<script setup>
import {
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'
import Hls from 'hls.js'

const props = defineProps({
  src: {
    type: String,
    required: true,
  },
  autoplay: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits([
  'playing',
  'error',
  'reconnecting',
])

const videoRef = ref(null)
const statusMessage = ref('')
const fatalError = ref(false)

let hls = null
let reconnectTimer = null
let stallTimer = null
let reconnectAttempt = 0
let lastProgressAt = Date.now()
let lastCurrentTime = -1

const MAX_RECONNECT_ATTEMPTS = 8

function clearTimers() {
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  if (stallTimer) {
    window.clearInterval(stallTimer)
    stallTimer = null
  }
}

function markPlaying() {
  reconnectAttempt = 0
  lastProgressAt = Date.now()
  statusMessage.value = ''
  fatalError.value = false
  emit('playing')
}

function markProgress() {
  const video = videoRef.value
  if (!video) return

  if (video.currentTime !== lastCurrentTime) {
    lastCurrentTime = video.currentTime
    lastProgressAt = Date.now()
  }
}

function scheduleReconnect(reason = '视频流暂时中断') {
  if (reconnectTimer) return

  reconnectAttempt += 1

  if (reconnectAttempt > MAX_RECONNECT_ATTEMPTS) {
    fatalError.value = true
    statusMessage.value = (
      '视频流多次重连失败，请检查 RTSP、MediaMTX 和网络'
    )
    emit('error', statusMessage.value)
    return
  }

  const delay = Math.min(
    8000,
    700 * (2 ** Math.min(reconnectAttempt - 1, 3)),
  )

  fatalError.value = false
  statusMessage.value = (
    `${reason}，正在自动重连 `
    + `(${reconnectAttempt}/${MAX_RECONNECT_ATTEMPTS})`
  )
  emit('reconnecting', {
    attempt: reconnectAttempt,
    delay,
    reason,
  })

  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    initPlayer()
  }, delay)
}

function startStallWatchdog() {
  if (stallTimer) {
    window.clearInterval(stallTimer)
  }

  stallTimer = window.setInterval(() => {
    const video = videoRef.value
    if (!video || video.paused || video.ended) return

    markProgress()

    if (
      Date.now() - lastProgressAt > 8000
      && video.readyState < 3
    ) {
      scheduleReconnect('视频画面长时间未更新')
    }
  }, 2000)
}

function handleNativeError() {
  scheduleReconnect('原生 HLS 播放失败')
}

function destroyPlayer() {
  clearTimers()

  const video = videoRef.value
  if (video) {
    video.removeEventListener('playing', markPlaying)
    video.removeEventListener('timeupdate', markProgress)
    video.removeEventListener('error', handleNativeError)
    video.removeAttribute('src')
    video.load()
  }

  if (hls) {
    hls.destroy()
    hls = null
  }
}

function initPlayer() {
  const video = videoRef.value
  if (!video || !props.src) return

  // 仅销毁播放器实例，不重置重连次数。
  if (hls) {
    hls.destroy()
    hls = null
  }

  video.removeEventListener('playing', markPlaying)
  video.removeEventListener('timeupdate', markProgress)
  video.removeEventListener('error', handleNativeError)

  video.addEventListener('playing', markPlaying)
  video.addEventListener('timeupdate', markProgress)
  video.addEventListener('error', handleNativeError)

  lastProgressAt = Date.now()
  lastCurrentTime = -1

  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = props.src
    if (props.autoplay) {
      video.play().catch(() => {})
    }
    startStallWatchdog()
    return
  }

  if (!Hls.isSupported()) {
    fatalError.value = true
    statusMessage.value = '当前浏览器不支持 HLS'
    emit('error', statusMessage.value)
    return
  }

  hls = new Hls({
    enableWorker: true,
    lowLatencyMode: true,
    backBufferLength: 10,
    maxBufferLength: 12,
    maxMaxBufferLength: 20,
    liveSyncDurationCount: 2,
    liveMaxLatencyDurationCount: 6,
    maxLiveSyncPlaybackRate: 1.5,
    manifestLoadingMaxRetry: 4,
    levelLoadingMaxRetry: 4,
    fragLoadingMaxRetry: 4,
    debug: false,
  })

  hls.loadSource(props.src)
  hls.attachMedia(video)

  hls.on(Hls.Events.MANIFEST_PARSED, () => {
    if (props.autoplay) {
      video.play().catch(() => {})
    }
  })

  hls.on(Hls.Events.ERROR, (_event, data) => {
    if (!data.fatal) return

    if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
      scheduleReconnect('HLS 网络连接中断')
      return
    }

    if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
      try {
        statusMessage.value = '媒体解码异常，正在恢复'
        hls.recoverMediaError()
      } catch {
        scheduleReconnect('媒体解码恢复失败')
      }
      return
    }

    scheduleReconnect('HLS 播放器异常')
  })

  startStallWatchdog()
}

function manualRetry() {
  reconnectAttempt = 0
  fatalError.value = false
  statusMessage.value = '正在重新连接'
  initPlayer()
}

watch(
  () => props.src,
  () => {
    reconnectAttempt = 0
    fatalError.value = false
    statusMessage.value = ''
    initPlayer()
  },
)

onMounted(() => {
  initPlayer()
})

onBeforeUnmount(() => {
  destroyPlayer()
})
</script>

<style scoped>
.hls-video-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 280px;
  background: #020617;
}

.hls-video-wrapper video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: #020617;
}

.hls-status-overlay {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: calc(100% - 36px);
  padding: 10px 14px;
  border: 1px solid rgba(34, 211, 238, 0.35);
  border-radius: 10px;
  background: rgba(2, 6, 23, 0.86);
  color: #a5f3fc;
  font-size: 13px;
  text-align: center;
}

.hls-status-overlay.fatal {
  border-color: rgba(248, 113, 113, 0.45);
  color: #fecaca;
}

.hls-status-overlay button {
  padding: 6px 12px;
  border: 1px solid currentColor;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  white-space: nowrap;
}
</style>
