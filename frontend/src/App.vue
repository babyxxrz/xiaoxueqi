<template>
  <div class="page">
    <h1>智能车载视觉感知系统</h1>
    <p class="subtitle">
      车牌识别、沙盘视频流、车主手势 AI 识别、交警手势 AI 识别、告警中心、操作日志
    </p>

    <div class="dashboard">
      <div class="stat-card">
        <div class="stat-title">识别总数</div>
        <div class="stat-value">{{ dashboard.total_records }}</div>
      </div>

      <div class="stat-card">
        <div class="stat-title">今日识别</div>
        <div class="stat-value">{{ dashboard.today_records }}</div>
      </div>

      <div class="stat-card">
        <div class="stat-title">告警总数</div>
        <div class="stat-value">{{ dashboard.total_alerts }}</div>
      </div>

      <div class="stat-card warning">
        <div class="stat-title">未处理告警</div>
        <div class="stat-value">{{ dashboard.unresolved_alerts }}</div>
      </div>
    </div>

    <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
    <p v-if="successMessage" class="success">{{ successMessage }}</p>

    <div class="card">
      <h2>系统自检</h2>

      <button class="secondary" @click="fetchSelfCheck">
        执行系统自检
      </button>

      <div v-if="systemCheck" class="check-grid">
        <div
          v-for="(value, key) in systemCheck.checks"
          :key="key"
          class="check-item"
        >
          <span>{{ key }}</span>
          <strong>{{ formatCheckValue(value) }}</strong>
        </div>
      </div>

      <p v-if="systemCheck" class="success">
        {{ systemCheck.message }}
      </p>
    </div>

    <div class="card">
      <h2>道路车辆车牌识别</h2>

      <div class="form-row">
        <input type="file" accept="image/*" @change="handleFileChange" />

        <label class="checkbox-label">
          <input v-model="simulateLowConfidence" type="checkbox" />
          模拟低置信度告警
        </label>
      </div>

      <div v-if="previewUrl" class="preview-block">
        <h3>待识别图片</h3>
        <img :src="previewUrl" class="preview-image" />
      </div>

      <button :disabled="!selectedFile || loading" @click="uploadImage">
        {{ loading ? "识别中..." : "上传并识别" }}
      </button>

      <button class="secondary" @click="refreshAll">
        刷新全部数据
      </button>

      <button class="danger" @click="createTestAlert">
        生成测试告警
      </button>
    </div>

    <div class="card">
      <h2>沙盘视频连续帧识别</h2>
      <p class="hint">
        从沙盘 RTSP 视频流中连续读取多帧，按间隔抽样后执行车牌 / 交警手势 / 车主手势识别，并对多帧结果做融合判断。
      </p>

      <div class="form-row">
        <select v-model="selectedRtspSource">
          <option
            v-for="source in rtspSources"
            :key="source.id"
            :value="source.id"
          >
            {{ source.name }} - {{ source.id }}
          </option>
        </select>

        <select v-model="streamTaskType">
          <option value="plate">车牌识别</option>
          <option value="traffic_gesture">交警手势识别</option>
          <option value="owner_gesture">车主手势识别</option>
          <option value="all">综合识别</option>
        </select>

        <label class="number-label">
          连续读取帧数
          <input v-model.number="streamFrameCount" type="number" min="5" max="300" />
        </label>

        <label class="number-label">
          抽样间隔
          <input v-model.number="streamSampleInterval" type="number" min="1" max="60" />
        </label>

        <label class="checkbox-label">
          <input v-model="useMockRtspFrame" type="checkbox" />
          使用模拟视频帧离线演示
        </label>
      </div>

      <button
        :disabled="!selectedRtspSource || streamLoading"
        @click="recognizeStreamFrames"
      >
        {{ streamLoading ? "连续帧识别中..." : "开始连续帧识别" }}
      </button>

      <div v-if="streamResult" class="result-box">
        <h3>连续帧识别结果</h3>
        <p><strong>记录 ID：</strong>{{ streamResult.record_id }}</p>
        <p><strong>告警 ID：</strong>{{ streamResult.alert_id || "无" }}</p>
        <p><strong>视频源：</strong>{{ streamResult.source.name }} - {{ streamResult.source.id }}</p>
        <p><strong>识别任务：</strong>{{ getStreamTaskName(streamResult.task_type) }}</p>
        <p><strong>输入类型：</strong>{{ streamResult.input_type }}</p>
        <p><strong>读取帧数：</strong>{{ streamResult.frames_read }}</p>
        <p><strong>抽样帧数：</strong>{{ streamResult.sampled_frames }}</p>
        <p><strong>抽样间隔：</strong>{{ streamResult.sample_interval }}</p>
        <p><strong>融合策略：</strong>{{ streamResult.result.stream_strategy || "-" }}</p>

        <div v-if="streamSourceImageUrl" class="preview-block">
          <h3>最佳视频帧原图</h3>
          <img :src="streamSourceImageUrl" class="preview-image" />
        </div>

        <div v-if="streamOutputImageUrl" class="preview-block">
          <h3>最佳视频帧标注图</h3>
          <img :src="streamOutputImageUrl" class="preview-image" />
        </div>

        <div v-if="streamResult.task_type === 'plate'">
          <table v-if="streamPlates.length > 0">
            <thead>
              <tr>
                <th>车牌号</th>
                <th>颜色</th>
                <th>置信度</th>
                <th>定位框</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(plate, index) in streamPlates" :key="index">
                <td>{{ plate.plate_number }}</td>
                <td>{{ plate.plate_color }}</td>
                <td>{{ plate.confidence }}</td>
                <td>{{ plate.bbox ? plate.bbox.join(", ") : "-" }}</td>
              </tr>
            </tbody>
          </table>

          <p v-else class="hint">连续帧中未检测到稳定车牌。</p>
        </div>

        <div v-else-if="streamResult.task_type === 'traffic_gesture' || streamResult.task_type === 'owner_gesture'">
          <p><strong>手势：</strong>{{ streamResult.result.gesture_name || "-" }}</p>
          <p><strong>手势代码：</strong>{{ streamResult.result.gesture || "-" }}</p>
          <p><strong>置信度：</strong>{{ streamResult.result.confidence ?? "-" }}</p>
          <p v-if="streamResult.result.traffic_command">
            <strong>交通指令：</strong>{{ streamResult.result.traffic_command }}
          </p>
          <p v-if="streamResult.result.description">
            <strong>控制说明：</strong>{{ streamResult.result.description }}
          </p>
        </div>

        <div v-else-if="streamResult.task_type === 'all'" class="task-summary-grid">
          <div
            v-for="task in ['plate', 'traffic_gesture', 'owner_gesture']"
            :key="task"
            class="task-summary-card"
          >
            <h4>{{ getStreamTaskName(task) }}</h4>
            <p>{{ getStreamTaskBrief(task, streamResult.result.tasks?.[task]?.result || {}) }}</p>
            <p>最佳帧：{{ streamResult.result.tasks?.[task]?.best_frame_index ?? "-" }}</p>
            <p>置信度：{{ streamResult.result.tasks?.[task]?.confidence ?? "-" }}</p>
          </div>
        </div>

        <h3>抽样帧摘要</h3>
        <table v-if="streamFrameResults.length > 0">
          <thead>
            <tr>
              <th>帧序号</th>
              <th>置信度</th>
              <th>摘要</th>
              <th>原图</th>
              <th>标注图</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in streamFrameResults" :key="index">
              <td>{{ item.frame_index }}</td>
              <td>{{ item.confidence }}</td>
              <td class="left">{{ getFrameResultBrief(streamResult.task_type, item) }}</td>
              <td>
                <a v-if="item.image_url" :href="API_BASE_URL + item.image_url" target="_blank">查看</a>
                <span v-else>-</span>
              </td>
              <td>
                <a v-if="item.output_image_url" :href="API_BASE_URL + item.output_image_url" target="_blank">查看</a>
                <span v-else>-</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="hint">综合识别模式下请查看上方各任务摘要，详细帧结果已写入返回 JSON。</p>

        <h3>本次连续帧返回 JSON</h3>
        <pre>{{ streamResultJson }}</pre>
      </div>
    </div>

    <div class="card">
      <h2>全局视频源自动识别监控</h2>
      <p class="hint">
        点击一次启动后，系统会自动巡检全部视频源，按固定间隔对每个 RTSP 源执行连续帧抽样识别，并自动写入识别记录、告警记录和操作日志。
      </p>

      <div class="form-row">
        <select v-model="monitorTaskType">
          <option value="plate">仅车牌识别</option>
          <option value="traffic_gesture">仅交警手势识别</option>
          <option value="owner_gesture">仅车主手势识别</option>
          <option value="all">综合识别</option>
        </select>

        <label class="number-label">
          巡检间隔秒
          <input v-model.number="monitorIntervalSeconds" type="number" min="5" max="3600" />
        </label>

        <label class="number-label">
          每源读取帧数
          <input v-model.number="monitorFrameCount" type="number" min="5" max="300" />
        </label>

        <label class="number-label">
          抽样间隔
          <input v-model.number="monitorSampleInterval" type="number" min="1" max="60" />
        </label>

        <label class="checkbox-label">
          <input v-model="monitorUseMockFrame" type="checkbox" />
          使用模拟视频帧离线演示
        </label>
      </div>

      <button
        :disabled="monitorLoading || monitorStatus?.running"
        @click="startGlobalMonitor"
      >
        {{ monitorLoading ? "启动中..." : "开始全局自动识别" }}
      </button>

      <button
        class="danger"
        :disabled="monitorLoading || !monitorStatus?.running"
        @click="stopGlobalMonitor"
      >
        停止全局自动识别
      </button>

      <button class="secondary" @click="fetchMonitorStatus">
        刷新监控状态
      </button>

      <div v-if="monitorStatus" class="result-box">
        <h3>全局监控状态</h3>
        <div class="monitor-status-grid">
          <div>
            <span class="panel-label">运行状态</span>
            <strong :class="monitorStatus.running ? 'text-success' : 'text-muted'">
              {{ monitorStatus.running ? "运行中" : "已停止" }}
            </strong>
          </div>
          <div>
            <span class="panel-label">任务类型</span>
            <strong>{{ getStreamTaskName(monitorStatus.task_type) }}</strong>
          </div>
          <div>
            <span class="panel-label">视频源数量</span>
            <strong>{{ monitorStatus.source_ids?.length || 0 }}</strong>
          </div>
          <div>
            <span class="panel-label">已完成轮次</span>
            <strong>{{ monitorStatus.rounds_completed }}</strong>
          </div>
          <div>
            <span class="panel-label">本次自动生成记录</span>
            <strong>{{ monitorStatus.total_records_created }}</strong>
          </div>
          <div>
            <span class="panel-label">本次自动生成告警</span>
            <strong>{{ monitorStatus.total_alerts_created }}</strong>
          </div>
        </div>

        <p><strong>启动时间：</strong>{{ monitorStatus.started_at || "-" }}</p>
        <p><strong>最近一轮：</strong>{{ monitorStatus.last_round_at || "-" }}</p>
        <p><strong>下一轮：</strong>{{ monitorStatus.next_round_after || "-" }}</p>

        <h3>视频源自动巡检状态</h3>
        <table v-if="monitorSourceRows.length > 0">
          <thead>
            <tr>
              <th>视频源</th>
              <th>状态</th>
              <th>最近完成时间</th>
              <th>记录 ID</th>
              <th>告警 ID</th>
              <th>车牌数量</th>
              <th>最近结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in monitorSourceRows" :key="row.source_id">
              <td>{{ row.source_name }} / {{ row.source_id }}</td>
              <td>{{ getMonitorStatusName(row.status) }}</td>
              <td>{{ row.last_finished_at || "-" }}</td>
              <td>{{ row.last_record_id || "-" }}</td>
              <td>{{ row.last_alert_id || "无" }}</td>
              <td>{{ row.last_plate_count ?? 0 }}</td>
              <td class="left">{{ getMonitorRowBrief(row) }}</td>
            </tr>
          </tbody>
        </table>

        <h3>最近自动巡检事件</h3>
        <table v-if="monitorRecentEvents.length > 0">
          <thead>
            <tr>
              <th>时间</th>
              <th>视频源</th>
              <th>状态</th>
              <th>记录/告警</th>
              <th>摘要</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(event, index) in monitorRecentEvents" :key="index">
              <td>{{ event.time }}</td>
              <td>{{ event.source_name }} / {{ event.source_id }}</td>
              <td>{{ getMonitorStatusName(event.status) }}</td>
              <td>记录 {{ event.record_id || "-" }} / 告警 {{ event.alert_id || "无" }}</td>
              <td class="left">{{ getMonitorEventBrief(event) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="hint">暂无自动巡检事件。启动后系统会按轮次更新这里。</p>
      </div>
    </div>

    <div v-if="plateResult" class="card">
      <h2>本次车牌识别结果</h2>

      <p><strong>记录 ID：</strong>{{ plateResult.record_id }}</p>
      <p><strong>告警 ID：</strong>{{ plateResult.alert_id || "无" }}</p>
      <p><strong>接口状态：</strong>{{ plateResult.status }}</p>
      <p><strong>原始文件名：</strong>{{ plateResult.original_filename }}</p>
      <p><strong>模型：</strong>{{ plateResult.result.model || "-" }}</p>

      <div v-if="serverImageUrl" class="preview-block">
        <h3>后端保存原图</h3>
        <img :src="serverImageUrl" class="preview-image" />
      </div>

      <div v-if="outputImageUrl" class="preview-block">
        <h3>识别标注结果图</h3>
        <img :src="outputImageUrl" class="preview-image" />
      </div>

      <table v-if="currentPlates.length > 0">
        <thead>
          <tr>
            <th>车牌号</th>
            <th>车牌颜色</th>
            <th>置信度</th>
            <th>定位框</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(plate, index) in currentPlates" :key="index">
            <td>{{ plate.plate_number }}</td>
            <td>{{ plate.plate_color }}</td>
            <td>{{ plate.confidence }}</td>
            <td>{{ plate.bbox.join(", ") }}</td>
          </tr>
        </tbody>
      </table>

      <p v-else class="hint">当前图片未检测到车牌。</p>

      <h3>本次返回 JSON</h3>
      <pre>{{ plateResultJson }}</pre>
    </div>

    <div class="card">
      <h2>车主手势控车</h2>

      <div class="vehicle-panel">
        <div>
          <span class="panel-label">系统状态</span>
          <strong>{{ vehicleState.system_awake ? "已唤醒" : "未唤醒" }}</strong>
        </div>

        <div>
          <span class="panel-label">当前功能</span>
          <strong>{{ vehicleState.current_function }}</strong>
        </div>

        <div>
          <span class="panel-label">音量</span>
          <strong>{{ vehicleState.volume }}</strong>
        </div>

        <div>
          <span class="panel-label">空调温度</span>
          <strong>{{ vehicleState.temperature }}℃</strong>
        </div>

        <div>
          <span class="panel-label">电话状态</span>
          <strong>{{ vehicleState.phone_status }}</strong>
        </div>
      </div>

      <h3>按钮模拟手势</h3>

      <div class="button-group">
        <button
          v-for="gesture in ownerGestures"
          :key="gesture.value"
          @click="simulateOwnerGesture(gesture.value)"
        >
          {{ gesture.label }}
        </button>
      </div>

      <div v-if="ownerResult" class="result-box">
        <h3>车主手势模拟结果</h3>
        <p><strong>记录 ID：</strong>{{ ownerResult.record_id }}</p>
        <p><strong>手势：</strong>{{ ownerResult.result.gesture_name }}</p>
        <p><strong>动作：</strong>{{ ownerResult.result.action }}</p>
        <p><strong>说明：</strong>{{ ownerResult.result.description }}</p>
        <p><strong>置信度：</strong>{{ ownerResult.result.confidence }}</p>
      </div>

      <div class="upload-block">
        <h3>车主手势图片 AI 识别</h3>

        <div class="form-row">
          <input
            type="file"
            accept="image/*"
            @change="handleOwnerGestureFileChange"
          />

          <label class="checkbox-label">
            <input v-model="ownerApplyControl" type="checkbox" />
            识别后自动执行车辆控制
          </label>
        </div>

        <div v-if="ownerGesturePreviewUrl" class="preview-block">
          <h3>待识别手势图片</h3>
          <img :src="ownerGesturePreviewUrl" class="preview-image" />
        </div>

        <button
          :disabled="!ownerGestureFile || ownerGestureLoading"
          @click="uploadOwnerGestureImage"
        >
          {{ ownerGestureLoading ? "识别中..." : "上传手势图片并识别" }}
        </button>
      </div>

      <div v-if="ownerImageResult" class="result-box">
        <h3>车主手势 AI 识别结果</h3>

        <p><strong>记录 ID：</strong>{{ ownerImageResult.record_id }}</p>
        <p><strong>告警 ID：</strong>{{ ownerImageResult.alert_id || "无" }}</p>
        <p><strong>模型：</strong>{{ ownerImageResult.result.model }}</p>
        <p><strong>手势：</strong>{{ ownerImageResult.result.gesture_name }}</p>
        <p><strong>手势代码：</strong>{{ ownerImageResult.result.gesture }}</p>
        <p><strong>置信度：</strong>{{ ownerImageResult.result.confidence }}</p>
        <p><strong>左右手：</strong>{{ ownerImageResult.result.handedness || "-" }}</p>
        <p><strong>控制动作：</strong>{{ ownerImageResult.result.action }}</p>
        <p><strong>动作说明：</strong>{{ ownerImageResult.result.description }}</p>

        <div v-if="ownerGestureSourceImageUrl" class="preview-block">
          <h3>手势原图</h3>
          <img :src="ownerGestureSourceImageUrl" class="preview-image" />
        </div>

        <div v-if="ownerGestureOutputImageUrl" class="preview-block">
          <h3>手部关键点骨架标注图</h3>
          <img :src="ownerGestureOutputImageUrl" class="preview-image" />
        </div>

        <h3>手部 21 个关键点</h3>

        <table v-if="ownerGestureLandmarks.length > 0">
          <thead>
            <tr>
              <th>关键点编号</th>
              <th>x</th>
              <th>y</th>
              <th>z</th>
              <th>像素 x</th>
              <th>像素 y</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="point in ownerGestureLandmarks" :key="point.index">
              <td>{{ point.index }}</td>
              <td>{{ point.x }}</td>
              <td>{{ point.y }}</td>
              <td>{{ point.z }}</td>
              <td>{{ point.pixel_x }}</td>
              <td>{{ point.pixel_y }}</td>
            </tr>
          </tbody>
        </table>

        <p v-else class="hint">当前图片未检测到手部关键点。</p>
      </div>
    </div>

    <div class="card">
      <h2>交警手势识别</h2>

      <h3>按钮模拟交警手势</h3>

      <div class="button-group">
        <button
          v-for="gesture in trafficGestures"
          :key="gesture.value"
          @click="simulateTrafficGesture(gesture.value)"
        >
          {{ gesture.label }}
        </button>
      </div>

      <div v-if="trafficResult" class="result-box">
        <h3>交警手势模拟结果</h3>
        <p><strong>记录 ID：</strong>{{ trafficResult.record_id }}</p>
        <p><strong>手势：</strong>{{ trafficResult.result.gesture_name }}</p>
        <p><strong>交通指令：</strong>{{ trafficResult.result.traffic_command }}</p>
        <p><strong>置信度：</strong>{{ trafficResult.result.confidence }}</p>

        <table>
          <thead>
            <tr>
              <th>关键点</th>
              <th>x</th>
              <th>y</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="point in trafficResult.result.keypoints"
              :key="point.name"
            >
              <td>{{ point.name }}</td>
              <td>{{ point.x }}</td>
              <td>{{ point.y }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="upload-block">
        <h3>交警手势图片 AI 识别</h3>

        <div class="form-row">
          <input
            type="file"
            accept="image/*"
            @change="handleTrafficGestureFileChange"
          />
        </div>

        <div v-if="trafficGesturePreviewUrl" class="preview-block">
          <h3>待识别交警手势图片</h3>
          <img :src="trafficGesturePreviewUrl" class="preview-image" />
        </div>

        <button
          :disabled="!trafficGestureFile || trafficGestureLoading"
          @click="uploadTrafficGestureImage"
        >
          {{ trafficGestureLoading ? "识别中..." : "上传交警手势图片并识别" }}
        </button>
      </div>

      <div v-if="trafficImageResult" class="result-box">
        <h3>交警手势 AI 识别结果</h3>

        <p><strong>记录 ID：</strong>{{ trafficImageResult.record_id }}</p>
        <p><strong>告警 ID：</strong>{{ trafficImageResult.alert_id || "无" }}</p>
        <p><strong>模型：</strong>{{ trafficImageResult.result.model }}</p>
        <p><strong>手势：</strong>{{ trafficImageResult.result.gesture_name }}</p>
        <p><strong>手势代码：</strong>{{ trafficImageResult.result.gesture }}</p>
        <p><strong>交通指令：</strong>{{ trafficImageResult.result.traffic_command }}</p>
        <p><strong>置信度：</strong>{{ trafficImageResult.result.confidence }}</p>

        <div v-if="trafficGestureSourceImageUrl" class="preview-block">
          <h3>交警手势原图</h3>
          <img :src="trafficGestureSourceImageUrl" class="preview-image" />
        </div>

        <div v-if="trafficGestureOutputImageUrl" class="preview-block">
          <h3>人体姿态骨架标注图</h3>
          <img :src="trafficGestureOutputImageUrl" class="preview-image" />
        </div>

        <h3>核心人体关键点</h3>

        <table v-if="trafficGestureKeypoints.length > 0">
          <thead>
            <tr>
              <th>编号</th>
              <th>名称</th>
              <th>x</th>
              <th>y</th>
              <th>可见度</th>
              <th>像素 x</th>
              <th>像素 y</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="point in trafficGestureKeypoints" :key="point.index">
              <td>{{ point.index }}</td>
              <td>{{ point.name }}</td>
              <td>{{ point.x }}</td>
              <td>{{ point.y }}</td>
              <td>{{ point.visibility }}</td>
              <td>{{ point.pixel_x }}</td>
              <td>{{ point.pixel_y }}</td>
            </tr>
          </tbody>
        </table>

        <p v-else class="hint">当前图片未检测到核心人体关键点。</p>

        <h3>MediaPipe Pose 33 个姿态关键点</h3>

        <table v-if="trafficGestureLandmarks.length > 0">
          <thead>
            <tr>
              <th>编号</th>
              <th>名称</th>
              <th>x</th>
              <th>y</th>
              <th>z</th>
              <th>可见度</th>
              <th>像素 x</th>
              <th>像素 y</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="point in trafficGestureLandmarks" :key="point.index">
              <td>{{ point.index }}</td>
              <td>{{ point.name }}</td>
              <td>{{ point.x }}</td>
              <td>{{ point.y }}</td>
              <td>{{ point.z }}</td>
              <td>{{ point.visibility }}</td>
              <td>{{ point.pixel_x }}</td>
              <td>{{ point.pixel_y }}</td>
            </tr>
          </tbody>
        </table>

        <p v-else class="hint">当前图片未检测到人体姿态。</p>
      </div>
    </div>

    <div class="card">
      <h2>历史识别记录</h2>

      <p v-if="records.length === 0">暂无历史记录</p>

      <table v-else>
        <thead>
          <tr>
            <th>ID</th>
            <th>任务类型</th>
            <th>输入类型</th>
            <th>文件名</th>
            <th>结果摘要</th>
            <th>识别时间</th>
            <th>原图</th>
            <th>标注图</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in records" :key="record.id">
            <td>{{ record.id }}</td>
            <td>{{ record.task_type }}</td>
            <td>{{ record.input_type }}</td>
            <td>{{ record.original_filename || "-" }}</td>
            <td class="left">{{ getRecordBrief(record) }}</td>
            <td>{{ record.created_at }}</td>
            <td>
              <a
                v-if="record.image_url"
                :href="API_BASE_URL + record.image_url"
                target="_blank"
              >
                查看原图
              </a>
              <span v-else>-</span>
            </td>
            <td>
              <a
                v-if="record.output_image_url"
                :href="API_BASE_URL + record.output_image_url"
                target="_blank"
              >
                查看标注图
              </a>
              <span v-else>-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>


    <div class="card">
      <h2>告警智能分析</h2>
      <p class="hint">
        基于告警记录进行规则推理，自动统计未处理告警、主要风险类型、风险等级，并给出处理建议。
      </p>

      <button class="secondary" @click="fetchAlertAnalysis">
        刷新告警智能分析
      </button>

      <div v-if="alertAnalysis" class="analysis-panel">
        <div class="analysis-grid">
          <div class="analysis-item">
            <span>风险等级</span>
            <strong :class="['risk-text', alertAnalysis.risk_level]">
              {{ alertAnalysis.risk_level_name }}
            </strong>
          </div>
          <div class="analysis-item">
            <span>告警总数</span>
            <strong>{{ alertAnalysis.total_count }}</strong>
          </div>
          <div class="analysis-item">
            <span>未处理告警</span>
            <strong>{{ alertAnalysis.unresolved_count }}</strong>
          </div>
          <div class="analysis-item">
            <span>生成时间</span>
            <strong>{{ alertAnalysis.generated_at }}</strong>
          </div>
        </div>

        <div class="analysis-block">
          <h3>智能分析结论</h3>
          <p>{{ alertAnalysis.analysis }}</p>
        </div>

        <div class="analysis-block">
          <h3>主要风险类型</h3>
          <p v-if="alertAnalysis.main_risk_types.length === 0" class="hint">暂无主要风险类型。</p>
          <div v-else class="risk-tags">
            <span
              v-for="item in alertAnalysis.main_risk_types"
              :key="item"
              class="risk-tag"
            >
              {{ item }}
            </span>
          </div>
        </div>

        <div class="analysis-block">
          <h3>风险类型统计</h3>
          <table v-if="alertAnalysis.risk_type_stats.length > 0">
            <thead>
              <tr>
                <th>风险类型</th>
                <th>数量</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in alertAnalysis.risk_type_stats" :key="item.risk_type">
                <td>{{ item.risk_type }}</td>
                <td>{{ item.count }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="hint">暂无统计数据。</p>
        </div>

        <div class="analysis-block">
          <h3>处理建议</h3>
          <ol>
            <li v-for="item in alertAnalysis.suggestions" :key="item">
              {{ item }}
            </li>
          </ol>
        </div>
      </div>

      <p v-else class="hint">暂无告警分析结果，请点击刷新或执行一次识别任务。</p>
    </div>

    <div class="card">
      <h2>告警中心</h2>

      <p v-if="alerts.length === 0">暂无告警记录</p>

      <table v-else>
        <thead>
          <tr>
            <th>ID</th>
            <th>级别</th>
            <th>类型</th>
            <th>摘要</th>
            <th>原因</th>
            <th>建议</th>
            <th>状态</th>
            <th>关联记录</th>
            <th>时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="alert in alerts" :key="alert.id">
            <td>{{ alert.id }}</td>
            <td>
              <span :class="['tag', alert.level]">
                {{ alert.level }}
              </span>
            </td>
            <td>{{ alert.event_type }}</td>
            <td>{{ alert.summary }}</td>
            <td>{{ alert.reason }}</td>
            <td>{{ alert.suggestion }}</td>
            <td>{{ alert.status }}</td>
            <td>{{ alert.related_record_id || "-" }}</td>
            <td>{{ alert.created_at }}</td>
            <td>
              <button
                v-if="alert.status === '未处理'"
                class="small"
                @click="resolveAlert(alert.id)"
              >
                标记处理
              </button>
              <span v-else>-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h2>操作日志</h2>

      <p v-if="logs.length === 0">暂无操作日志</p>

      <table v-else>
        <thead>
          <tr>
            <th>ID</th>
            <th>操作</th>
            <th>详情</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in logs" :key="log.id">
            <td>{{ log.id }}</td>
            <td>{{ log.action }}</td>
            <td class="left">{{ formatDetail(log.detail) }}</td>
            <td>{{ log.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const API_BASE_URL = "http://127.0.0.1:8000";

const selectedFile = ref(null);
const previewUrl = ref("");
const plateResult = ref(null);
const streamResult = ref(null);
const ownerResult = ref(null);
const ownerImageResult = ref(null);
const trafficResult = ref(null);
const trafficImageResult = ref(null);

const ownerGestureFile = ref(null);
const ownerGesturePreviewUrl = ref("");
const ownerGestureLoading = ref(false);
const ownerApplyControl = ref(true);

const trafficGestureFile = ref(null);
const trafficGesturePreviewUrl = ref("");
const trafficGestureLoading = ref(false);

const records = ref([]);
const alerts = ref([]);
const logs = ref([]);
const alertAnalysis = ref(null);

const loading = ref(false);
const errorMessage = ref("");
const successMessage = ref("");
const simulateLowConfidence = ref(false);

const systemCheck = ref(null);

const rtspSources = ref([]);
const selectedRtspSource = ref("");
const useMockRtspFrame = ref(false);
const streamTaskType = ref("plate");
const streamFrameCount = ref(50);
const streamSampleInterval = ref(5);
const streamLoading = ref(false);

const monitorTaskType = ref("plate");
const monitorIntervalSeconds = ref(30);
const monitorFrameCount = ref(20);
const monitorSampleInterval = ref(5);
const monitorUseMockFrame = ref(false);
const monitorLoading = ref(false);
const monitorStatus = ref(null);
let monitorStatusTimer = null;

const dashboard = ref({
  total_records: 0,
  today_records: 0,
  total_alerts: 0,
  unresolved_alerts: 0,
});

const vehicleState = ref({
  system_awake: false,
  current_function: "home",
  volume: 50,
  temperature: 24,
  phone_status: "空闲",
  updated_at: "",
});

const ownerGestures = [
  { label: "手掌张开：唤醒", value: "open_palm" },
  { label: "握拳：确认", value: "fist" },
  { label: "左滑：上一个功能", value: "swipe_left" },
  { label: "右滑：下一个功能", value: "swipe_right" },
  { label: "拇指向上：音量+/接听", value: "thumb_up" },
  { label: "拇指向下：音量-/挂断", value: "thumb_down" },
  { label: "挥手：返回主页", value: "wave" },
  { label: "单指画圈：调节", value: "circle" },
];

const trafficGestures = [
  { label: "停止信号", value: "stop" },
  { label: "直行信号", value: "straight" },
  { label: "左转弯信号", value: "left_turn" },
  { label: "左转弯待转", value: "left_turn_wait" },
  { label: "右转弯信号", value: "right_turn" },
  { label: "变道信号", value: "lane_change" },
  { label: "减速慢行", value: "slow_down" },
  { label: "靠边停车", value: "pull_over" },
];

const currentPlates = computed(() => {
  return plateResult.value?.result?.plates || [];
});

const serverImageUrl = computed(() => {
  if (!plateResult.value?.image_url) {
    return "";
  }
  return `${API_BASE_URL}${plateResult.value.image_url}`;
});

const outputImageUrl = computed(() => {
  if (!plateResult.value?.output_image_url) {
    return "";
  }
  return `${API_BASE_URL}${plateResult.value.output_image_url}`;
});

const plateResultJson = computed(() => {
  return plateResult.value ? JSON.stringify(plateResult.value, null, 2) : "";
});

const streamSourceImageUrl = computed(() => {
  if (!streamResult.value?.image_url) {
    return "";
  }
  return `${API_BASE_URL}${streamResult.value.image_url}`;
});

const streamOutputImageUrl = computed(() => {
  if (!streamResult.value?.output_image_url) {
    return "";
  }
  return `${API_BASE_URL}${streamResult.value.output_image_url}`;
});

const streamPlates = computed(() => {
  return streamResult.value?.result?.plates || [];
});

const streamFrameResults = computed(() => {
  return streamResult.value?.result?.frame_results || [];
});

const streamResultJson = computed(() => {
  return streamResult.value ? JSON.stringify(streamResult.value, null, 2) : "";
});

const monitorSourceRows = computed(() => {
  const status = monitorStatus.value?.source_status || {};
  return Object.values(status).sort((a, b) => String(a.source_id).localeCompare(String(b.source_id)));
});

const monitorRecentEvents = computed(() => {
  return monitorStatus.value?.recent_events || [];
});

const ownerGestureSourceImageUrl = computed(() => {
  if (!ownerImageResult.value?.image_url) {
    return "";
  }
  return `${API_BASE_URL}${ownerImageResult.value.image_url}`;
});

const ownerGestureOutputImageUrl = computed(() => {
  if (!ownerImageResult.value?.output_image_url) {
    return "";
  }
  return `${API_BASE_URL}${ownerImageResult.value.output_image_url}`;
});

const ownerGestureLandmarks = computed(() => {
  return ownerImageResult.value?.result?.landmarks || [];
});

const trafficGestureSourceImageUrl = computed(() => {
  if (!trafficImageResult.value?.image_url) {
    return "";
  }
  return `${API_BASE_URL}${trafficImageResult.value.image_url}`;
});

const trafficGestureOutputImageUrl = computed(() => {
  if (!trafficImageResult.value?.output_image_url) {
    return "";
  }
  return `${API_BASE_URL}${trafficImageResult.value.output_image_url}`;
});

const trafficGestureLandmarks = computed(() => {
  return trafficImageResult.value?.result?.landmarks || [];
});

const trafficGestureKeypoints = computed(() => {
  return trafficImageResult.value?.result?.keypoints || [];
});

function clearMessages() {
  errorMessage.value = "";
  successMessage.value = "";
}

function handleFileChange(event) {
  const file = event.target.files[0];

  selectedFile.value = file || null;
  plateResult.value = null;
  clearMessages();

  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value);
  }

  previewUrl.value = file ? URL.createObjectURL(file) : "";
}

function handleOwnerGestureFileChange(event) {
  const file = event.target.files[0];

  ownerGestureFile.value = file || null;
  ownerImageResult.value = null;
  clearMessages();

  if (ownerGesturePreviewUrl.value) {
    URL.revokeObjectURL(ownerGesturePreviewUrl.value);
  }

  ownerGesturePreviewUrl.value = file ? URL.createObjectURL(file) : "";
}

function handleTrafficGestureFileChange(event) {
  const file = event.target.files[0];

  trafficGestureFile.value = file || null;
  trafficImageResult.value = null;
  clearMessages();

  if (trafficGesturePreviewUrl.value) {
    URL.revokeObjectURL(trafficGesturePreviewUrl.value);
  }

  trafficGesturePreviewUrl.value = file ? URL.createObjectURL(file) : "";
}

function formatCheckValue(value) {
  if (typeof value === "boolean") {
    return value ? "正常" : "异常";
  }

  return value;
}

async function uploadImage() {
  if (!selectedFile.value) {
    errorMessage.value = "请先选择一张图片";
    return;
  }

  loading.value = true;
  clearMessages();
  plateResult.value = null;

  try {
    const formData = new FormData();
    formData.append("file", selectedFile.value);

    const url = `${API_BASE_URL}/api/plate/image?simulate_low_confidence=${simulateLowConfidence.value}`;

    const response = await fetch(url, {
      method: "POST",
      body: formData,
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "上传失败");
    }

    plateResult.value = data;

    successMessage.value = data.alert_id
      ? `识别完成，同时生成告警 ID：${data.alert_id}`
      : "识别完成，未产生告警";

    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "请求失败，请检查后端是否启动";
  } finally {
    loading.value = false;
  }
}

async function recognizeStreamFrames() {
  clearMessages();
  streamResult.value = null;
  streamLoading.value = true;

  try {
    const response = await fetch(`${API_BASE_URL}/api/stream/recognize`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        source_id: selectedRtspSource.value,
        task_type: streamTaskType.value,
        frame_count: Number(streamFrameCount.value),
        sample_interval: Number(streamSampleInterval.value),
        use_mock_frame: useMockRtspFrame.value,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "连续帧识别失败");
    }

    streamResult.value = data;
    successMessage.value = data.alert_id
      ? `连续帧识别完成，同时生成告警 ID：${data.alert_id}`
      : `连续帧识别完成：${data.source.name}`;

    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "连续帧识别失败";
  } finally {
    streamLoading.value = false;
  }
}


async function startGlobalMonitor() {
  monitorLoading.value = true;
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/monitor/start`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        task_type: monitorTaskType.value,
        interval_seconds: Number(monitorIntervalSeconds.value),
        frame_count: Number(monitorFrameCount.value),
        sample_interval: Number(monitorSampleInterval.value),
        use_mock_frame: monitorUseMockFrame.value,
        source_ids: ["all"],
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "启动全局自动识别失败");
    }

    monitorStatus.value = data.monitor;
    successMessage.value = data.message || "全局自动识别监控已启动";
    startMonitorPolling();
    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "启动全局自动识别失败";
  } finally {
    monitorLoading.value = false;
  }
}

async function stopGlobalMonitor() {
  monitorLoading.value = true;
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/monitor/stop`, {
      method: "POST",
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "停止全局自动识别失败");
    }

    monitorStatus.value = data.monitor;
    successMessage.value = data.message || "全局自动识别监控已停止";
    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "停止全局自动识别失败";
  } finally {
    monitorLoading.value = false;
  }
}

async function fetchMonitorStatus() {
  const response = await fetch(`${API_BASE_URL}/api/monitor/status`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取全局监控状态失败");
  }

  monitorStatus.value = data.monitor || null;
}

function startMonitorPolling() {
  if (monitorStatusTimer) {
    clearInterval(monitorStatusTimer);
  }

  monitorStatusTimer = setInterval(async () => {
    try {
      await fetchMonitorStatus();
      if (monitorStatus.value?.running) {
        await Promise.all([fetchDashboard(), fetchRecords(), fetchAlerts(), fetchAlertAnalysis(), fetchLogs()]);
      }
    } catch (error) {
      console.warn("刷新全局监控状态失败", error);
    }
  }, 5000);
}

async function simulateOwnerGesture(gesture) {
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/gesture/owner/simulate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ gesture }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "车主手势模拟失败");
    }

    ownerResult.value = data;
    vehicleState.value = data.result.vehicle_state;
    successMessage.value = `车主手势执行成功：${data.result.description}`;

    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "车主手势模拟失败";
  }
}

