<template>
  <section class="alert-dashboard">
    <!-- 统计概览 -->
    <div class="alert-stats-row">
      <div class="alert-stat-card" v-for="item in statCards" :key="item.key"
           :class="'stat-' + item.key">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
        <span class="stat-sub" v-if="item.sub">{{ item.sub }}</span>
      </div>
    </div>

    <!-- 连接状态 -->
    <div class="alert-connection">
      <span :class="['sse-dot', sseConnected ? 'on' : 'off']"></span>
      <span>{{ sseConnected ? 'SSE 实时连接中' : 'SSE 已断开' }}</span>
      <button class="sse-reconnect-btn" @click="reconnectSSE" :disabled="sseConnected">
        重连
      </button>
    </div>

    <!-- 告警类型分布 -->
    <div class="alert-chart-row" v-if="typeDistribution.length > 0">
      <div class="alert-bar-item" v-for="item in typeDistribution" :key="item.name">
        <span class="bar-label">{{ item.name }}</span>
        <div class="bar-track">
          <div class="bar-fill" :style="{ width: item.percent + '%', background: item.color }"></div>
        </div>
        <span class="bar-count">{{ item.count }}</span>
      </div>
    </div>

    <!-- 实时告警列表 -->
    <div class="alert-realtime-list">
      <h4>
        实时告警流
        <span class="alert-count-badge" v-if="realtimeAlerts.length">
          {{ realtimeAlerts.length }}
        </span>
      </h4>
      <div class="alert-items" v-if="realtimeAlerts.length > 0">
        <div
          v-for="alert in realtimeAlerts.slice(0, 20)"
          :key="alert.alert_id || alert.db_id"
          :class="['alert-item', 'level-' + normalizeLevel(alert.level)]"
        >
          <span :class="['level-tag', 'level-' + normalizeLevel(alert.level)]">
            {{ alert.level || 'info' }}
          </span>
          <div class="alert-body">
            <strong>{{ alert.summary || alert.event_type }}</strong>
            <span class="alert-time">{{ shortTime(alert.created_at) }}</span>
            <p v-if="alert.suggestion">{{ alert.suggestion }}</p>
          </div>
        </div>
      </div>
      <p class="empty-hint" v-else>暂无实时告警</p>
    </div>

    <!-- 统计数据 -->
    <div class="alert-metrics-grid">
      <div class="alert-metric">
        <span>告警频率</span>
        <strong>{{ stats.avg_per_minute ?? '-' }} /分钟</strong>
      </div>
      <div class="alert-metric">
        <span>未处理</span>
        <strong :style="{ color: stats.open > 0 ? '#f97316' : '' }">{{ stats.open ?? 0 }}</strong>
      </div>
      <div class="alert-metric">
        <span>已处理</span>
        <strong>{{ stats.resolved ?? 0 }}</strong>
      </div>
      <div class="alert-metric">
        <span>历史总计</span>
        <strong>{{ stats.total ?? 0 }}</strong>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

const API_BASE = 'http://127.0.0.1:8000'

const sseConnected = ref(false)
const realtimeAlerts = ref([])
const stats = reactive({
  total: 0,
  critical: 0,
  high: 0,
  medium: 0,
  low: 0,
  info: 0,
  open: 0,
  resolved: 0,
  by_type: {},
  avg_per_minute: null,
})

let eventSource = null

// --- SSE ---
function connectSSE() {
  if (eventSource) {
    eventSource.close()
  }
  const url = `${API_BASE}/api/alerts/stream`
  eventSource = new EventSource(url)

  eventSource.onopen = () => {
    sseConnected.value = true
  }

  eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data)
      if (payload.type === 'alert' && payload.data) {
        realtimeAlerts.value.unshift(payload.data)
        if (realtimeAlerts.value.length > 50) {
          realtimeAlerts.value = realtimeAlerts.value.slice(0, 50)
        }
      }
    } catch {
      // 忽略解析错误
    }
  }

  eventSource.onerror = () => {
    sseConnected.value = false
  }
}

function reconnectSSE() {
  connectSSE()
}

// --- 统计数据 ---
async function fetchStats() {
  try {
    const resp = await fetch(`${API_BASE}/api/alerts/agent/stats`)
    const data = await resp.json()
    if (data.status === 'success' && data.stats) {
      Object.assign(stats, data.stats)
    }
  } catch {
    // 静默
  }
}

const statCards = computed(() => [
  { key: 'critical', label: '严重', value: stats.critical, sub: '' },
  { key: 'high', label: '高风险', value: stats.high, sub: '' },
  { key: 'medium', label: '中风险', value: stats.medium, sub: '' },
  { key: 'low', label: '低风险', value: stats.low, sub: '' },
  { key: 'info', label: '信息', value: stats.info, sub: '' },
])

