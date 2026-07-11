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
        <article class="panel span-12 model-test-header">
          <header class="panel-title">
            <div>
              <p class="eyebrow">MODEL TEST CENTER</p>
              <h2>模型测试中心</h2>
              <p>
                车牌识别与交警手势识别统一使用本地视频上传。系统会连续抽帧、完成时序分析，
                并展示最佳标注帧、识别结果和性能数据。
              </p>
            </div>
            <div class="input-mode-badge">
              <span>正式输入方式</span>
              <strong>本地视频上传</strong>
            </div>
          </header>

          <div class="sub-tabs">
            <button
              v-for="item in recognitionTabs"
              :key="item.key"
              :class="{ active: recognitionTab === item.key }"
              @click="recognitionTab = item.key; recognitionError = ''"
            >
              {{ item.label }}
            </button>
          </div>
        </article>

        <template v-if="recognitionTab === 'plate'">
          <article class="panel span-5">
            <header class="panel-title">
              <div>
                <p class="eyebrow">PLATE VIDEO</p>
                <h2>车牌视频上传</h2>
                <p>支持 MP4、AVI、MOV、MKV、WebM，单次最多读取 300 帧。</p>
              </div>
            </header>

            <div
              class="video-drop-zone"
              @dragover.prevent
              @drop.prevent="dropRecognitionFile($event, 'plate')"
            >
              <div class="drop-zone-icon">▣</div>
              <strong>拖入车牌视频，或从本地选择</strong>
              <span>浏览器将视频上传到后端，不需要填写本地文件路径。</span>
              <label class="file-select-button">
                选择车牌视频
                <input
                  class="hidden-file-input"
                  type="file"
                  accept="video/mp4,video/avi,video/x-msvideo,video/quicktime,video/x-matroska,video/webm,.m4v"
                  @change="pickRecognitionFile($event, 'plate')"
                />
              </label>
            </div>

            <div v-if="plateUpload.previewUrl" class="local-video-preview">
              <video
                :src="plateUpload.previewUrl"
                controls
                preload="metadata"
                @loadedmetadata="captureVideoMetadata($event, 'plate')"
              ></video>
            </div>

            <div v-if="plateFile" class="selected-file-card">
              <div>
                <span>已选择视频</span>
                <strong>{{ plateFile.name }}</strong>
                <small>
                  {{ formatFileSize(plateFile.size) }}
                  <template v-if="plateUpload.duration">
                    · {{ formatDuration(plateUpload.duration) }}
                  </template>
                </small>
              </div>
              <button class="text-button" :disabled="loading.plate" @click="clearRecognitionFile('plate')">
                移除
              </button>
            </div>

            <div class="recognition-parameter-grid">
              <label>
                <span>最大读取帧数</span>
                <input
                  v-model.number="plateUpload.frameCount"
                  type="number"
                  min="20"
                  max="300"
                  step="10"
                />
                <small>建议 120～240 帧</small>
              </label>
              <label>
                <span>抽帧间隔</span>
                <input
                  v-model.number="plateUpload.sampleInterval"
                  type="number"
                  min="1"
                  max="20"
                  step="1"
                />
                <small>建议每 5 帧识别一次</small>
              </label>
            </div>

            <button
              class="primary-action"
              :disabled="loading.plate || !plateFile"
              @click="recognizePlate"
            >
              {{ loading.plate ? '正在上传并识别...' : '开始车牌视频识别' }}
            </button>

            <div :class="['recognition-status', `status-${plateUpload.status}`]">
              <span>{{ recognitionStatusLabel(plateUpload.status) }}</span>
              <small>
                {{
                  plateUpload.status === 'processing'
                    ? '视频正在上传并进行连续帧识别，请保持页面开启。'
                    : plateUpload.status === 'success'
                      ? '识别完成，右侧已生成聚合结果。'
                      : '选择本地视频后即可开始。'
                }}
              </small>
            </div>

            <p v-if="recognitionError" class="page-error">{{ recognitionError }}</p>
          </article>

          <article class="panel span-7">
            <header class="panel-title">
              <div>
                <p class="eyebrow">PLATE RESULT</p>
                <h2>车牌视频识别结果</h2>
              </div>
              <span v-if="plateResult" class="result-success-badge">识别完成</span>
            </header>

            <div v-if="plateResult" class="video-result-stack">
              <div class="result-summary-grid">
                <div>
                  <span>稳定车牌</span>
                  <strong>{{ plateStats.plateCount }}</strong>
                  <small>过滤低置信度并合并相似结果</small>
                </div>
                <div>
                  <span>读取帧数</span>
                  <strong>{{ plateStats.framesRead }}</strong>
                  <small>原始视频帧</small>
                </div>
                <div>
                  <span>识别帧数</span>
                  <strong>{{ plateStats.sampledFrames }}</strong>
                  <small>实际推理帧</small>
                </div>
                <div>
                  <span>处理耗时</span>
                  <strong>{{ formatMilliseconds(plateStats.processingLatencyMs) }}</strong>
                  <small>上传后端处理总耗时</small>
                </div>
              </div>

              <div class="result-media-card">
                <div>
                  <span>最佳车牌标注帧</span>
                  <small>
                    {{ plateVideoMetadata.width || '-' }} × {{ plateVideoMetadata.height || '-' }}
                    · {{ formatDuration(plateVideoMetadata.duration_seconds) }}
                  </small>
                </div>
                <img
                  v-if="plateResult.output_image_url"
                  :src="assetUrl(plateResult.output_image_url)"
                  alt="车牌视频最佳标注帧"
                />
                <div v-else class="empty-state">本次未生成最佳标注帧。</div>
              </div>

              <div class="data-table result-table">
                <table>
                  <thead>
                    <tr>
                      <th>车牌号码</th>
                      <th>颜色</th>
                      <th>置信度</th>
                      <th>出现次数</th>
                      <th>最佳帧</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(plate, index) in plateList" :key="`${plate.plate_number || index}_${index}`">
                      <td><strong>{{ plate.plate_number || plate.plate || plate.text || '未解析号码' }}</strong></td>
                      <td>{{ plate.plate_color || plate.color || '未知' }}</td>
                      <td>{{ percent(plate.confidence) }}</td>
                      <td>{{ plate.appear_count ?? plate.count ?? 1 }}</td>
                      <td>{{ plate.best_frame_index ?? '-' }}</td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="!plateList.length" class="empty-state">
                  当前视频未检测到车牌。可尝试增加读取帧数、减小抽帧间隔，或更换更清晰的视频。
                </div>
              </div>

              <div class="plate-filter-summary">
                <span>
                  聚合规则：相似车牌合并为一组，组内只显示最高置信度结果；
                  低于 {{ percent(plateStats.confidenceThreshold) }} 的候选不写入记录。
                </span>
                <span v-if="plateStats.discardedCandidates > 0">
                  本次已过滤 {{ plateStats.discardedCandidates }} 条低置信度或不稳定候选。
                </span>
              </div>
            </div>

            <div v-else class="empty-state result-empty">
              上传包含清晰车辆和车牌的本地视频后，这里将显示最佳标注帧、多车牌聚合结果和处理耗时。
            </div>
          </article>
        </template>

        <template v-if="recognitionTab === 'traffic'">
          <article class="panel span-5">
            <header class="panel-title">
              <div>
                <p class="eyebrow">TRAFFIC GESTURE VIDEO</p>
                <h2>交警手势视频上传</h2>
                <p>使用 MediaPipe Pose 与 V6 八类时序规则完成连续动作识别。</p>
              </div>
            </header>

            <div
              class="video-drop-zone"
              @dragover.prevent
              @drop.prevent="dropRecognitionFile($event, 'traffic')"
            >
              <div class="drop-zone-icon">◎</div>
              <strong>拖入交警手势视频，或从本地选择</strong>
              <span>建议人物正面、全身清晰，肩、肘、腕完整可见。</span>
              <label class="file-select-button">
                选择交警手势视频
                <input
                  class="hidden-file-input"
                  type="file"
                  accept="video/mp4,video/avi,video/x-msvideo,video/quicktime,video/x-matroska,video/webm,.m4v"
                  @change="pickRecognitionFile($event, 'traffic')"
                />
              </label>
            </div>

            <div v-if="trafficUpload.previewUrl" class="local-video-preview">
              <video
                :src="trafficUpload.previewUrl"
                controls
                preload="metadata"
                @loadedmetadata="captureVideoMetadata($event, 'traffic')"
              ></video>
            </div>

            <div v-if="trafficFile" class="selected-file-card">
              <div>
                <span>已选择视频</span>
                <strong>{{ trafficFile.name }}</strong>
                <small>
                  {{ formatFileSize(trafficFile.size) }}
                  <template v-if="trafficUpload.duration">
                    · {{ formatDuration(trafficUpload.duration) }}
                  </template>
                </small>
              </div>
              <button class="text-button" :disabled="loading.traffic" @click="clearRecognitionFile('traffic')">
                移除
              </button>
            </div>

            <div class="recognition-parameter-grid">
              <label>
                <span>最大读取帧数</span>
                <input
                  v-model.number="trafficUpload.frameCount"
                  type="number"
                  min="20"
                  max="300"
                  step="10"
                />
                <small>建议覆盖完整动作</small>
              </label>
              <label>
                <span>抽帧间隔</span>
                <input
                  v-model.number="trafficUpload.sampleInterval"
                  type="number"
                  min="1"
                  max="20"
                  step="1"
                />
                <small>建议每 2 帧识别一次</small>
              </label>
            </div>

            <button
              class="primary-action"
              :disabled="loading.traffic || !trafficFile"
              @click="recognizeTraffic"
            >
              {{ loading.traffic ? '正在上传并识别...' : '开始交警手势视频识别' }}
            </button>

            <div :class="['recognition-status', `status-${trafficUpload.status}`]">
              <span>{{ recognitionStatusLabel(trafficUpload.status) }}</span>
              <small>
                {{
                  trafficUpload.status === 'processing'
                    ? '正在提取人体关键点并进行连续帧动作签名分析。'
                    : trafficUpload.status === 'success'
                      ? '识别完成，右侧已生成手势与交通指令。'
                      : '选择本地视频后即可开始。'
                }}
              </small>
            </div>

            <p v-if="recognitionError" class="page-error">{{ recognitionError }}</p>
          </article>

          <article class="panel span-7">
            <header class="panel-title">
              <div>
                <p class="eyebrow">GESTURE RESULT</p>
                <h2>交警手势识别结果</h2>
              </div>
              <span v-if="trafficResult" class="result-success-badge">
                {{ trafficPayload.rule_version || '时序规则' }}
              </span>
            </header>

            <div v-if="trafficResult" class="video-result-stack">
              <div class="traffic-primary-result">
                <div>
                  <span>识别手势</span>
                  <strong>{{ trafficPayload.gesture_name || trafficPayload.gesture || '-' }}</strong>
                  <p>{{ trafficPayload.traffic_command || trafficPayload.command || '暂无交通指令' }}</p>
                </div>
                <div class="confidence-orbit">
                  <strong>{{ percent(trafficPayload.confidence) }}</strong>
                  <span>规则置信度</span>
                </div>
              </div>

              <div class="result-summary-grid traffic-summary-grid">
                <div>
                  <span>有效姿态帧</span>
                  <strong>{{ trafficStats.validFrames }}</strong>
                  <small>检测到人体关键点</small>
                </div>
                <div>
                  <span>稳定支持帧</span>
                  <strong>{{ trafficStats.stableFrames }}</strong>
                  <small>支持最终类别</small>
                </div>
                <div>
                  <span>支持比例</span>
                  <strong>{{ percent(trafficStats.voteRatio) }}</strong>
                  <small>稳定帧 / 有效帧</small>
                </div>
                <div>
                  <span>处理耗时</span>
                  <strong>{{ formatMilliseconds(trafficStats.processingLatencyMs) }}</strong>
                  <small>连续帧处理总耗时</small>
                </div>
              </div>

              <div class="result-media-card">
                <div>
                  <span>最佳骨架标注帧</span>
                  <small>
                    最佳帧 {{ trafficPayload.best_frame_index ?? '-' }}
                    · {{ trafficVideoMetadata.width || '-' }} × {{ trafficVideoMetadata.height || '-' }}
                  </small>
                </div>
                <img
                  v-if="trafficResult.output_image_url"
                  :src="assetUrl(trafficResult.output_image_url)"
                  alt="交警手势最佳骨架标注帧"
                />
                <div v-else class="empty-state">本次未生成最佳标注帧。</div>
              </div>

              <div class="rule-detail-grid">
                <div>
                  <span>内部类别</span>
                  <strong>{{ trafficPayload.gesture || '-' }}</strong>
                </div>
                <div>
                  <span>命中规则</span>
                  <strong>{{ trafficPayload.matched_rule || '-' }}</strong>
                </div>
                <div>
                  <span>规则版本</span>
                  <strong>{{ trafficPayload.rule_version || '-' }}</strong>
                </div>
                <div>
                  <span>识别策略</span>
                  <strong>{{ trafficPayload.classifier_type || 'rule_based_temporal' }}</strong>
                </div>
              </div>

              <div class="signature-panel">
                <div>
                  <span>V6 动作签名</span>
                  <small>高亮项表示本次视频满足对应的互斥动作结构。</small>
                </div>
                <div class="signature-grid">
                  <span
                    v-for="item in trafficSignatureItems"
                    :key="item.key"
                    :class="{ matched: item.matched }"
                  >
                    {{ item.label }}
                  </span>
                </div>
              </div>
            </div>

            <div v-else class="empty-state result-empty">
              上传完整交警动作视频后，这里将显示手势类别、交通指令、稳定支持帧、规则版本和最佳骨架帧。
            </div>
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
              <p>
                每条告警都会自动发送到告警所属用户邮箱，并同步通知所有有效管理员邮箱。
              </p>
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
            <div>
              <span>邮件已发送</span>
              <strong>{{ sentAlertEmailCount }}</strong>
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
                  <th>邮件通知</th>
                  <th>操作</th>
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
                  <td>
                    <span :class="['email-status-tag', emailStatusClass(item)]">
                      {{ alertEmailStatus(item) }}
                    </span>
                  </td>
                  <td>
                    <button
                      v-if="canRetryAlertEmail(item)"
                      class="table-detail-button"
                      :disabled="retryingAlertId === (item.id || item.alert_id)"
                      @click="retryAlertEmail(item)"
                    >
                      {{ retryingAlertId === (item.id || item.alert_id) ? '重试中...' : '重试邮件' }}
                    </button>
                    <span v-else class="table-muted">—</span>
                  </td>
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
              <h2>我的识别记录</h2>
              <p>仅展示当前登录账号产生的数据。点击“查看结果”可查看识别内容、标注图和完整结果。</p>
            </div>
          </header>

          <div class="data-table">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>任务</th>
                  <th>识别结果</th>
                  <th>输入方式</th>
                  <th>文件</th>
                  <th>时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in records" :key="item.id || item.record_id">
                  <td>{{ item.id || item.record_id }}</td>
                  <td>{{ taskLabel(item.task_type) }}</td>
                  <td class="record-summary-cell">{{ recordResultSummary(item) }}</td>
                  <td>{{ item.input_type || '-' }}</td>
                  <td>{{ item.original_filename || item.saved_filename || '-' }}</td>
                  <td>{{ item.created_at || item.created_time || '-' }}</td>
                  <td>
                    <button class="table-detail-button" @click="openRecordDetail(item)">
                      查看结果
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="!records.length" class="empty-state">当前账号暂无识别记录。</div>
          </div>
        </article>
      </section>
    </main>

    <div
      v-if="recordDetailOpen"
      class="record-modal-backdrop"
      @click.self="closeRecordDetail"
    >
      <article class="record-modal">
        <header class="record-modal-header">
          <div>
            <p class="eyebrow">RECOGNITION DETAIL</p>
            <h2>{{ taskLabel(selectedRecord?.task_type) }}结果</h2>
            <span>
              记录 #{{ selectedRecord?.id || selectedRecord?.record_id || '-' }}
              · {{ selectedRecord?.created_at || '-' }}
            </span>
          </div>
          <button class="record-modal-close" @click="closeRecordDetail">关闭</button>
        </header>

        <div v-if="recordDetailLoading" class="empty-state">正在加载完整识别结果...</div>

        <template v-else-if="selectedRecord">
          <section class="record-detail-grid">
            <div>
              <span>识别摘要</span>
              <strong>{{ recordResultSummary(selectedRecord) }}</strong>
            </div>
            <div>
              <span>输入方式</span>
              <strong>{{ selectedRecord.input_type || '-' }}</strong>
            </div>
            <div>
              <span>文件</span>
              <strong>{{ selectedRecord.original_filename || selectedRecord.saved_filename || '-' }}</strong>
            </div>
            <div>
              <span>处理耗时</span>
              <strong>{{ recordLatencyText(selectedRecord) }}</strong>
            </div>
          </section>

          <section
            v-if="selectedRecord.image_url || selectedRecord.output_image_url"
            class="record-media-grid"
          >
            <figure v-if="selectedRecord.image_url">
              <figcaption>输入图像 / 最佳视频帧</figcaption>
              <img :src="assetUrl(selectedRecord.image_url)" alt="识别输入" />
            </figure>
            <figure v-if="selectedRecord.output_image_url">
              <figcaption>识别标注结果</figcaption>
              <img :src="assetUrl(selectedRecord.output_image_url)" alt="识别标注结果" />
            </figure>
          </section>

          <section v-if="recordPlateItems(selectedRecord).length" class="record-result-panel">
            <h3>车牌结果</h3>
            <div class="record-plate-list">
              <div
                v-for="(plate, index) in recordPlateItems(selectedRecord)"
                :key="`${plate.plate_number || plate.plate || index}-${index}`"
              >
                <strong>{{ plate.plate_number || plate.plate || plate.text || '-' }}</strong>
                <span>{{ plate.plate_color || plate.color || '未知颜色' }}</span>
                <span>置信度 {{ percent(plate.confidence) }}</span>
                <span v-if="plate.appear_count">出现 {{ plate.appear_count }} 次</span>
              </div>
            </div>
          </section>

          <section
            v-if="recordGestureInfo(selectedRecord)"
            class="record-result-panel"
          >
            <h3>手势与控制结果</h3>
            <dl class="record-gesture-detail">
              <div>
                <dt>手势</dt>
                <dd>{{ recordGestureInfo(selectedRecord).gesture }}</dd>
              </div>
              <div>
                <dt>控制 / 指令</dt>
                <dd>{{ recordGestureInfo(selectedRecord).command }}</dd>
              </div>
              <div>
                <dt>置信度</dt>
                <dd>{{ percent(recordGestureInfo(selectedRecord).confidence) }}</dd>
              </div>
            </dl>
          </section>

          <details class="record-json-panel">
            <summary>查看完整识别结果 JSON</summary>
            <pre>{{ prettyJson(selectedRecord.result) }}</pre>
          </details>
        </template>
      </article>
    </div>
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
  { key: 'recognition', label: '模型测试中心', icon: '◈' },
  { key: 'sources', label: '视频源管理', icon: '▣' },
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
const selectedRecord = ref(null)
const recordDetailOpen = ref(false)
const recordDetailLoading = ref(false)
const alerts = ref([])
const retryingAlertId = ref(null)
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