async function uploadOwnerGestureImage() {
  if (!ownerGestureFile.value) {
    errorMessage.value = "请先选择一张手势图片";
    return;
  }

  ownerGestureLoading.value = true;
  clearMessages();
  ownerImageResult.value = null;

  try {
    const formData = new FormData();
    formData.append("file", ownerGestureFile.value);

    const url = `${API_BASE_URL}/api/gesture/owner/image?apply_control=${ownerApplyControl.value}`;

    const response = await fetch(url, {
      method: "POST",
      body: formData,
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "车主手势 AI 识别失败");
    }

    ownerImageResult.value = data;
    vehicleState.value = data.result.vehicle_state;

    successMessage.value = data.alert_id
      ? `手势识别完成，同时生成告警 ID：${data.alert_id}`
      : `手势识别完成：${data.result.gesture_name}`;

    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "车主手势 AI 识别失败";
  } finally {
    ownerGestureLoading.value = false;
  }
}

async function simulateTrafficGesture(gesture) {
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/gesture/traffic/simulate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ gesture }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "交警手势模拟失败");
    }

    trafficResult.value = data;
    successMessage.value = `交警手势识别成功：${data.result.traffic_command}`;

    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "交警手势模拟失败";
  }
}

async function uploadTrafficGestureImage() {
  if (!trafficGestureFile.value) {
    errorMessage.value = "请先选择一张交警手势图片";
    return;
  }

  trafficGestureLoading.value = true;
  clearMessages();
  trafficImageResult.value = null;

  try {
    const formData = new FormData();
    formData.append("file", trafficGestureFile.value);

    const response = await fetch(`${API_BASE_URL}/api/gesture/traffic/image`, {
      method: "POST",
      body: formData,
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "交警手势 AI 识别失败");
    }

    trafficImageResult.value = data;

    successMessage.value = data.alert_id
      ? `交警手势识别完成，同时生成告警 ID：${data.alert_id}`
      : `交警手势识别完成：${data.result.gesture_name}`;

    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "交警手势 AI 识别失败";
  } finally {
    trafficGestureLoading.value = false;
  }
}

