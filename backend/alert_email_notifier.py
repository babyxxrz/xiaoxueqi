"""
告警邮件通知模块

负责将告警事件转化为邮件通知，支持：
- 多收件人（告警所属用户 + 所有管理员）
- 邮件去重（同一告警+同一邮箱短时间内不重复发送）
- 失败重试（可配置最大重试次数与退避延迟）
- 异步队列（后台工作线程处理，不阻塞主流程）
- HTML 邮件模板（含告警级别、原因、建议等结构化信息）

配置通过 backend/.env 中的环境变量控制。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import html
import json
import os
import queue
import re
import sqlite3
import threading
import time

from auth import (
    is_smtp_configured,
    mask_email,
    send_html_email,
)


ALERT_EMAIL_ENABLED = (
    os.getenv("ALERT_EMAIL_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
ALERT_EMAIL_NOTIFY_USER = (
    os.getenv("ALERT_EMAIL_NOTIFY_USER", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
ALERT_EMAIL_NOTIFY_ADMINS = (
    os.getenv("ALERT_EMAIL_NOTIFY_ADMINS", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)

ALERT_EMAIL_MAX_RETRIES = max(
    1,
    int(os.getenv("ALERT_EMAIL_MAX_RETRIES", "3")),
)
ALERT_EMAIL_RETRY_DELAY_SECONDS = max(
    1,
    int(os.getenv("ALERT_EMAIL_RETRY_DELAY_SECONDS", "10")),
)
ALERT_EMAIL_DEDUP_WINDOW_SECONDS = max(
    0,
    int(os.getenv("ALERT_EMAIL_DEDUP_WINDOW_SECONDS", "300")),
)

ALERT_EMAIL_ADMIN_LEVELS = {
    item.strip().lower()
    for item in os.getenv(
        "ALERT_EMAIL_ADMIN_LEVELS",
        "critical,error,high,danger,emergency,severe",
    ).split(",")
    if item.strip()
}

ALERT_EMAIL_SYSTEM_KEYWORDS = {
    item.strip().lower()
    for item in os.getenv(
        "ALERT_EMAIL_SYSTEM_KEYWORDS",
        "system,service,stream,performance,latency,worker,video_source,monitor",
    ).split(",")
    if item.strip()
}

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NOTIFICATION_QUEUE: queue.Queue[int] = queue.Queue()
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_WORKER_DB_PATH: Path | None = None


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def future_text(seconds: int) -> str:
    return (
        datetime.now() + timedelta(seconds=max(0, int(seconds)))
    ).strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_alert_notification_db(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL,
                recipient_user_id INTEGER,
                recipient_email TEXT NOT NULL,
                recipient_role TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'email',
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                dedup_key TEXT NOT NULL UNIQUE,
                last_error TEXT,
                sent_at TEXT,
                next_retry_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alert_notifications_alert
            ON alert_notifications(alert_id, id DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alert_notifications_status
            ON alert_notifications(status, next_retry_at, id)
            """
        )
        conn.commit()


def _valid_recipient_email(email: str) -> bool:
    value = str(email or "").strip().lower()

    if not value or not _EMAIL_PATTERN.fullmatch(value):
        return False

    # 项目默认管理员可能使用 admin@xiaoxueqi.local，
    # 这种本地域名无法通过公网 SMTP 投递。
    if value.endswith(".local"):
        return False

    return True


def _normalized_level(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "严重": "critical",
        "高": "high",
        "警告": "warning",
        "提示": "info",
    }
    return aliases.get(text, text or "warning")


def _should_notify_admins(
    level: str,
    event_type: str,
    owner_user_id: int | None,
) -> bool:
    """
    当前项目策略：所有告警都通知管理员。

    参数保留是为了兼容旧调用，但不再按级别或事件类型筛选。
    """
    return True


