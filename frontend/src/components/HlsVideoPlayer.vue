<template>
  <div class="hls-video-wrapper" ref="wrapperRef">
    <video ref="videoRef" muted autoplay playsinline></video>
    <div v-if="error" class="hls-error-overlay">
      <span>{{ error }}</span>
      <button @click="retry">重试</button>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Hls from 'hls.js'

const props = defineProps({
  src: { type: String, required: true },
  autoplay: { type: Boolean, default: true },
})

const emit = defineEmits(['playing', 'error', 'destroyed'])

const videoRef = ref(null)
const error = ref('')
let hls = null

function initHls() {
  destroyHls()
  error.value = ''

  const video = videoRef.value
  if (!video || !props.src) return

  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari 原生 HLS
    video.src = props.src
    video.addEventListener('playing', onPlaying)
    video.addEventListener('error', onVideoError)
  } else if (Hls.isSupported()) {
    hls = new Hls({
      enableWorker: false,
      lowLatencyMode: false,
      backBufferLength: 90,
      debug: false,
      maxBufferLength: 30,
      maxMaxBufferLength: 60,
    })
    hls.loadSource(props.src)
    hls.attachMedia(video)
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      if (props.autoplay) {
        video.play().catch(() => {})
      }
      emit('playing')
    })
    hls.on(Hls.Events.ERROR, (_event, data) => {
      // 记录所有错误，非 fatal 错误可能导致黑屏但不触发 error 事件
      const msg = `HLS ${data.fatal ? '[FATAL]' : '[RECOVERABLE]'}: ${data.type} - ${data.details}`
      console.warn('[HlsVideoPlayer]', msg, data)
      if (data.fatal) {
        error.value = msg
        emit('error', msg)
      }
    })
    hls.on(Hls.Events.FRAG_BUFFERED, () => {
      console.log('[HlsVideoPlayer] fragment buffered, playing')
    })
    hls.on(Hls.Events.FRAG_LOADING, () => {
      console.log('[HlsVideoPlayer] loading fragment...')
    })
  } else {
    error.value = '浏览器不支持 HLS 播放'
    emit('error', error.value)
  }
}

function onPlaying() {
  emit('playing')
}

function onVideoError() {
  error.value = '原生 HLS 播放失败'
  emit('error', error.value)
}

function destroyHls() {
  const video = videoRef.value
  if (video) {
    video.removeEventListener('playing', onPlaying)
    video.removeEventListener('error', onVideoError)
  }
  if (hls) {
    hls.destroy()
    hls = null
  }
  emit('destroyed')
}

function retry() {
  initHls()
}

watch(() => props.src, () => {
  if (props.src) initHls()
})

onMounted(() => {
  if (props.src) initHls()
})

onBeforeUnmount(() => {
  destroyHls()
})
</script>

<style scoped>
.hls-video-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
  background: #020617;
}

.hls-video-wrapper video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.hls-error-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #ef4444;
  background: rgba(2, 6, 23, 0.9);
}

.hls-error-overlay button {
  padding: 6px 18px;
  border-radius: 6px;
  border: 1px solid #ef4444;
  background: transparent;
  color: #ef4444;
  cursor: pointer;
}
</style>