async function fetchSelfCheck() {
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/self-check`);
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "系统自检失败");
    }

    systemCheck.value = data;
    successMessage.value = "系统自检完成";
  } catch (error) {
    errorMessage.value = error.message || "系统自检失败";
  }
}

async function fetchDashboard() {
  const response = await fetch(`${API_BASE_URL}/api/dashboard/summary`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取仪表盘数据失败");
  }

  dashboard.value = data.summary || dashboard.value;
}

async function fetchRtspSources() {
  const response = await fetch(`${API_BASE_URL}/api/rtsp/sources`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取视频源失败");
  }

  rtspSources.value = data.sources || [];

  if (!selectedRtspSource.value && rtspSources.value.length > 0) {
    selectedRtspSource.value = rtspSources.value[0].id;
  }
}

async function fetchRecords() {
  const response = await fetch(`${API_BASE_URL}/api/records?limit=20`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取历史记录失败");
  }

  records.value = data.records || [];
}

async function fetchAlerts() {
  const response = await fetch(`${API_BASE_URL}/api/alerts?limit=20`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取告警记录失败");
  }

  alerts.value = data.alerts || [];
}


async function fetchAlertAnalysis() {
  const response = await fetch(`${API_BASE_URL}/api/alerts/analysis?limit=100`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取告警智能分析失败");
  }

  alertAnalysis.value = data.analysis || null;
}

async function fetchLogs() {
  const response = await fetch(`${API_BASE_URL}/api/logs?limit=30`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取操作日志失败");
  }

  logs.value = data.logs || [];
}

async function fetchVehicleState() {
  const response = await fetch(`${API_BASE_URL}/api/vehicle/state`);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || "获取车辆状态失败");
  }

  vehicleState.value = data.state || vehicleState.value;
}

async function refreshAll() {
  try {
    await Promise.all([
      fetchDashboard(),
      fetchRtspSources(),
      fetchRecords(),
      fetchAlerts(),
      fetchAlertAnalysis(),
      fetchMonitorStatus(),
      fetchLogs(),
      fetchVehicleState(),
    ]);
  } catch (error) {
    errorMessage.value = error.message || "刷新数据失败";
  }
}

async function createTestAlert() {
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/alerts/test`, {
      method: "POST",
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "生成测试告警失败");
    }

    successMessage.value = `测试告警已生成，告警 ID：${data.alert_id}`;
    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "生成测试告警失败";
  }
}