def _load_alert_context(
    db_path: Path | str,
    alert_id: int,
) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                a.id,
                a.user_id,
                a.level,
                a.event_type,
                a.summary,
                a.reason,
                a.suggestion,
                a.status,
                a.related_record_id,
                a.created_at,
                u.username,
                u.email AS user_email,
                r.task_type AS related_task_type,
                r.input_type AS related_input_type,
                r.original_filename AS related_filename,
                r.result_json AS related_result_json
            FROM alert_events a
            LEFT JOIN users u ON u.id = a.user_id
            LEFT JOIN recognition_records r
                ON r.id = a.related_record_id
            WHERE a.id = ?
            """,
            (int(alert_id),),
        ).fetchone()

    if row is None:
        return None

    result: dict = {}
    raw_result = row["related_result_json"] or ""

    if raw_result:
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            result = {"raw": raw_result}

    return {
        "id": int(row["id"]),
        "user_id": (
            int(row["user_id"])
            if row["user_id"] is not None
            else None
        ),
        "level": row["level"],
        "event_type": row["event_type"],
        "summary": row["summary"],
        "reason": row["reason"] or "",
        "suggestion": row["suggestion"] or "",
        "status": row["status"],
        "related_record_id": row["related_record_id"],
        "created_at": row["created_at"],
        "username": row["username"] or "",
        "user_email": row["user_email"] or "",
        "related_task_type": row["related_task_type"] or "",
        "related_input_type": row["related_input_type"] or "",
        "related_filename": row["related_filename"] or "",
        "related_result": result,
    }


def _recognition_summary(context: dict) -> str:
    result = context.get("related_result") or {}
    task_type = context.get("related_task_type") or ""

    if task_type == "plate":
        plates = result.get("plates") or []
        values = []
        for item in plates:
            if not isinstance(item, dict):
                continue
            number = str(item.get("plate_number") or item.get("plate") or item.get("text") or "").strip()
            if not number:
                continue
            color = str(item.get("plate_color") or item.get("color") or "未知颜色").strip()
            values.append(f"{number}（{color}）")
        return "、".join(values[:5]) or "未识别到有效车牌"

    if task_type == "traffic_gesture":
        gesture = (
            result.get("gesture_name")
            or result.get("gesture")
            or "未知交警手势"
        )
        command = result.get("traffic_command") or ""
        return f"{gesture}；{command}" if command else str(gesture)

    if task_type == "owner_gesture":
        gesture = (
            result.get("gesture_name")
            or result.get("gesture")
            or "未知车主手势"
        )
        action = (
            result.get("description")
            or result.get("action")
            or ""
        )
        return f"{gesture}；{action}" if action else str(gesture)

    if task_type == "all":
        return (
            result.get("summary")
            or result.get("suggestion")
            or "多任务融合识别结果"
        )

    return (
        result.get("summary")
        or result.get("message")
        or "无关联识别结果"
    )


def _email_subject(context: dict) -> str:
    level = str(context.get("level") or "warning").upper()
    summary = str(context.get("summary") or "系统告警").strip()
    return f"【{level}】智能交通系统告警：{summary}"


def _build_email_html(
    context: dict,
    recipient_role: str,
) -> str:
    level = html.escape(str(context.get("level") or "-"))
    event_type = html.escape(str(context.get("event_type") or "-"))
    summary = html.escape(str(context.get("summary") or "-"))
    reason = html.escape(str(context.get("reason") or "-"))
    suggestion = html.escape(str(context.get("suggestion") or "-"))
    created_at = html.escape(str(context.get("created_at") or "-"))
    username = html.escape(
        str(context.get("username") or "系统任务")
    )
    related_record_id = html.escape(
        str(context.get("related_record_id") or "-")
    )
    related_task = html.escape(
        str(context.get("related_task_type") or "-")
    )
    related_filename = html.escape(
        str(context.get("related_filename") or "-")
    )
    recognition_summary = html.escape(_recognition_summary(context))
    recipient_label = "管理员" if recipient_role == "admin" else "用户"

    return f"""
