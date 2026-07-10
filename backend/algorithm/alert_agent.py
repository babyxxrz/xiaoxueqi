"""
AlertAgent —— 智能告警代理。

功能：
1. 异常检测：基于规则引擎，检测车牌/手势/延迟等多维度异常
2. 告警级别判断：critical / high / medium / low / info
3. LLM 摘要生成：可选配置，调用 LLM API 生成自然语言告警说明
4. 告警抑制：同类型告警在冷却期内不重复生成
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import requests as _requests

# ---------------------------------------------------------------------------
# 告警级别定义
# ---------------------------------------------------------------------------
ALERT_LEVELS = ("critical", "high", "medium", "low", "info")

LEVEL_SCORE_MAP = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Alert:
    """单条告警"""

    alert_id: str
    level: str
    event_type: str
    summary: str
    reason: str = ""
    suggestion: str = ""
    status: str = "open"
    related_record_id: int | None = None
    created_at: str = ""
    source_module: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    llm_summary: str = ""
    dedup_key: str = ""


@dataclass
class AlertStats:
    """告警统计"""

    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    open: int = 0
    resolved: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    recent: list[Alert] = field(default_factory=list)
    avg_per_minute: float = 0.0


# ---------------------------------------------------------------------------
# 异常检测规则
# ---------------------------------------------------------------------------
class AnomalyDetector:
    """
    基于规则的异常检测器。

    检测维度：
    - 延迟超限
    - 车牌识别异常（长期空、置信度过低）
    - 交警手势冲突
    - 车主手势异常
    - 系统健康状态
    """

    # 延迟阈值
    LATENCY_WARNING_MS = 800       # 接口延迟告警
    LATENCY_CRITICAL_MS = 2000     # 接口延迟严重

    # 连续空车牌告警阈值
    EMPTY_PLATE_WARNING_COUNT = 10
    EMPTY_PLATE_CRITICAL_COUNT = 30

    def detect(self, snapshot: dict[str, Any]) -> list[Alert]:
        """
        输入：当前系统快照（包含 plate/traffic/owner/performance 等模块结果）
        输出：检测到的告警列表
        """
        alerts: list[Alert] = []

        # 1. 延迟检测
        latency_alerts = self._check_latency(snapshot)
        alerts.extend(latency_alerts)

        # 2. 车牌识别异常
        plate_alerts = self._check_plate_anomaly(snapshot)
        alerts.extend(plate_alerts)

        # 3. 手势识别异常
        gesture_alerts = self._check_gesture_anomaly(snapshot)
        alerts.extend(gesture_alerts)

        # 4. 系统健康
        health_alerts = self._check_system_health(snapshot)
        alerts.extend(health_alerts)

        return alerts

    def _check_latency(self, snapshot: dict[str, Any]) -> list[Alert]:
        alerts: list[Alert] = []
        perf = snapshot.get("performance", {})
        if not isinstance(perf, dict):
            return alerts

        latency = perf.get("latency_ms")
        if latency is None:
            try:
                latency = float(perf.get("total_ms", 0))
            except (TypeError, ValueError):
                return alerts

        if latency >= self.LATENCY_CRITICAL_MS:
            alerts.append(Alert(
                alert_id=f"latency_critical_{uuid.uuid4().hex[:8]}",
                level="high",
                event_type="latency_critical",
                summary=f"端到端识别延迟严重超标：{latency:.0f}ms（阈值 {self.LATENCY_CRITICAL_MS}ms）",
                reason=f"全链路延迟 {latency:.0f}ms > {self.LATENCY_CRITICAL_MS}ms，系统实时性严重受损",
                suggestion="立即降低抽帧频率，暂停非关键监控任务",
                dedup_key="latency_critical",
                source_module="performance",
            ))
        elif latency >= self.LATENCY_WARNING_MS:
            alerts.append(Alert(
                alert_id=f"latency_warning_{uuid.uuid4().hex[:8]}",
                level="medium",
                event_type="latency_warning",
                summary=f"接口延迟偏高：{latency:.0f}ms（阈值 {self.LATENCY_WARNING_MS}ms）",
                reason=f"全链路延迟 {latency:.0f}ms > {self.LATENCY_WARNING_MS}ms",
                suggestion="建议降低抽帧频率或检查网络状态",
                dedup_key="latency_warning",
                source_module="performance",
            ))

        return alerts

    def _check_plate_anomaly(self, snapshot: dict[str, Any]) -> list[Alert]:
        alerts: list[Alert] = []
        plate = snapshot.get("plate", {})
        if not isinstance(plate, dict):
            return alerts

        # 连续空检测
        consecutive_empty = plate.get("consecutive_empty_count", 0)
        if consecutive_empty >= self.EMPTY_PLATE_CRITICAL_COUNT:
            alerts.append(Alert(
                alert_id=f"plate_empty_critical_{uuid.uuid4().hex[:8]}",
                level="high",
                event_type="plate_empty_critical",
                summary=f"车牌连续 {consecutive_empty} 帧未检测到，模型可能失效",
                reason=f"连续 {consecutive_empty} 帧空车牌，超过严重阈值 {self.EMPTY_PLATE_CRITICAL_COUNT}",
                suggestion="检查摄像头角度与沙盘车牌清晰度，考虑重新训练检测模型",
                dedup_key="plate_empty_critical",
                source_module="plate",
            ))
        elif consecutive_empty >= self.EMPTY_PLATE_WARNING_COUNT:
            alerts.append(Alert(
                alert_id=f"plate_empty_warning_{uuid.uuid4().hex[:8]}",
                level="low",
                event_type="plate_empty_warning",
                summary=f"车牌连续 {consecutive_empty} 帧为空，建议关注",
                reason=f"连续 {consecutive_empty} 帧未检测到车牌",
                suggestion="检查沙盘车辆是否移动，避免静态场景误判",
                dedup_key="plate_empty_warning",
                source_module="plate",
            ))

        # 低置信度
        plate_list = plate.get("plates", [])
        for p in plate_list if isinstance(plate_list, list) else []:
            conf = p.get("confidence", 0)
            if isinstance(conf, (int, float)) and conf < 0.4 and conf > 0:
                plate_num = p.get("plate_number", "未知")
                alerts.append(Alert(
                    alert_id=f"plate_low_conf_{uuid.uuid4().hex[:8]}",
                    level="low",
                    event_type="plate_low_confidence",
                    summary=f"车牌 {plate_num} 置信度偏低：{conf:.2f}",
                    reason=f"识别置信度 {conf:.2f}，建议人工复核",
                    suggestion="提高抽帧分辨率或重新拍摄",
                    dedup_key=f"plate_low_conf_{plate_num}",
                    source_module="plate",
                ))

        return alerts

    def _check_gesture_anomaly(self, snapshot: dict[str, Any]) -> list[Alert]:
        alerts: list[Alert] = []
        traffic = snapshot.get("traffic_gesture", {})
        owner = snapshot.get("owner_gesture", {})

        # 交警手势与车主手势冲突
        if isinstance(traffic, dict) and isinstance(owner, dict):
            t_gesture = str(traffic.get("gesture", "")).lower()
            o_gesture = str(owner.get("gesture", "")).lower()

            # 交警停止 + 车主有通行意图 → 冲突
            stop_keywords = {"stop", "stop_signal", "parking", "halt"}
            go_keywords = {"go_straight", "straight", "move_forward", "pass"}

            if t_gesture in stop_keywords and o_gesture in go_keywords:
                alerts.append(Alert(
                    alert_id=f"gesture_conflict_{uuid.uuid4().hex[:8]}",
                    level="critical",
                    event_type="gesture_conflict",
                    summary="交警停止手势与车主通行意图冲突！",
                    reason=f"交警手势={t_gesture}（停止），车主手势={o_gesture}（通行），存在严重安全冲突",
                    suggestion="优先遵守交警指挥，立即取消车辆自主通行决策",
                    dedup_key="gesture_conflict_stop_go",
                    source_module="fusion",
                ))

        return alerts

    def _check_system_health(self, snapshot: dict[str, Any]) -> list[Alert]:
        alerts: list[Alert] = []
        stats = snapshot.get("monitor_stats", {})
        if not isinstance(stats, dict):
            return alerts

        # 长时间无识别结果
        idle_seconds = stats.get("seconds_since_last_recognition")
        if idle_seconds is not None and idle_seconds > 120:
            alerts.append(Alert(
                alert_id=f"idle_warning_{uuid.uuid4().hex[:8]}",
                level="medium",
                event_type="system_idle",
                summary=f"系统已 {idle_seconds:.0f} 秒无新识别结果，请检查监控状态",
                reason="超过 120 秒无新识别记录，可能监控已停止或数据采集异常",
                suggestion="检查视频流是否正常、监控开关是否开启",
                dedup_key="system_idle",
                source_module="monitor",
            ))

        return alerts


# ---------------------------------------------------------------------------
# 告警级别分类器
# ---------------------------------------------------------------------------
class AlertLevelClassifier:
    """
    告警级别二次判定。

    在 AnomalyDetector 初步分级基础上，根据：
    - 上下文关联（同一时间窗口内是否存在多个告警）
    - 历史频率（短时间内同类型告警次数）
    进行级别升级或降级。
    """

    def __init__(self) -> None:
        self._recent_alerts: list[tuple[float, str]] = []  # (timestamp, level)
        self._window_seconds = 60.0

    def classify(self, alerts: list[Alert]) -> list[Alert]:
        """对告警列表进行级别调整"""
        now = time.time()
        self._recent_alerts.append((now, ""))
        self._recent_alerts = [
            (t, lv) for t, lv in self._recent_alerts
            if now - t <= self._window_seconds
        ]

        classified: list[Alert] = []
        for alert in alerts:
            adjusted = self._adjust_level(alert, now)
            self._recent_alerts.append((now, adjusted.level))
            classified.append(adjusted)

        return classified

    def _adjust_level(self, alert: Alert, now: float) -> Alert:
        """单条告警级别微调"""
        # 统计窗口内同 event_type 的数量
        same_type_count = sum(
            1 for t, _ in self._recent_alerts
            if now - t <= self._window_seconds
        )

        # 同一窗口内已经触发 ≥3 条告警 → 可能升级
        if same_type_count >= 3 and alert.level in ("medium", "low"):
            alert.level = "high"
            alert.reason += "（因短时间内同类告警频发，级别自动升级）"

        return alert


# ---------------------------------------------------------------------------
# LLM 摘要生成器（可选）
# ---------------------------------------------------------------------------
class LLMSummaryGenerator:
    """
    可选 LLM 告警摘要生成器。

    配置：
    - enabled: 是否启用（默认 False）
    - api_url: LLM API 地址
    - api_key: API 密钥
    - model: 模型名称
    - max_tokens: 最大输出 token 数
    """

    def __init__(
        self,
        enabled: bool = False,
        api_url: str = "",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        max_tokens: int = 256,
    ) -> None:
        self.enabled = enabled
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    def summarize(self, alert: Alert) -> str:
        """为告警生成 LLM 摘要"""
        if not self.enabled or not self.api_url:
            return ""

        prompt = (
            "你是一个智能交通系统的告警分析专家。请用 1-2 句话总结以下告警，"
            "并给出一个可行的处理建议。\n\n"
            f"告警级别：{alert.level}\n"
            f"事件类型：{alert.event_type}\n"
            f"摘要：{alert.summary}\n"
            f"原因：{alert.reason}\n"
            f"建议：{alert.suggestion}\n\n"
            "请用中文回复，格式：\n"
            "【摘要】...\n"
            "【建议】..."
        )

        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是智能交通告警分析助手。"},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.max_tokens,
                "temperature": 0.3,
            }

            resp = _requests.post(
                self.api_url,
                headers=headers,
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip()

        except Exception as exc:
            # LLM 不可用时静默回退，不阻塞告警流程
            return f"[LLM 摘要生成失败: {exc}]"

    def configure(
        self,
        enabled: bool | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """动态更新配置，返回当前配置状态"""
        if enabled is not None:
            self.enabled = enabled
        if api_url is not None:
            self.api_url = api_url
        if api_key is not None:
            self.api_key = api_key
        if model is not None:
            self.model = model

        return {
            "enabled": self.enabled,
            "api_url": self.api_url[:50] + "..." if len(self.api_url) > 50 else self.api_url,
            "model": self.model,
            "max_tokens": self.max_tokens,
        }


# ---------------------------------------------------------------------------
# 告警抑制器
# ---------------------------------------------------------------------------
class AlertSuppressor:
    """
    告警抑制器：同 dedup_key 在冷却期内不再重复触发。
    """

    def __init__(self, cooldown_seconds: float = 30.0) -> None:
        self._last_trigger: dict[str, float] = {}
        self._cooldown = cooldown_seconds

    def should_fire(self, dedup_key: str) -> bool:
        """是否应该触发该告警（冷却期外）"""
        now = time.time()
        last = self._last_trigger.get(dedup_key, 0)
        if now - last < self._cooldown:
            return False
        self._last_trigger[dedup_key] = now
        return True

    def reset(self, dedup_key: str) -> None:
        """手动重置某类告警的冷却"""
        self._last_trigger.pop(dedup_key, None)

    def reset_all(self) -> None:
        self._last_trigger.clear()

    def set_cooldown(self, seconds: float) -> None:
        self._cooldown = seconds


# ---------------------------------------------------------------------------
# AlertAgent 主类
# ---------------------------------------------------------------------------
class AlertAgent:
    """
    告警代理主入口。

    用法：
        agent = AlertAgent(llm_enabled=False)
        alerts = agent.evaluate(snapshot)       # 检测 + 分级 + 抑制
        stats  = agent.get_statistics(alerts)   # 统计
        agent.configure_llm(enabled=True, ...)  # 启用 LLM
    """

    def __init__(
        self,
        llm_enabled: bool = False,
        llm_api_url: str = "",
        llm_api_key: str = "",
        llm_model: str = "gpt-4o-mini",
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.detector = AnomalyDetector()
        self.classifier = AlertLevelClassifier()
        self.suppressor = AlertSuppressor(cooldown_seconds=cooldown_seconds)
        self.llm = LLMSummaryGenerator(
            enabled=llm_enabled,
            api_url=llm_api_url,
            api_key=llm_api_key,
            model=llm_model,
        )

        self._alert_history: list[Alert] = []
        self._max_history = 200

    def evaluate(self, snapshot: dict[str, Any]) -> list[Alert]:
        """
        核心方法：评估系统快照，返回触发的告警列表。

        流程：检测 → 抑制 → 分级 → LLM摘要
        """
        # 1. 异常检测
        raw_alerts = self.detector.detect(snapshot)

        # 2. 抑制过滤
        filtered: list[Alert] = []
        for alert in raw_alerts:
            if self.suppressor.should_fire(alert.dedup_key):
                filtered.append(alert)

        # 3. 级别调整
        classified = self.classifier.classify(filtered)

        # 4. LLM 摘要（可选）
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for alert in classified:
            alert.created_at = now_str
            if self.llm.enabled:
                alert.llm_summary = self.llm.summarize(alert)

        # 5. 记录历史
        self._alert_history.extend(classified)
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history:]

        return classified

    def get_statistics(self) -> AlertStats:
        """返回告警统计"""
        stats = AlertStats()
        total = len(self._alert_history)

        now = time.time()
        recent = [
            a for a in self._alert_history
            if now - self._parse_time(a.created_at) <= 300  # 5分钟窗口
        ]

        stats.total = total
        stats.recent = self._alert_history[-10:]

        for a in self._alert_history:
            setattr(stats, a.level, getattr(stats, a.level, 0) + 1)
            if a.status == "open":
                stats.open += 1
            elif a.status == "resolved":
                stats.resolved += 1
            stats.by_type[a.event_type] = stats.by_type.get(a.event_type, 0) + 1

        # 平均每分钟告警数（基于历史时间跨度）
        if self._alert_history and len(self._alert_history) >= 2:
            first = self._parse_time(self._alert_history[0].created_at)
            last = self._parse_time(self._alert_history[-1].created_at)
            span_minutes = max((last - first) / 60, 0.016)
            stats.avg_per_minute = round(total / span_minutes, 2)

        return stats

    def configure_llm(
        self,
        enabled: bool | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """动态配置 LLM 选项"""
        return self.llm.configure(
            enabled=enabled,
            api_url=api_url,
            api_key=api_key,
            model=model,
        )

    def to_dict(self, alert: Alert) -> dict[str, Any]:
        """将 Alert 转为可序列化字典"""
        return {
            "alert_id": alert.alert_id,
            "level": alert.level,
            "event_type": alert.event_type,
            "summary": alert.summary,
            "reason": alert.reason,
            "suggestion": alert.suggestion,
            "status": alert.status,
            "related_record_id": alert.related_record_id,
            "created_at": alert.created_at,
            "source_module": alert.source_module,
            "detail": alert.detail,
            "llm_summary": alert.llm_summary,
            "dedup_key": alert.dedup_key,
        }

    def from_alert_list(self, alerts: list[Alert]) -> list[dict[str, Any]]:
        return [self.to_dict(a) for a in alerts]

    def _parse_time(self, time_str: str) -> float:
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            return dt.timestamp()
        except (ValueError, TypeError):
            return time.time()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_alert_agent_instance: AlertAgent | None = None


def get_alert_agent() -> AlertAgent:
    global _alert_agent_instance
    if _alert_agent_instance is None:
        _alert_agent_instance = AlertAgent()
    return _alert_agent_instance


def reset_alert_agent() -> AlertAgent:
    global _alert_agent_instance
    _alert_agent_instance = AlertAgent()
    return _alert_agent_instance