async function resolveAlert(alertId) {
  clearMessages();

  try {
    const response = await fetch(`${API_BASE_URL}/api/alerts/${alertId}/resolve`, {
      method: "POST",
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "处理告警失败");
    }

    successMessage.value = `告警 ${data.alert_id} 已标记为已处理`;
    await refreshAll();
  } catch (error) {
    errorMessage.value = error.message || "处理告警失败";
  }
}

function getStreamTaskName(taskType) {
  const names = {
    plate: "车牌识别",
    traffic_gesture: "交警手势识别",
    owner_gesture: "车主手势识别",
    all: "综合识别",
  };

  return names[taskType] || taskType;
}

function getStreamTaskBrief(taskType, result) {
  if (taskType === "plate") {
    const plates = result.plates || [];
    if (plates.length === 0) {
      return "未检测到车牌";
    }

    return `检测到 ${plates.length} 个车牌：${plates
      .map((plate) => `${plate.plate_number}(${plate.confidence})`)
      .join("、")}`;
  }

  if (taskType === "traffic_gesture") {
    return `交警手势：${result.gesture_name || "-"}，指令：${result.traffic_command || "-"}，置信度：${result.confidence ?? "-"}`;
  }

  if (taskType === "owner_gesture") {
    return `车主手势：${result.gesture_name || "-"}，置信度：${result.confidence ?? "-"}`;
  }

  return JSON.stringify(result);
}