<div style="font-family:Arial,'Microsoft YaHei',sans-serif;max-width:680px;margin:0 auto;padding:24px;background:#f8fafc;color:#172033;">
  <div style="padding:22px;border-radius:14px;background:#0f172a;color:#fff;">
    <div style="font-size:12px;letter-spacing:.14em;color:#67e8f9;">INTELLIGENT TRAFFIC ALERT</div>
    <h2 style="margin:8px 0 0;font-size:24px;">智能交通识别系统告警通知</h2>
  </div>

  <div style="margin-top:16px;padding:20px;border:1px solid #dbe4ef;border-radius:14px;background:#fff;">
    <p style="margin-top:0;">您好，{recipient_label}。</p>
    <p>系统检测到一条需要关注的告警，详情如下：</p>

    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;width:130px;">告警级别</td><td style="padding:9px;border-bottom:1px solid #edf2f7;font-weight:bold;">{level}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">告警类型</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">{event_type}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">告警摘要</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">{summary}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">发生时间</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">{created_at}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">关联用户</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">{username}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">关联记录</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">#{related_record_id} · {related_task}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">输入文件</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">{related_filename}</td></tr>
      <tr><td style="padding:9px;border-bottom:1px solid #edf2f7;color:#64748b;">识别结果</td><td style="padding:9px;border-bottom:1px solid #edf2f7;">{recognition_summary}</td></tr>
    </table>

    <div style="margin-top:16px;padding:14px;border-radius:10px;background:#fff7ed;">
      <strong>告警原因</strong>
      <p style="margin:7px 0 0;line-height:1.7;">{reason}</p>
    </div>

    <div style="margin-top:12px;padding:14px;border-radius:10px;background:#ecfeff;">
      <strong>处理建议</strong>
      <p style="margin:7px 0 0;line-height:1.7;">{suggestion}</p>
    </div>
  </div>

  <p style="color:#94a3b8;font-size:12px;text-align:center;margin-top:16px;">
    此邮件由智能交通识别系统自动发送，请勿直接回复。
  </p>