const typeDistribution = computed(() => {
  const byType = stats.by_type || {}
  const entries = Object.entries(byType).sort((a, b) => b[1] - a[1])
  const total = entries.reduce((sum, [, c]) => sum + c, 0) || 1
  const colors = ['#ef4444', '#f97316', '#eab308', '#3b82f6', '#22c55e', '#8b5cf6', '#06b6d4']
  return entries.map(([name, count], idx) => ({
    name,
    count,
    percent: Math.round((count / total) * 100),
    color: colors[idx % colors.length],
  }))
})

function normalizeLevel(level) {
  const map = { critical: 'critical', high: 'high', medium: 'medium', low: 'low', info: 'info',
    warning: 'medium', error: 'high' }
  return map[level] || 'info'
}

function shortTime(ts) {
  if (!ts) return ''
  return ts.slice(-8) || ts
}

onMounted(() => {
  connectSSE()
  fetchStats()
})

onBeforeUnmount(() => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
})
</script>

<style scoped>
.alert-dashboard {
  padding: 14px 0;
}

/* 统计卡片 */
.alert-stats-row {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.alert-stat-card {
  border-radius: 14px;
  padding: 14px;
  text-align: center;
  border: 1px solid rgba(148, 163, 184, 0.28);
  background: rgba(255, 255, 255, 0.82);
}

.alert-stat-card .stat-label {
  display: block;
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}

.alert-stat-card .stat-value {
  display: block;
  font-size: 30px;
  font-weight: 800;
}

.alert-stat-card .stat-sub {
  font-size: 11px;
  color: #94a3b8;
}

.stat-critical .stat-value { color: #dc2626; }
.stat-high .stat-value { color: #ea580c; }
.stat-medium .stat-value { color: #ca8a04; }
.stat-low .stat-value { color: #2563eb; }
.stat-info .stat-value { color: #6b7280; }

/* SSE 连接 */
.alert-connection {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #64748b;
  margin-bottom: 12px;
}

.sse-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #94a3b8;
}

.sse-dot.on {
  background: #22c55e;
  box-shadow: 0 0 6px #22c55e;
}

.sse-dot.off {
  background: #ef4444;
}

.sse-reconnect-btn {
  border: none;
  border-radius: 8px;
  padding: 4px 10px;
  background: #334155;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}

/* 类型分布条 */
.alert-chart-row {
  margin-bottom: 14px;
}

.alert-bar-item {
  display: grid;
  grid-template-columns: 120px 1fr 40px;
  gap: 10px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 13px;
}

.bar-label {
  text-align: right;
  color: #475569;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-track {
  height: 10px;
  border-radius: 5px;
  background: #e2e8f0;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 5px;
  transition: width .3s;
}

.bar-count {
  color: #0f172a;
  font-weight: 700;
}

/* 实时告警列表 */
.alert-realtime-list h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  color: #0f172a;
}

.alert-count-badge {
  background: #ef4444;
  color: #fff;
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 12px;
}

.alert-items {
  max-height: 360px;
  overflow: auto;
}

.alert-item {
  display: flex;
  gap: 10px;
  padding: 10px;
  border-radius: 10px;
  margin-bottom: 6px;
  border-left: 4px solid #94a3b8;
  background: #f8fafc;
  animation: slideIn .25s ease;
}

@keyframes slideIn {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}

.level-tag {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.level-critical { border-left-color: #dc2626; }
.level-tag.level-critical { background: #fef2f2; color: #dc2626; }

.level-high { border-left-color: #ea580c; }
.level-tag.level-high { background: #fff7ed; color: #ea580c; }

.level-medium { border-left-color: #ca8a04; }
.level-tag.level-medium { background: #fefce8; color: #ca8a04; }

.level-low { border-left-color: #2563eb; }
.level-tag.level-low { background: #eff6ff; color: #2563eb; }

.level-info { border-left-color: #6b7280; }
.level-tag.level-info { background: #f9fafb; color: #6b7280; }

.alert-body strong {
  display: block;
  font-size: 13px;
  color: #0f172a;
  margin-bottom: 2px;
}

.alert-time {
  font-size: 11px;
  color: #94a3b8;
}

.alert-body p {
  margin: 4px 0 0;
  font-size: 12px;
  color: #64748b;
}

.empty-hint {
  color: #94a3b8;
  font-size: 13px;
}

/* 统计指标 */
.alert-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.alert-metric {
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 12px;
  padding: 12px;
  background: #f8fafc;
  text-align: center;
}

.alert-metric span {
  display: block;
  color: #64748b;
  font-size: 12px;
  margin-bottom: 4px;
}

.alert-metric strong {
  display: block;
  font-size: 20px;
  font-weight: 800;
  color: #0f172a;
}
</style>