function getFrameResultBrief(taskType, item) {
  if (taskType === "plate") {
    if (!item.best_plate) {
      return `未检测到车牌，检测数量：${item.plates_count || 0}`;
    }

    const plates = item.plates || [];
    const plateText = plates.length > 0
      ? plates.map((plate) => plate.plate_number).join("、")
      : item.best_plate.plate_number;

    return `车牌：${plateText}，数量：${item.plates_count}`;
  }

  if (taskType === "traffic_gesture" || taskType === "owner_gesture") {
    return `手势：${item.gesture_name || "-"}，代码：${item.gesture || "-"}`;
  }

  return "综合识别帧结果请查看 JSON";
}

function getMonitorStatusName(status) {
  const names = {
    waiting: "等待中",
    running: "识别中",
    success: "成功",
    error: "异常",
    stopped: "已停止",
  };

  return names[status] || status || "-";
}

function getMonitorRowBrief(row) {
  if (row.last_error) {
    return `异常：${row.last_error}`;
  }

  const result = row.last_result || {};

  if (!result.task_type) {
    return "尚未完成识别";
  }

  if (result.task_type === "plate") {
    const plates = result.plates || [];
    if (plates.length === 0) {
      return "未检测到车牌";
    }

    return `检测到 ${plates.length} 个车牌：${plates.map((plate) => plate.plate_number).join("、")}`;
  }

  if (result.task_type === "all") {
    const plates = result.plates || [];
    return `车牌 ${plates.length} 个；交警：${result.traffic_gesture || "-"}；车主：${result.owner_gesture || "-"}`;
  }

  return `手势：${result.gesture_name || "-"}，置信度：${result.confidence ?? "-"}`;
}