</div>
"""


def _recipient_rows(
    db_path: Path | str,
    context: dict,
) -> list[dict]:
    """
    每条告警的收件人：

    1. 告警有 user_id 时，发送给告警所属用户。
    2. 无论级别和类型，始终发送给所有管理员。
    3. 同一邮箱只保留一次，避免用户本身也是管理员时重复发送。
    4. 默认的 .local 管理员占位邮箱不创建发送任务。
    """
    recipients: list[dict] = []

    with connect(db_path) as conn:
        if context.get("user_id") is not None:
            owner = conn.execute(
                """
                SELECT id, username, email, role
                FROM users
                WHERE id = ?
                """,
                (int(context["user_id"]),),
            ).fetchone()

            if owner is not None:
                recipients.append({
                    "user_id": int(owner["id"]),
                    "username": owner["username"],
                    "email": owner["email"],
                    "role": owner["role"],
                    "recipient_kind": "owner",
                })

        admin_rows = conn.execute(
            """
            SELECT id, username, email, role
            FROM users
            WHERE role = 'admin'
            ORDER BY id ASC
            """
        ).fetchall()

        for row in admin_rows:
            recipients.append({
                "user_id": int(row["id"]),
                "username": row["username"],
                "email": row["email"],
                "role": row["role"],
                "recipient_kind": "admin",
            })

    result: list[dict] = []
    seen: set[str] = set()

    for item in recipients:
        normalized = str(item.get("email") or "").strip().lower()

        if not normalized or normalized in seen:
            continue

        # admin@xiaoxueqi.local 是项目占位邮箱，无法通过公网 SMTP 投递。
        # 对管理员占位邮箱直接忽略，避免每条告警都显示“部分发送”。
        if (
            item.get("recipient_kind") == "admin"
            and not _valid_recipient_email(normalized)
        ):
            print(
                "[ALERT EMAIL] skip invalid admin email:"
                f" {mask_email(normalized)}"
            )
            continue

        seen.add(normalized)
        copied = dict(item)
        copied["email"] = normalized
        result.append(copied)

    return result


def _recent_duplicate_exists(
    conn: sqlite3.Connection,
    *,
    alert_id: int,
    recipient_email: str,
    event_type: str,
    user_id: int | None,
) -> bool:
    if ALERT_EMAIL_DEDUP_WINDOW_SECONDS <= 0:
        return False

    threshold = (
        datetime.now()
        - timedelta(seconds=ALERT_EMAIL_DEDUP_WINDOW_SECONDS)
    ).strftime("%Y-%m-%d %H:%M:%S")

    row = conn.execute(
        """
        SELECT n.id
        FROM alert_notifications n
        INNER JOIN alert_events a ON a.id = n.alert_id
        WHERE n.alert_id <> ?
          AND LOWER(n.recipient_email) = LOWER(?)
          AND n.status = 'sent'
          AND a.event_type = ?
          AND (
                (a.user_id IS NULL AND ? IS NULL)
                OR a.user_id = ?
          )
          AND n.sent_at >= ?
        ORDER BY n.id DESC
        LIMIT 1
        """,
        (
            int(alert_id),
            recipient_email,
            event_type,
            user_id,
            user_id,
            threshold,
        ),
    ).fetchone()

    return row is not None


def schedule_alert_notifications(
    db_path: Path | str,
    alert_id: int,
) -> list[int]:
    """
    为一条告警创建邮件通知任务。

    去重策略：
    - 只对同一 alert_id + 同一收件邮箱去重。
    - 不再跨告警抑制；每一条新告警都会创建并发送邮件。
    """
    if not ALERT_EMAIL_ENABLED:
        return []

    init_alert_notification_db(db_path)
    context = _load_alert_context(db_path, alert_id)

    if context is None:
        return []

    recipients = _recipient_rows(db_path, context)
    created_job_ids: list[int] = []

    with connect(db_path) as conn:
        for recipient in recipients:
            email = recipient["email"]
            dedup_key = f"alert:{int(alert_id)}:email:{email}"
            valid_email = _valid_recipient_email(email)

            status = "pending" if valid_email else "skipped"
            error_message = (
                ""
                if valid_email
                else "邮箱地址无效或使用了不可投递的 .local 域名"
            )

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO alert_notifications (
                    alert_id,
                    recipient_user_id,
                    recipient_email,
                    recipient_role,
                    channel,
                    status,
                    attempt_count,
                    max_attempts,
                    dedup_key,
                    last_error,
                    sent_at,
                    next_retry_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'email', ?, 0, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    int(alert_id),
                    recipient.get("user_id"),
                    email,
                    recipient.get("role") or "user",
                    status,
                    ALERT_EMAIL_MAX_RETRIES,
                    dedup_key,
                    error_message,
                    now_text(),
                    now_text(),
                ),
            )

            if cursor.rowcount:
                job_id = int(cursor.lastrowid)
                created_job_ids.append(job_id)

                if status == "pending":
                    _enqueue_job(job_id)

        conn.commit()

    return created_job_ids


def _enqueue_job(job_id: int) -> None:
    try:
        _NOTIFICATION_QUEUE.put_nowait(int(job_id))
    except Exception:
        pass


def _process_job(db_path: Path | str, job_id: int) -> None:
    with connect(db_path) as conn:
        job = conn.execute(
            """
            SELECT *
            FROM alert_notifications
            WHERE id = ?
            """,
            (int(job_id),),
        ).fetchone()

        if job is None:
            return

        if job["status"] in {"sent", "skipped"}:
            return

        attempt_count = int(job["attempt_count"] or 0) + 1
        max_attempts = int(job["max_attempts"] or ALERT_EMAIL_MAX_RETRIES)

        conn.execute(
            """
            UPDATE alert_notifications
            SET
                status = 'processing',
                attempt_count = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                attempt_count,
                now_text(),
                int(job_id),
            ),
        )
        conn.commit()

    context = _load_alert_context(db_path, int(job["alert_id"]))

    if context is None:
        success = False
        error_message = "关联告警不存在"
    elif not ALERT_EMAIL_ENABLED:
        success = False
        error_message = "告警邮件通知已关闭"
    elif not is_smtp_configured():
        success = False
        error_message = "SMTP 未配置"
    else:
        success, error_message = send_html_email(
            to_emails=job["recipient_email"],
            subject=_email_subject(context),
            html_body=_build_email_html(
                context,
                str(job["recipient_role"] or "user"),
            ),
        )

    with connect(db_path) as conn:
        if success:
            conn.execute(
                """
                UPDATE alert_notifications
                SET
                    status = 'sent',
                    last_error = '',
                    sent_at = ?,
                    next_retry_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now_text(),
                    now_text(),
                    int(job_id),
                ),
            )
            print(
                "[ALERT EMAIL] sent:"
                f" alert_id={job['alert_id']},"
                f" recipient={mask_email(job['recipient_email'])}"
            )
        else:
            if attempt_count < max_attempts:
                status = "retrying"
                next_retry_at = future_text(
                    ALERT_EMAIL_RETRY_DELAY_SECONDS
                )
            else:
                status = "failed"
                next_retry_at = None

            conn.execute(
                """
                UPDATE alert_notifications
                SET
                    status = ?,
                    last_error = ?,
                    next_retry_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    str(error_message or "发送失败")[:1000],
                    next_retry_at,
                    now_text(),
                    int(job_id),
                ),
            )

        conn.commit()

    if not success and attempt_count < max_attempts:
        timer = threading.Timer(
            ALERT_EMAIL_RETRY_DELAY_SECONDS,
            _enqueue_job,
            args=(int(job_id),),
        )
        timer.daemon = True
        timer.start()