const plateUpload = reactive({
  previewUrl: '',
  duration: 0,
  frameCount: 240,
  sampleInterval: 5,
  status: 'idle',
})

const trafficUpload = reactive({
  previewUrl: '',
  duration: 0,
  frameCount: 300,
  sampleInterval: 2,
  status: 'idle',
})

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

const plateVideoMetadata = computed(() =>
  plateResult.value?.video_metadata ||
  platePayload.value?.video_metadata ||
  {},
)

const trafficVideoMetadata = computed(() =>
  trafficResult.value?.video_metadata ||
  trafficPayload.value?.video_metadata ||
  {},
)

const plateStats = computed(() => ({
  plateCount:
    platePayload.value?.stable_plate_count ??
    platePayload.value?.plate_count ??
    plateList.value.length,
  framesRead:
    plateResult.value?.frames_read ??
    platePayload.value?.frames_read ??
    '-',
  sampledFrames:
    plateResult.value?.sampled_frames ??
    platePayload.value?.sampled_frames ??
    '-',
  processingLatencyMs:
    plateResult.value?.processing_latency_ms ??
    platePayload.value?.processing_latency_ms ??
    platePayload.value?.latency_ms ??
    null,
  confidenceThreshold:
    platePayload.value?.confidence_threshold ?? 0.8,
  discardedCandidates:
    Number(platePayload.value?.discarded_low_confidence_count || 0) +
    Number(platePayload.value?.discarded_unstable_count || 0),
}))