function getMonitorEventBrief(event) {
  if (event.error) {
    return event.error;
  }

  return getMonitorRowBrief({
    last_result: event.summary,
    last_error: "",
  });
}

function getRecordBrief(record) {
  const result = record?.result || {};

  if (record.task_type === "plate") {
    const plate = result.plates?.[0];
    const source = result.source ? `，来源：${result.source.name}` : "";
    const model = result.model ? `，模型：${result.model}` : "";

    if (!plate) {
      return `未检测到车牌${source}${model}`;
    }

    return `车牌号：${plate.plate_number}，颜色：${plate.plate_color}，置信度：${plate.confidence}${source}${model}`;
  }

  if (record.task_type === "owner_gesture") {
    const model = result.model ? `，模型：${result.model}` : "";
    const action = result.description ? `，动作：${result.description}` : "";
    return `车主手势：${result.gesture_name || "-"}，置信度：${result.confidence ?? "-"}${action}${model}`;
  }

  if (record.task_type === "traffic_gesture") {
    const model = result.model ? `，模型：${result.model}` : "";
    return `交警手势：${result.gesture_name || "-"}，指令：${result.traffic_command || "-"}，置信度：${result.confidence ?? "-"}${model}`;
  }

  if (record.task_type === "all") {
    const tasks = result.tasks || {};
    return [
      getStreamTaskBrief("plate", tasks.plate?.result || {}),
      getStreamTaskBrief("traffic_gesture", tasks.traffic_gesture?.result || {}),
      getStreamTaskBrief("owner_gesture", tasks.owner_gesture?.result || {}),
    ].join("；");
  }

  return JSON.stringify(result);
}