def _worker_loop() -> None:
    while True:
        job_id = _NOTIFICATION_QUEUE.get()

        try:
            if _WORKER_DB_PATH is not None:
                _process_job(_WORKER_DB_PATH, int(job_id))
        except Exception as error:
            print(
                f"[ALERT EMAIL WORKER ERROR] job_id={job_id}, "
                f"{type(error).__name__}: {error}"
            )
        finally:
            _NOTIFICATION_QUEUE.task_done()


def start_alert_notification_worker(
    db_path: Path | str,
) -> None:
    global _WORKER_STARTED, _WORKER_DB_PATH

    init_alert_notification_db(db_path)
    _WORKER_DB_PATH = Path(db_path)

    with _WORKER_LOCK:
        if not _WORKER_STARTED:
            thread = threading.Thread(
                target=_worker_loop,
                name="alert-email-worker",
                daemon=True,
            )
            thread.start()
            _WORKER_STARTED = True

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM alert_notifications
            WHERE status IN ('pending', 'processing', 'retrying')
              AND attempt_count < max_attempts
            ORDER BY id ASC
            """
        ).fetchall()

    for row in rows:
        _enqueue_job(int(row["id"]))


def get_alert_notification_summary(
    db_path: Path | str,
    alert_id: int,
) -> dict:
    init_alert_notification_db(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END)
                    AS sent_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                    AS failed_count,
                SUM(CASE
                    WHEN status IN ('pending', 'processing', 'retrying')
                    THEN 1 ELSE 0 END)
                    AS pending_count,
                SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END)
                    AS skipped_count
            FROM alert_notifications
            WHERE alert_id = ?
            """,
            (int(alert_id),),
        ).fetchone()

    total = int(row["total_count"] or 0)
    sent = int(row["sent_count"] or 0)
    failed = int(row["failed_count"] or 0)
    pending = int(row["pending_count"] or 0)
    skipped = int(row["skipped_count"] or 0)

    if total == 0:
        status = "未创建"
    elif pending > 0:
        status = "发送中"
    elif sent == total:
        status = "已发送"
    elif sent > 0 and (failed > 0 or skipped > 0):
        status = "部分发送"
    elif failed > 0:
        status = "发送失败"
    elif skipped == total:
        status = "已跳过"
    else:
        status = "待发送"

    return {
        "status": status,
        "total_count": total,
        "sent_count": sent,
        "failed_count": failed,
        "pending_count": pending,
        "skipped_count": skipped,
    }