const trafficStats = computed(() => ({
  validFrames: trafficPayload.value?.valid_frames ?? '-',
  stableFrames: trafficPayload.value?.stable_frames ?? '-',
  voteRatio: trafficPayload.value?.vote_ratio ?? 0,
  processingLatencyMs:
    trafficResult.value?.processing_latency_ms ??
    trafficPayload.value?.processing_latency_ms ??
    trafficPayload.value?.latency_ms ??
    null,
}))

const trafficSignatureItems = computed(() => {
  const labels = {
    stop: '停止',
    straight: '直行',
    lane_change: '变道',
    left_turn: '左转',
    right_turn: '右转',
    left_turn_wait: '左转待转',
    slow_down: '减速慢行',
    pull_over: '靠边停车',
  }
  const matches =
    trafficPayload.value?.temporal_evidence?.decision?.signature_matches || {}

  return Object.entries(labels).map(([key, label]) => ({
    key,
    label,
    matched: Boolean(matches[key]),
  }))
})

const criticalAlertCount = computed(() =>
  alerts.value.filter((item) => alertLevel(item) === 'high').length,
)

const unresolvedAlertCount = computed(() =>
  alerts.value.filter((item) => !item.resolved && item.status !== 'resolved').length,
)

const sentAlertEmailCount = computed(
  () =>
    alerts.value.filter(
      (item) => alertEmailStatus(item) === '已发送',
    ).length,
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


function recordResultSummary(item) {
  if (!item) return '-'
  if (item.result_summary) return item.result_summary

  const result = item.result || {}
  if (item.task_type === 'plate') {
    const plates = Array.isArray(result.plates) ? result.plates : []
    const numbers = plates
      .map((plate) => plate.plate_number || plate.plate || plate.text)
      .filter(Boolean)
    return numbers.length ? numbers.slice(0, 3).join('、') : '未识别到有效车牌'
  }

  if (item.task_type === 'traffic_gesture') {
    const gesture = result.gesture_name || result.gesture || '未知手势'
    const command = result.traffic_command || result.command || ''
    return command ? `${gesture} · ${command}` : gesture
  }

  if (item.task_type === 'owner_gesture') {
    const gesture = result.gesture_name || result.gesture || '未知手势'
    const action = result.description || result.action || ''
    return action ? `${gesture} · ${action}` : gesture
  }

  return result.summary || result.suggestion || result.message || '查看完整结果'
}

function recordPlateItems(item) {
  return Array.isArray(item?.result?.plates) ? item.result.plates : []
}

function recordGestureInfo(item) {
  const result = item?.result || {}
  if (!['traffic_gesture', 'owner_gesture'].includes(item?.task_type)) return null

  return {
    gesture: result.gesture_name || result.gesture || '未知手势',
    command:
      result.traffic_command ||
      result.command ||
      result.description ||
      result.action ||
      '-',
    confidence: result.confidence,
  }
}

function recordLatencyText(item) {
  const result = item?.result || {}
  return formatMilliseconds(
    result.processing_latency_ms ??
    result.latency_ms ??
    result.total_latency_ms,
  )
}

function prettyJson(value) {
  try {
    return JSON.stringify(value || {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

async function openRecordDetail(item) {
  selectedRecord.value = item
  recordDetailOpen.value = true
  recordDetailLoading.value = true

  const recordId = item?.id || item?.record_id
  if (!recordId) {
    recordDetailLoading.value = false
    return
  }

  try {
    const data = await apiGet(`/api/records/${recordId}`)
    selectedRecord.value = data?.record || item
  } catch (error) {
    globalError.value = `识别结果加载失败：${error.message}`
  } finally {
    recordDetailLoading.value = false
  }
}

function closeRecordDetail() {
  recordDetailOpen.value = false
  selectedRecord.value = null
  recordDetailLoading.value = false
}

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

function formatFileSize(bytes) {
  const number = Number(bytes)
  if (!Number.isFinite(number) || number < 0) return '-'
  if (number < 1024) return `${number} B`
  if (number < 1024 ** 2) return `${(number / 1024).toFixed(1)} KB`
  return `${(number / 1024 ** 2).toFixed(1)} MB`
}

function formatDuration(seconds) {
  const number = Number(seconds)
  if (!Number.isFinite(number) || number <= 0) return '-'
  const minutes = Math.floor(number / 60)
  const remaining = Math.round(number % 60)
  return minutes ? `${minutes} 分 ${remaining} 秒` : `${remaining} 秒`
}

function formatMilliseconds(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  if (number >= 1000) return `${(number / 1000).toFixed(2)} s`
  return `${Math.round(number)} ms`
}

function recognitionStatusLabel(status) {
  const labels = {
    idle: '等待选择视频',
    ready: '视频已就绪',
    processing: '正在处理',
    success: '识别完成',
    error: '识别失败',
  }
  return labels[status] || labels.idle
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

  if (summary.status === 'fulfilled') dashboard.value = summary.value?.summary || summary.value
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

function isVideoFile(file) {
  if (!file) return false
  if (file.type?.startsWith('video/')) return true
  return /\.(mp4|avi|mov|mkv|webm|m4v)$/i.test(file.name || '')
}

function revokePreviewUrl(uploadState) {
  if (uploadState.previewUrl) {
    URL.revokeObjectURL(uploadState.previewUrl)
    uploadState.previewUrl = ''
  }
}

function applyRecognitionFile(file, type) {
  recognitionError.value = ''

  if (!file) return

  if (!isVideoFile(file)) {
    recognitionError.value = '请选择 MP4、AVI、MOV、MKV、WebM 或 M4V 视频文件'
    return
  }

  if (file.size > 500 * 1024 * 1024) {
    recognitionError.value = '单个视频不能超过 500 MB'
    return
  }

  const isPlate = type === 'plate'
  const targetFile = isPlate ? plateFile : trafficFile
  const targetResult = isPlate ? plateResult : trafficResult
  const targetUpload = isPlate ? plateUpload : trafficUpload

  revokePreviewUrl(targetUpload)
  targetFile.value = file
  targetResult.value = null
  targetUpload.duration = 0
  targetUpload.previewUrl = URL.createObjectURL(file)
  targetUpload.status = 'ready'
}

function pickRecognitionFile(event, type) {
  const file = event.target.files?.[0] || null
  applyRecognitionFile(file, type)
  event.target.value = ''
}

function dropRecognitionFile(event, type) {
  const file = event.dataTransfer?.files?.[0] || null
  applyRecognitionFile(file, type)
}

function captureVideoMetadata(event, type) {
  const duration = Number(event.target?.duration || 0)
  if (type === 'plate') plateUpload.duration = duration
  if (type === 'traffic') trafficUpload.duration = duration
}

function clearRecognitionFile(type) {
  const isPlate = type === 'plate'
  const targetFile = isPlate ? plateFile : trafficFile
  const targetResult = isPlate ? plateResult : trafficResult
  const targetUpload = isPlate ? plateUpload : trafficUpload

  revokePreviewUrl(targetUpload)
  targetFile.value = null
  targetResult.value = null
  targetUpload.duration = 0
  targetUpload.status = 'idle'
  recognitionError.value = ''
}

function normalizeRecognitionParameters(uploadState) {
  return {
    frameCount: Math.min(300, Math.max(20, Number(uploadState.frameCount || 120))),
    sampleInterval: Math.min(20, Math.max(1, Number(uploadState.sampleInterval || 1))),
  }
}

async function recognizePlate() {
  if (!plateFile.value) {
    recognitionError.value = '请先选择车牌视频'
    return
  }

  const { frameCount, sampleInterval } = normalizeRecognitionParameters(plateUpload)
  plateUpload.frameCount = frameCount
  plateUpload.sampleInterval = sampleInterval
  plateUpload.status = 'processing'
  loading.plate = true
  recognitionError.value = ''

  try {
    const path =
      `/api/plate/video?frame_count=${encodeURIComponent(frameCount)}` +
      `&sample_interval=${encodeURIComponent(sampleInterval)}`
    plateResult.value = await uploadFile(path, plateFile.value)
    plateUpload.status = 'success'
    await refreshAll()
  } catch (error) {
    plateUpload.status = 'error'
    recognitionError.value = error.message
  } finally {
    loading.plate = false
  }
}

async function recognizeTraffic() {
  if (!trafficFile.value) {
    recognitionError.value = '请先选择交警手势视频'
    return
  }

  const { frameCount, sampleInterval } = normalizeRecognitionParameters(trafficUpload)
  trafficUpload.frameCount = frameCount
  trafficUpload.sampleInterval = sampleInterval
  trafficUpload.status = 'processing'
  loading.traffic = true
  recognitionError.value = ''

  try {
    const path =
      `/api/gesture/traffic/video?frame_count=${encodeURIComponent(frameCount)}` +
      `&sample_interval=${encodeURIComponent(sampleInterval)}`
    trafficResult.value = await uploadFile(path, trafficFile.value)
    trafficUpload.status = 'success'
    await refreshAll()
  } catch (error) {
    trafficUpload.status = 'error'
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


function alertEmailStatus(item) {
  return item.email_notification_status || '未创建'
}

function emailStatusClass(item) {
  const value = alertEmailStatus(item)
  if (value === '已发送') return 'email-sent'
  if (value === '发送失败') return 'email-failed'
  if (value === '部分发送') return 'email-partial'
  if (value === '发送中' || value === '待发送') return 'email-pending'
  return 'email-skipped'
}

function canRetryAlertEmail(item) {
  return Number(item.email_failed_count || 0) > 0
}

function sleep(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function withTimeout(promise, milliseconds, message = '请求超时，请稍后重试') {
  let timerId = null

  const timeoutPromise = new Promise((_, reject) => {
    timerId = window.setTimeout(() => {
      reject(new Error(message))
    }, milliseconds)
  })

  return Promise.race([promise, timeoutPromise]).finally(() => {
    if (timerId) window.clearTimeout(timerId)
  })
}

async function waitForAlertEmailResult(alertId) {
  const maxAttempts = 12

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await sleep(1000)

    try {
      const data = await withTimeout(
        apiGet(`/api/alert-notifications/${alertId}`),
        5000,
        '读取邮件状态超时',
      )

      const status = data?.summary?.status || ''
      if (
        ['已发送', '部分发送', '发送失败', '已跳过'].includes(status)
      ) {
        return status
      }
    } catch {
      // 后台发送状态暂时读取失败时继续轮询，最后统一刷新页面。
    }
  }

  return '发送中'
}

async function retryAlertEmail(item) {
  const id = item.id || item.alert_id
  if (!id || retryingAlertId.value) return

  retryingAlertId.value = id
  globalError.value = ''

  try {
    await withTimeout(
      apiPost(`/api/alert-notifications/${id}/retry`, {}),
      10000,
      '邮件重试请求超时',
    )

    await waitForAlertEmailResult(id)
    await refreshAll()
  } catch (error) {
    globalError.value = `邮件重试失败：${error.message}`
    await refreshAll()
  } finally {
    retryingAlertId.value = null
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
  revokePreviewUrl(plateUpload)
  revokePreviewUrl(trafficUpload)
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

.model-test-header .panel-title {
  align-items: center;
}

.input-mode-badge {
  min-width: 156px;
  padding: 12px 14px;
  border: 1px solid rgba(34, 211, 238, 0.24);
  border-radius: 13px;
  background: rgba(8, 145, 178, 0.12);
}

.input-mode-badge span,
.input-mode-badge strong {
  display: block;
}

.input-mode-badge span {
  color: #64748b;
  font-size: 11px;
}

.input-mode-badge strong {
  margin-top: 4px;
  color: #67e8f9;
}

.video-drop-zone {
  display: grid;
  place-items: center;
  gap: 8px;
  min-height: 198px;
  padding: 22px;
  border: 1px dashed #3b5878;
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(8, 145, 178, 0.08), rgba(37, 99, 235, 0.05)),
    #081426;
  text-align: center;
  transition: border-color 0.2s ease, background 0.2s ease;
}

.video-drop-zone:hover {
  border-color: #22d3ee;
  background:
    linear-gradient(135deg, rgba(8, 145, 178, 0.14), rgba(37, 99, 235, 0.08)),
    #081426;
}

.drop-zone-icon {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border-radius: 14px;
  background: rgba(8, 145, 178, 0.18);
  color: #67e8f9;
  font-size: 22px;
}

.video-drop-zone strong {
  color: #f8fafc;
}

.video-drop-zone span {
  max-width: 360px;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}

.file-select-button,
.primary-action {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  min-height: 42px;
  padding: 10px 14px;
  border: 0;
  border-radius: 11px;
  background: #0891b2;
  color: #ffffff;
  font-weight: 800;
  cursor: pointer;
}

.file-select-button {
  margin-top: 5px;
}

.hidden-file-input {
  display: none;
}

.local-video-preview {
  margin-top: 13px;
  overflow: hidden;
  border: 1px solid #26354d;
  border-radius: 14px;
  background: #020617;
}

.local-video-preview video {
  display: block;
  width: 100%;
  max-height: 280px;
  background: #020617;
}

.selected-file-card {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1729;
}

.selected-file-card span,
.selected-file-card strong,
.selected-file-card small {
  display: block;
}

.selected-file-card span,
.selected-file-card small {
  color: #64748b;
  font-size: 12px;
}

.selected-file-card strong {
  max-width: 310px;
  margin: 4px 0;
  overflow: hidden;
  color: #f8fafc;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-button {
  padding: 7px 10px !important;
  background: #334155 !important;
  color: #cbd5e1 !important;
  font-size: 12px;
}

.recognition-parameter-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 11px;
  margin-top: 13px;
}

.recognition-parameter-grid label {
  display: grid;
  gap: 6px;
  padding: 11px;
  border: 1px solid #26354d;
  border-radius: 12px;
  background: #0b1729;
  color: #cbd5e1;
  font-size: 13px;
  font-weight: 700;
}

.recognition-parameter-grid input {
  width: 100%;
  padding: 9px 10px;
  border: 1px solid #334155;
  border-radius: 9px;
  outline: none;
  background: #07111f;
  color: #f8fafc;
}

.recognition-parameter-grid input:focus {
  border-color: #22d3ee;
}

.recognition-parameter-grid small {
  color: #64748b;
  font-weight: 500;
}

.primary-action {
  width: 100%;
  margin-top: 13px;
}

.primary-action:disabled,
.file-select-button:has(input:disabled),
.panel button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.recognition-status {
  display: grid;
  gap: 4px;
  margin-top: 11px;
  padding: 10px 12px;
  border: 1px solid #26354d;
  border-radius: 11px;
  background: #0b1729;
}

.recognition-status span {
  font-weight: 800;
}

.recognition-status small {
  color: #64748b;
  line-height: 1.5;
}

.status-processing {
  border-color: rgba(34, 211, 238, 0.34);
}

.status-processing span {
  color: #67e8f9;
}

.status-success {
  border-color: rgba(16, 185, 129, 0.32);
}

.status-success span {
  color: #6ee7b7;
}

.status-error {
  border-color: rgba(248, 113, 113, 0.34);
}

.status-error span {
  color: #fca5a5;
}

.result-success-badge {
  display: inline-flex;
  max-width: 260px;
  padding: 6px 10px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(16, 185, 129, 0.14);
  color: #6ee7b7;
  font-size: 12px;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-result-stack {
  display: grid;
  gap: 14px;
}

.result-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 9px;
}

.result-summary-grid > div {
  min-width: 0;
  padding: 12px;
  border: 1px solid #26354d;
  border-radius: 12px;
  background: #0b1729;
}

.result-summary-grid span,
.result-summary-grid strong,
.result-summary-grid small {
  display: block;
}

.result-summary-grid span,
.result-summary-grid small {
  color: #64748b;
  font-size: 11px;
}

.result-summary-grid strong {
  margin: 6px 0;
  color: #f8fafc;
  font-size: 20px;
}

.result-media-card {
  overflow: hidden;
  border: 1px solid #26354d;
  border-radius: 14px;
  background: #07111f;
}

.result-media-card > div:first-child {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 13px;
  background: #0b1729;
}

.result-media-card span {
  font-weight: 800;
}

.result-media-card small {
  color: #64748b;
}

.result-media-card img {
  display: block;
  width: 100%;
  max-height: 430px;
  object-fit: contain;
  background: #020617;
}

.result-table strong {
  color: #f8fafc;
}

.result-empty {
  min-height: 420px;
  display: grid;
  place-items: center;
  line-height: 1.8;
}

.traffic-primary-result {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: center;
  padding: 18px;
  border: 1px solid rgba(34, 211, 238, 0.22);
  border-radius: 15px;
  background:
    linear-gradient(135deg, rgba(8, 145, 178, 0.15), rgba(37, 99, 235, 0.08)),
    #0b1729;
}

.traffic-primary-result span,
.traffic-primary-result strong {
  display: block;
}

.traffic-primary-result > div:first-child > span {
  color: #64748b;
}

.traffic-primary-result > div:first-child > strong {
  margin-top: 5px;
  color: #f8fafc;
  font-size: 26px;
}

.traffic-primary-result p {
  margin: 8px 0 0;
  color: #67e8f9;
}

.confidence-orbit {
  display: grid;
  width: 104px;
  height: 104px;
  flex: 0 0 104px;
  place-items: center;
  align-content: center;
  border: 7px solid rgba(34, 211, 238, 0.28);
  border-radius: 50%;
  background: #07111f;
  text-align: center;
}

.confidence-orbit strong {
  color: #f8fafc;
  font-size: 24px;
}

.confidence-orbit span {
  margin-top: 3px;
  color: #64748b;
  font-size: 11px;
}

.rule-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
}

.rule-detail-grid > div {
  min-width: 0;
  padding: 11px 12px;
  border: 1px solid #26354d;
  border-radius: 11px;
  background: #0b1729;
}

.rule-detail-grid span,
.rule-detail-grid strong {
  display: block;
}

.rule-detail-grid span {
  color: #64748b;
  font-size: 11px;
}

.rule-detail-grid strong {
  margin-top: 5px;
  overflow-wrap: anywhere;
  color: #f8fafc;
  font-size: 13px;
}

.signature-panel {
  display: grid;
  gap: 10px;
  padding: 13px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1729;
}

.signature-panel > div:first-child {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.signature-panel small {
  color: #64748b;
}

.signature-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.signature-grid span {
  padding: 8px;
  border: 1px solid #334155;
  border-radius: 9px;
  background: #07111f;
  color: #64748b;
  text-align: center;
  font-size: 12px;
  font-weight: 800;
}

.signature-grid span.matched {
  border-color: rgba(16, 185, 129, 0.44);
  background: rgba(16, 185, 129, 0.12);
  color: #6ee7b7;
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
  .recognition-result,
  .recognition-parameter-grid,
  .result-summary-grid,
  .rule-detail-grid,
  .signature-grid {
    grid-template-columns: 1fr;
  }

  .traffic-primary-result,
  .selected-file-card,
  .result-media-card > div:first-child,
  .signature-panel > div:first-child {
    align-items: stretch;
    flex-direction: column;
  }

  .confidence-orbit {
    width: 92px;
    height: 92px;
    flex-basis: 92px;
  }

  .metric-card {
    grid-column: span 12;
  }

  .topbar-actions {
    flex-wrap: wrap;
  }
}

.plate-filter-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 18px;
  padding: 12px 14px;
  border: 1px solid rgba(94, 234, 212, 0.18);
  border-radius: 12px;
  background: rgba(13, 148, 136, 0.07);
  color: #8fa9c6;
  font-size: 12px;
  line-height: 1.7;
}

.plate-filter-summary span:last-child {
  color: #5eead4;
}


.table-detail-button,
.record-modal-close {
  border: 1px solid rgba(103, 232, 249, 0.35);
  border-radius: 9px;
  background: rgba(8, 145, 178, 0.12);
  color: #67e8f9;
  cursor: pointer;
  font-weight: 700;
  padding: 7px 11px;
}

.record-summary-cell {
  min-width: 220px;
  color: #dbeafe;
  font-weight: 650;
}

.record-modal-backdrop {
  position: fixed;
  z-index: 1000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(2, 6, 23, 0.82);
  backdrop-filter: blur(8px);
}

.record-modal {
  width: min(1080px, 96vw);
  max-height: 92vh;
  overflow: auto;
  padding: 22px;
  border: 1px solid #334155;
  border-radius: 18px;
  background: #071427;
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.48);
}

.record-modal-header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  padding-bottom: 16px;
  border-bottom: 1px solid #26354d;
}

.record-modal-header h2 {
  margin: 4px 0;
}

.record-modal-header span {
  color: #64748b;
  font-size: 13px;
}

.record-detail-grid,
.record-media-grid {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}

.record-detail-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.record-detail-grid > div,
.record-result-panel {
  padding: 14px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #0b1729;
}

.record-detail-grid span,
.record-detail-grid strong {
  display: block;
}

.record-detail-grid span {
  color: #64748b;
  font-size: 12px;
}

.record-detail-grid strong {
  margin-top: 7px;
  color: #f8fafc;
}

.record-media-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.record-media-grid figure {
  margin: 0;
  overflow: hidden;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #020617;
}

.record-media-grid figcaption {
  padding: 10px 12px;
  color: #94a3b8;
  font-size: 12px;
}

.record-media-grid img {
  display: block;
  width: 100%;
  max-height: 420px;
  object-fit: contain;
}

.record-result-panel {
  margin-top: 16px;
}

.record-result-panel h3 {
  margin-top: 0;
}

.record-plate-list {
  display: grid;
  gap: 10px;
}

.record-plate-list > div {
  display: grid;
  grid-template-columns: 1.2fr repeat(3, minmax(100px, 1fr));
  gap: 12px;
  padding: 10px;
  border-radius: 10px;
  background: #071427;
}

.record-plate-list span {
  color: #94a3b8;
}

.record-gesture-detail {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.record-gesture-detail div {
  padding: 12px;
  border-radius: 10px;
  background: #071427;
}

.record-gesture-detail dt {
  color: #64748b;
  font-size: 12px;
}

.record-gesture-detail dd {
  margin: 7px 0 0;
  color: #f8fafc;
  font-weight: 700;
}

.record-json-panel {
  margin-top: 16px;
  border: 1px solid #26354d;
  border-radius: 13px;
  background: #020617;
}

.record-json-panel summary {
  padding: 13px 15px;
  color: #67e8f9;
  cursor: pointer;
  font-weight: 700;
}

.record-json-panel pre {
  max-height: 420px;
  overflow: auto;
  margin: 0;
  padding: 15px;
  border-top: 1px solid #26354d;
  color: #cbd5e1;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 820px) {
  .record-detail-grid,
  .record-media-grid,
  .record-gesture-detail {
    grid-template-columns: 1fr;
  }

  .record-plate-list > div {
    grid-template-columns: 1fr;
  }
}


.email-status-tag {
  display: inline-flex;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}

.email-sent {
  background: rgba(16, 185, 129, 0.13);
  color: #5eead4;
}

.email-failed {
  background: rgba(239, 68, 68, 0.13);
  color: #fca5a5;
}

.email-partial {
  background: rgba(245, 158, 11, 0.13);
  color: #fbbf24;
}

.email-pending {
  background: rgba(59, 130, 246, 0.13);
  color: #93c5fd;
}

.email-skipped {
  background: rgba(148, 163, 184, 0.12);
  color: #94a3b8;
}

.table-muted {
  color: #64748b;
}

</style>