function formatDetail(detail) {
  if (typeof detail === "string") {
    return detail;
  }

  return JSON.stringify(detail);
}

onMounted(() => {
  refreshAll();
  fetchSelfCheck();
  startMonitorPolling();
});

onUnmounted(() => {
  if (monitorStatusTimer) {
    clearInterval(monitorStatusTimer);
  }
});
</script>

<style scoped>
.page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 32px;
  font-family: Arial, "Microsoft YaHei", sans-serif;
}

.subtitle {
  color: #666;
}

.dashboard {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-top: 24px;
}

.stat-card {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 20px;
  background: #fff;
}

.stat-card.warning {
  border-color: #ffb020;
}

.stat-title {
  color: #666;
  font-size: 14px;
}

.stat-value {
  margin-top: 8px;
  font-size: 32px;
  font-weight: bold;
}

.card {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 24px;
  margin-top: 24px;
  background: #fff;
}

.form-row {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}

select {
  min-width: 260px;
  padding: 8px 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
}

.number-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #333;
}

.number-label input {
  width: 90px;
  padding: 8px 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
}

.task-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-top: 16px;
}

.task-summary-card {
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 14px;
  background: #fff;
}

.task-summary-card h4 {
  margin-top: 0;
  margin-bottom: 8px;
}

.monitor-status-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin: 16px 0;
}