def list_alert_notifications(
    db_path: Path | str,
    *,
    alert_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    init_alert_notification_db(db_path)
    conditions = []
    params: list = []

    if alert_id is not None:
        conditions.append("n.alert_id = ?")
        params.append(int(alert_id))

    where_sql = (
        f"WHERE {' AND '.join(conditions)}"
        if conditions
        else ""
    )

    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                n.id,
                n.alert_id,
                n.recipient_user_id,
                n.recipient_email,
                n.recipient_role,
                n.channel,
                n.status,
                n.attempt_count,
                n.max_attempts,
                n.last_error,
                n.sent_at,
                n.next_retry_at,
                n.created_at,
                n.updated_at,
                a.level,
                a.event_type,
                a.summary
            FROM alert_notifications n
            LEFT JOIN alert_events a ON a.id = n.alert_id
            {where_sql}
            ORDER BY n.id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "alert_id": int(row["alert_id"]),
            "recipient_user_id": row["recipient_user_id"],
            "recipient_email_masked": mask_email(
                row["recipient_email"]
            ),
            "recipient_role": row["recipient_role"],
            "channel": row["channel"],
            "status": row["status"],
            "attempt_count": int(row["attempt_count"] or 0),
            "max_attempts": int(row["max_attempts"] or 0),
            "last_error": row["last_error"] or "",
            "sent_at": row["sent_at"],
            "next_retry_at": row["next_retry_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "level": row["level"],
            "event_type": row["event_type"],
            "summary": row["summary"],
        }
        for row in rows
    ]


def retry_alert_notifications(
    db_path: Path | str,
    alert_id: int,
) -> int:
    init_alert_notification_db(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM alert_notifications
            WHERE alert_id = ?
              AND status IN ('failed', 'retrying')
            """,
            (int(alert_id),),
        ).fetchall()

        for row in rows:
            conn.execute(
                """
                UPDATE alert_notifications
                SET
                    status = 'pending',
                    attempt_count = 0,
                    last_error = '',
                    sent_at = NULL,
                    next_retry_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now_text(),
                    int(row["id"]),
                ),
            )

        conn.commit()

    for row in rows:
        _enqueue_job(int(row["id"]))

    return len(rows)


def retry_all_failed_notifications(
    db_path: Path | str,
) -> int:
    init_alert_notification_db(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM alert_notifications
            WHERE status = 'failed'
            ORDER BY id ASC
            """
        ).fetchall()

        for row in rows:
            conn.execute(
                """
                UPDATE alert_notifications
                SET
                    status = 'pending',
                    attempt_count = 0,
                    last_error = '',
                    sent_at = NULL,
                    next_retry_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now_text(),
                    int(row["id"]),
                ),
            )

        conn.commit()

    for row in rows:
        _enqueue_job(int(row["id"]))

    return len(rows)


def alert_email_config_summary() -> dict:
    return {
        "enabled": ALERT_EMAIL_ENABLED,
        "smtp_configured": is_smtp_configured(),
        "notification_policy": (
            "every_alert_to_owner_and_all_valid_admins"
        ),
        "notify_user": True,
        "notify_admins": True,
        "cross_alert_dedup_enabled": False,
        "same_alert_same_email_dedup": True,
        "max_retries": ALERT_EMAIL_MAX_RETRIES,
        "retry_delay_seconds": ALERT_EMAIL_RETRY_DELAY_SECONDS,
    }