.monitor-status-grid > div {
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 14px;
  background: #fff;
}

.text-success {
  color: #16803c;
}

.text-muted {
  color: #777;
}

button {
  margin-top: 16px;
  margin-right: 12px;
  padding: 10px 18px;
  border: none;
  border-radius: 6px;
  background: #1677ff;
  color: white;
  cursor: pointer;
}

button.secondary {
  background: #666;
}

button.danger {
  background: #d9363e;
}

button.small {
  margin: 0;
  padding: 6px 10px;
  font-size: 12px;
}

button:disabled {
  background: #aaa;
  cursor: not-allowed;
}

.button-group {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.button-group button {
  margin-right: 0;
}

.upload-block {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px dashed #ddd;
}

.preview-block {
  margin-top: 16px;
}

.preview-image {
  max-width: 100%;
  max-height: 420px;
  border: 1px solid #ddd;
  border-radius: 6px;
}

.vehicle-panel {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.vehicle-panel > div {
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 14px;
  background: #fafafa;
}

.panel-label {
  display: block;
  color: #666;
  font-size: 13px;
  margin-bottom: 8px;
}

.result-box {
  margin-top: 20px;
  padding: 16px;
  background: #f6f8fa;
  border-radius: 8px;
}

.check-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-top: 16px;
}

.check-item {
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 14px;
  background: #fafafa;
}

.check-item span {
  display: block;
  color: #666;
  font-size: 13px;
  margin-bottom: 8px;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 16px;
  font-size: 13px;
}

th,
td {
  border: 1px solid #ddd;
  padding: 8px;
  text-align: center;
  vertical-align: top;
}

td.left {
  text-align: left;
  word-break: break-all;
}

th {
  background: #f5f5f5;
}

a {
  color: #1677ff;
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

pre {
  padding: 16px;
  background: #f6f8fa;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 13px;
}

.error {
  color: #c00;
  margin-top: 12px;
}

.success {
  color: #087443;
  margin-top: 12px;
}

.hint {
  color: #666;
  margin-top: 12px;
}


.analysis-panel {
  margin-top: 16px;
  padding: 16px;
  border-radius: 8px;
  background: #f6f8fa;
}

.analysis-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.analysis-item {
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 14px;
  background: #fff;
}

.analysis-item span {
  display: block;
  color: #666;
  font-size: 13px;
  margin-bottom: 8px;
}

.analysis-item strong {
  font-size: 18px;
}

.analysis-block {
  margin-top: 18px;
}

.risk-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.risk-tag {
  display: inline-block;
  padding: 6px 10px;
  border-radius: 999px;
  background: #e8f3ff;
  color: #0958d9;
  font-size: 13px;
}

.risk-text.normal {
  color: #087443;
}

.risk-text.low {
  color: #0958d9;
}

.risk-text.medium {
  color: #8a5a00;
}

.risk-text.high {
  color: #a40000;
}

ol {
  margin-top: 8px;
  padding-left: 22px;
}

li {
  margin-bottom: 6px;
}

.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #eee;
}

.tag.warning {
  background: #fff3cd;
  color: #8a5a00;
}

.tag.critical {
  background: #ffe1e1;
  color: #a40000;
}

.tag.info {
  background: #e8f3ff;
  color: #0958d9;
}
</style>