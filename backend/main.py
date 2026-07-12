from pathlib import Path
from collections import Counter
from uuid import uuid4
from datetime import datetime
from contextvars import ContextVar
import asyncio
import json
import queue
import shutil
import sqlite3
import threading
import time

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from algorithm.plate_recognizer import (
    draw_plate_annotations,
    recognize_plate_frame,
    recognize_plate_real,
)
from algorithm.plate_video_aggregator import aggregate_plate_frame_results_v2
from algorithm.owner_gesture_recognizer import recognize_owner_gesture_image
from algorithm.traffic_gesture_recognizer import (
    recognize_traffic_gesture_frame,
    recognize_traffic_gesture_image,
    aggregate_traffic_gesture_sequence,
)
from algorithm.alert_agent import (
    AlertAgent,
    Alert,
    AlertStats,
    AnomalyDetector,
    AlertLevelClassifier,
    AlertSuppressor,
    LLMSummaryGenerator,
    get_alert_agent,
    reset_alert_agent,
)
from auth import (
    auth_router,
    init_auth,
    get_current_user,
    require_admin,
    decode_access_token,
    get_user_by_id,
)
from alert_email_notifier import (
    alert_email_config_summary,
    get_alert_notification_summary,
    init_alert_notification_db,
    list_alert_notifications,
    retry_alert_notifications,
    retry_all_failed_notifications,
    schedule_alert_notifications,
    start_alert_notification_worker,
)

from stream_manager import (
    SANDBOX_CAMERAS,
    SANDBOX_RTSP_BASE,
    build_ffmpeg_cmd,
    get_all_streams_status,
    get_stream_status,
    start_stream,
    stop_all_streams,
    stop_stream,
)

# --- AlertAgent 全局实例 ---
_ALERT_AGENT = get_alert_agent()
_ALERT_SSE_QUEUES: list[queue.Queue] = []
_ALERT_SSE_LOCK = threading.Lock()


app = FastAPI(title="智能车载视觉感知与告警系统")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


_REQUEST_USER_ID: ContextVar[int | None] = ContextVar(
    "request_user_id",
    default=None,
)
_REQUEST_USER: ContextVar[dict | None] = ContextVar(
    "request_user",
    default=None,
)


@app.middleware("http")
async def bind_request_user_context(request: Request, call_next):
    """
    从 Bearer access token 中读取当前用户，并写入 ContextVar。

    现有识别接口无需逐个重写参数，所有记录、告警和操作日志写入函数
    都可以自动绑定本次请求的 user_id。未登录的系统任务保留为 NULL，
    只在管理员全局视图中展示。
    """
    current_user = None
    authorization = request.headers.get("Authorization", "")

    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            try:
                payload = decode_access_token(token)
                current_user = get_user_by_id(int(payload["sub"]))
            except Exception:
                current_user = None

    user_token = _REQUEST_USER.set(current_user)
    user_id_token = _REQUEST_USER_ID.set(
        int(current_user["id"]) if current_user else None
    )

    try:
        return await call_next(request)
    finally:
        _REQUEST_USER.reset(user_token)
        _REQUEST_USER_ID.reset(user_id_token)


def current_request_user_id(explicit_user_id: int | None = None) -> int | None:
    if explicit_user_id is not None:
        return int(explicit_user_id)
    return _REQUEST_USER_ID.get()


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")


RTSP_SOURCES = [
    {"id": "live1", "name": "桥面", "url": "rtsp://10.126.59.120:8554/live/live1"},
    {"id": "live2", "name": "停车场出口", "url": "rtsp://10.126.59.120:8554/live/live2"},
    {"id": "live3", "name": "行人检测", "url": "rtsp://10.126.59.120:8554/live/live3"},
    {"id": "live4", "name": "消防车识别", "url": "rtsp://10.126.59.120:8554/live/live4"},
    {"id": "live5", "name": "桥出口", "url": "rtsp://10.126.59.120:8554/live/live5"},
    {"id": "live6", "name": "桥入口", "url": "rtsp://10.126.59.120:8554/live/live6"},
    {"id": "live7", "name": "道路2", "url": "rtsp://10.126.59.120:8554/live/live7"},
    {"id": "live8", "name": "隧道事故识别", "url": "rtsp://10.126.59.120:8554/live/live8"},
    {"id": "live9", "name": "隧道车辆数量", "url": "rtsp://10.126.59.120:8554/live/live9"},
    {"id": "live10", "name": "道路3", "url": "rtsp://10.126.59.120:8554/live/live10"},
    {"id": "live11", "name": "停车场入口", "url": "rtsp://10.126.59.120:8554/live/live11"},
    {"id": "live12", "name": "道路1", "url": "rtsp://10.126.59.120:8554/live/live12"},
]


class GestureRequest(BaseModel):
    gesture: str


class RtspRecognizeRequest(BaseModel):
    source_id: str
    use_mock_frame: bool = True


class StreamRecognizeRequest(BaseModel):
    source_id: str
    task_type: str = "plate"
    frame_count: int = 50
    sample_interval: int = 5
    use_mock_frame: bool = False
    # 视频批量和融合监控可开启快速模式，跳过标注图绘制与磁盘写入。
    fast_mode: bool = False
    # 保留旧字段，兼容现有前端。
    custom_rtsp_url: str | None = None
    # 新字段同时支持 RTSP、HTTP 视频、本地视频路径和摄像头编号。
    custom_source_url: str | None = None


class MonitorStartRequest(BaseModel):
    task_type: str = "all"
    interval_seconds: int = 30
    frame_count: int = 20
    sample_interval: int = 5
    use_mock_frame: bool = False
    source_ids: list[str] | None = None


MONITOR_LOCK = threading.RLock()
MONITOR_STOP_EVENT = threading.Event()
MONITOR_THREAD: threading.Thread | None = None
MONITOR_STATE = {
    "running": False,
    "task_type": "all",
    "interval_seconds": 30,
    "frame_count": 20,
    "sample_interval": 5,
    "use_mock_frame": False,
    "source_ids": [],
    "started_at": "",
    "stopped_at": "",
    "last_round_at": "",
    "next_round_after": "",
    "rounds_completed": 0,
    "total_records_created": 0,
    "total_alerts_created": 0,
    "source_status": {},
    "recent_events": [],
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column_exists(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
):
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]

    if column_name not in columns:
        cursor.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
        conn.commit()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS recognition_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_type TEXT NOT NULL,
            input_type TEXT NOT NULL,
            original_filename TEXT,
            saved_filename TEXT,
            image_url TEXT,
            output_image_url TEXT,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            reason TEXT,
            suggestion TEXT,
            status TEXT NOT NULL,
            related_record_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            system_awake INTEGER NOT NULL,
            current_function TEXT NOT NULL,
            volume INTEGER NOT NULL,
            temperature INTEGER NOT NULL,
            phone_status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO vehicle_state (
            id,
            system_awake,
            current_function,
            volume,
            temperature,
            phone_status,
            updated_at
        )
        VALUES (
            1,
            0,
            'home',
            50,
            24,
            '空闲',
            ?
        )
        """,
        (now_text(),),
    )
    conn.commit()

    # 兼容已有 SQLite 数据库，启动时自动补列，不删除旧数据。
    ensure_column_exists(
        conn=conn,
        table_name="recognition_records",
        column_name="output_image_url",
        column_definition="TEXT",
    )
    ensure_column_exists(
        conn=conn,
        table_name="recognition_records",
        column_name="user_id",
        column_definition="INTEGER",
    )
    ensure_column_exists(
        conn=conn,
        table_name="alert_events",
        column_name="user_id",
        column_definition="INTEGER",
    )
    ensure_column_exists(
        conn=conn,
        table_name="operation_logs",
        column_name="user_id",
        column_definition="INTEGER",
    )
    conn.close()

    # 初始化认证表，确保 users 表和默认管理员已经存在。
    init_auth()
    init_alert_notification_db(DB_PATH)

    conn = get_db_connection()
    cursor = conn.cursor()

    # 旧版本没有 user_id。为了保留既有记录，将历史无归属数据统一归到
    # 最早创建的管理员账号；新记录则自动绑定发起请求的当前用户。
    admin_row = cursor.execute(
        """
        SELECT id
        FROM users
        WHERE role = 'admin'
        ORDER BY id ASC
        LIMIT 1
        """
    ).fetchone()
    legacy_owner_id = int(admin_row["id"]) if admin_row else None

    if legacy_owner_id is not None:
        for table_name in (
            "recognition_records",
            "alert_events",
            "operation_logs",
        ):
            cursor.execute(
                f"UPDATE {table_name} SET user_id = ? WHERE user_id IS NULL",
                (legacy_owner_id,),
            )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_records_user_created
        ON recognition_records(user_id, created_at DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alert_events_user_created
        ON alert_events(user_id, created_at DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_operation_logs_user_created
        ON operation_logs(user_id, created_at DESC)
        """
    )
    conn.commit()
    conn.close()

def insert_operation_log(
    action: str,
    detail: dict | str | None = None,
    user_id: int | None = None,
) -> int:
    if isinstance(detail, dict):
        detail_text = json.dumps(detail, ensure_ascii=False)
    elif detail is None:
        detail_text = ""
    else:
        detail_text = str(detail)

    effective_user_id = current_request_user_id(user_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO operation_logs (
            user_id,
            action,
            detail,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            effective_user_id,
            action,
            detail_text,
            now_text(),
        ),
    )
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return int(log_id)

def insert_alert_event(
    level: str,
    event_type: str,
    summary: str,
    reason: str,
    suggestion: str,
    related_record_id: int | None = None,
    status: str = "未处理",
    user_id: int | None = None,
) -> int:
    effective_user_id = current_request_user_id(user_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO alert_events (
            user_id,
            level,
            event_type,
            summary,
            reason,
            suggestion,
            status,
            related_record_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            effective_user_id,
            level,
            event_type,
            summary,
            reason,
            suggestion,
            status,
            related_record_id,
            now_text(),
        ),
    )
    conn.commit()
    alert_id = cursor.lastrowid
    conn.close()

    try:
        schedule_alert_notifications(
            DB_PATH,
            int(alert_id),
        )
    except Exception as error:
        print(
            "[ALERT EMAIL SCHEDULE ERROR] "
            f"alert_id={alert_id}, "
            f"{type(error).__name__}: {error}"
        )

    return int(alert_id)

def insert_recognition_record(
    task_type: str,
    input_type: str,
    original_filename: str,
    saved_filename: str,
    image_url: str,
    output_image_url: str,
    result: dict,
    user_id: int | None = None,
) -> int:
    effective_user_id = current_request_user_id(user_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO recognition_records (
            user_id,
            task_type,
            input_type,
            original_filename,
            saved_filename,
            image_url,
            output_image_url,
            result_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            effective_user_id,
            task_type,
            input_type,
            original_filename,
            saved_filename,
            image_url,
            output_image_url,
            json.dumps(result, ensure_ascii=False),
            now_text(),
        ),
    )
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return int(record_id)

def create_annotated_plate_image(
    input_path: Path,
    output_path: Path,
    confidence: float = 0.92,
) -> dict:
    """
    使用 HyperLPR3 完成一帧中的多车牌检测、OCR、颜色推断和标注。

    confidence 仅用于模拟低置信度告警：当传入值小于 0.6 时，
    强制覆盖模型置信度；正常识别始终采用模型真实置信度。
    """
    try:
        force_confidence = confidence if confidence < 0.6 else None
        return recognize_plate_real(
            input_path=input_path,
            output_path=output_path,
            force_confidence=force_confidence,
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"真实车牌识别失败：{error}",
        ) from error


def create_mock_rtsp_frame(source_name: str, output_path: Path):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[:] = (35, 35, 35)

    cv2.rectangle(image, (0, 420), (1280, 720), (55, 55, 55), -1)
    cv2.line(image, (0, 570), (1280, 570), (255, 255, 255), 5)
    cv2.rectangle(image, (480, 430), (800, 560), (20, 20, 160), -1)
    cv2.rectangle(image, (560, 535), (720, 565), (255, 255, 255), -1)

    cv2.putText(
        image,
        f"RTSP Source: {source_name}",
        (40, 80),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 255, 255),
        3,
        cv2.LINE_AA,
    )

    cv2.putText(
        image,
        "Mock frame for demo",
        (40, 130),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (200, 200, 200),
        2,
        cv2.LINE_AA,
    )

    success = cv2.imwrite(str(output_path), image)

    if not success:
        raise HTTPException(status_code=500, detail="模拟视频帧保存失败")


def _source_kind(source_value: str | int) -> str:
    if isinstance(source_value, int):
        return "camera"

    text = str(source_value or "").strip()
    if text.isdigit():
        return "camera"
    if Path(text).expanduser().exists():
        return "local_video"
    if text.lower().startswith("rtsp://"):
        return "rtsp"
    if text.lower().startswith(("http://", "https://")):
        return "http_video"
    return "video_source"


def open_video_capture(source: dict):
    """统一打开 RTSP、HTTP 视频、本地视频文件或电脑摄像头。"""
    source_value = source.get("url", "")
    kind = source.get("kind") or _source_kind(source_value)

    if kind == "camera":
        camera_index = int(source_value)
        cap = cv2.VideoCapture(camera_index)
    elif kind == "local_video":
        cap = cv2.VideoCapture(str(Path(str(source_value)).expanduser()))
    else:
        cap = cv2.VideoCapture(str(source_value), cv2.CAP_FFMPEG)

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def stream_input_type(source: dict, use_mock_frame: bool) -> str:
    if use_mock_frame:
        return "mock_stream"

    kind = source.get("kind") or _source_kind(source.get("url", ""))
    return {
        "camera": "camera_stream",
        "local_video": "local_video",
        "http_video": "http_video",
        "rtsp": "rtsp_stream",
    }.get(kind, "video_stream")


def capture_rtsp_frame(source: dict, output_path: Path):
    cap = open_video_capture(source)

    if not cap.isOpened():
        raise HTTPException(
            status_code=400,
            detail=f"无法打开视频源：{source['name']}。请检查地址、文件路径或摄像头编号。",
        )

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise HTTPException(
            status_code=400,
            detail=f"读取视频帧失败：{source['name']}",
        )

    if not cv2.imwrite(str(output_path), frame):
        raise HTTPException(status_code=500, detail="视频帧保存失败")




def clamp_int(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(int(value), max_value))


def get_stream_source(source_id: str, custom_source_url: str | None = None) -> dict:
    if custom_source_url:
        kind = _source_kind(custom_source_url)
        kind_name = {
            "camera": "自定义摄像头",
            "local_video": "本地视频文件",
            "http_video": "HTTP 视频源",
            "rtsp": "自定义 RTSP 视频源",
        }.get(kind, "自定义视频源")

        return {
            "id": "custom",
            "name": kind_name,
            "url": custom_source_url,
            "kind": kind,
        }

    source = next((item for item in RTSP_SOURCES if item["id"] == source_id), None)

    if source is None:
        raise HTTPException(status_code=404, detail="未找到指定视频源")

    result = dict(source)
    result["kind"] = _source_kind(result.get("url", ""))
    return result


def save_frame_image(frame, filename_prefix: str) -> dict:
    saved_filename = f"{filename_prefix}_{uuid4().hex}.jpg"
    saved_path = UPLOAD_DIR / saved_filename

    success = cv2.imwrite(str(saved_path), frame)

    if not success:
        raise HTTPException(status_code=500, detail="视频帧保存失败")

    return {
        "saved_filename": saved_filename,
        "saved_path": saved_path,
        "image_url": f"/uploads/{saved_filename}",
    }


def capture_stream_sampled_frames(
    source: dict,
    frame_count: int,
    sample_interval: int,
    use_mock_frame: bool,
) -> tuple[list[dict], int]:
    """
    从 RTSP、本地视频、HTTP 视频或摄像头连续读取 frame_count 帧，
    每隔 sample_interval 帧保存一张抽样帧。
    """
    frame_count = clamp_int(frame_count, 5, 300)
    sample_interval = clamp_int(sample_interval, 1, 60)
    sampled_frames: list[dict] = []

    if use_mock_frame:
        mock_count = max(1, min(10, frame_count // sample_interval))

        for index in range(mock_count):
            saved_filename = f"stream_mock_{source['id']}_{index:03d}_{uuid4().hex}.jpg"
            saved_path = UPLOAD_DIR / saved_filename
            create_mock_rtsp_frame(source["name"], saved_path)
            sampled_frames.append({
                "frame_index": index * sample_interval,
                "saved_filename": saved_filename,
                "saved_path": saved_path,
                "image_url": f"/uploads/{saved_filename}",
                "captured_at": now_text(),
            })

        return sampled_frames, frame_count

    cap = open_video_capture(source)

    if not cap.isOpened():
        raise HTTPException(
            status_code=400,
            detail=(
                f"无法打开视频源：{source['name']}。"
                "请确认 RTSP/HTTP 地址、本地文件路径或摄像头编号正确。"
            ),
        )

    frames_read = 0
    consecutive_failures = 0

    try:
        for frame_index in range(frame_count):
            ret, frame = cap.read()

            if not ret or frame is None:
                consecutive_failures += 1
                # 本地视频读到结尾或网络流连续失败时提前结束，避免无效等待。
                if consecutive_failures >= 8:
                    break
                continue

            consecutive_failures = 0
            frames_read += 1

            if frame_index % sample_interval == 0:
                frame_info = save_frame_image(
                    frame=frame,
                    filename_prefix=f"stream_{source['id']}_{frame_index:03d}",
                )
                frame_info["frame_index"] = frame_index
                frame_info["captured_at"] = now_text()
                sampled_frames.append(frame_info)
    finally:
        cap.release()

    if not sampled_frames:
        raise HTTPException(
            status_code=400,
            detail=f"已尝试读取视频源，但没有得到可用抽样帧：{source['name']}",
        )

    return sampled_frames, frames_read


def get_result_confidence(task_type: str, result: dict) -> float:
    if task_type == "plate":
        plates = result.get("plates", []) or []
        if not plates:
            return 0.0
        return max(float(plate.get("confidence", 0) or 0) for plate in plates)

    return float(result.get("confidence", 0) or 0)


def recognize_single_sampled_frame(
    frame_info: dict,
    task_type: str,
    fast_mode: bool = False,
) -> dict:
    """识别一张抽样帧；快速模式不生成标注图，显著减少批量处理耗时。"""
    started = time.perf_counter()
    input_path = frame_info["saved_path"]
    output_filename = ""
    output_path = None

    if not fast_mode:
        output_filename = f"annotated_{task_type}_{frame_info['saved_filename']}"
        output_path = OUTPUT_DIR / output_filename

    if task_type == "plate":
        if fast_mode:
            image_bgr = cv2.imread(str(input_path))
            if image_bgr is None:
                raise RuntimeError("抽样帧读取失败")
            result = recognize_plate_frame(
                image_bgr=image_bgr,
                output_path=None,
                draw_annotation=False,
            )
        else:
            result = create_annotated_plate_image(
                input_path=input_path,
                output_path=output_path,
                confidence=0.92,
            )
    elif task_type == "owner_gesture":
        result = recognize_owner_gesture_image(
            input_path=input_path,
            output_path=output_path,
        )
    elif task_type == "traffic_gesture":
        if fast_mode:
            image_bgr = cv2.imread(str(input_path))
            if image_bgr is None:
                raise RuntimeError("抽样帧读取失败")
            result = recognize_traffic_gesture_frame(
                image_bgr=image_bgr,
                output_path=None,
                static_image_mode=True,
                draw_annotation=False,
            )
        else:
            result = recognize_traffic_gesture_image(
                input_path=input_path,
                output_path=output_path,
            )
    else:
        raise HTTPException(status_code=400, detail="不支持的连续帧识别任务类型")

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    result.setdefault("latency_ms", latency_ms)
    confidence = get_result_confidence(task_type, result)

    return {
        "frame_index": frame_info["frame_index"],
        "captured_at": frame_info.get("captured_at", ""),
        "saved_filename": frame_info["saved_filename"],
        "image_url": frame_info["image_url"],
        "output_filename": output_filename,
        "output_image_url": f"/outputs/{output_filename}" if output_filename else "",
        "confidence": confidence,
        "latency_ms": latency_ms,
        "fast_mode": bool(fast_mode),
        "result": result,
    }


def compact_frame_result(task_type: str, item: dict) -> dict:
    result = item.get("result", {})
    compact = {
        "frame_index": item.get("frame_index"),
        "captured_at": item.get("captured_at", ""),
        "image_url": item.get("image_url"),
        "output_image_url": item.get("output_image_url"),
        "confidence": item.get("confidence", 0),
        "latency_ms": item.get("latency_ms", result.get("latency_ms", 0)),
    }

    if task_type == "plate":
        plates = result.get("plates", []) or []
        compact["plates_count"] = len(plates)
        compact["best_plate"] = max(
            plates,
            key=lambda plate: float(plate.get("confidence", 0) or 0),
            default=None,
        )
        compact["plates"] = [
            {
                "plate_number": plate.get("plate_number", ""),
                "plate_color": plate.get("plate_color", ""),
                "confidence": plate.get("confidence", 0),
                "bbox": plate.get("bbox"),
            }
            for plate in plates
        ]
    else:
        compact["gesture"] = result.get("gesture")
        compact["gesture_name"] = result.get("gesture_name")

    return compact


def aggregate_plate_frame_results(frame_results: list[dict]) -> dict:
    """调用车牌视频聚合 V2。"""
    return aggregate_plate_frame_results_v2(
        frame_results=frame_results,
        compact_frame_result_fn=compact_frame_result,
    )

def ensure_plate_best_frame_annotation(
    best: dict,
    final_result: dict,
    *,
    prefix: str,
) -> tuple[str, str]:
    """快速视频识别结束后，只为最佳帧生成一张标注图。"""
    existing_url = str(best.get("output_image_url") or "").strip()
    existing_name = str(best.get("output_filename") or "").strip()
    if existing_url:
        final_result["best_frame_image_url"] = best.get("image_url", "")
        final_result["best_frame_output_image_url"] = existing_url
        return existing_name, existing_url

    saved_filename = str(best.get("saved_filename") or "").strip()
    if not saved_filename:
        return "", ""
    input_path = UPLOAD_DIR / saved_filename
    image_bgr = cv2.imread(str(input_path))
    if image_bgr is None:
        return "", ""

    output_filename = f"annotated_plate_best_{prefix}_{uuid4().hex}.jpg"
    output_path = OUTPUT_DIR / output_filename
    result = (
        best.get("result")
        if isinstance(best.get("result"), dict)
        else {}
    )
    best_frame_plates = (
        final_result.get("best_frame_plates")
        or result.get("plates")
        or []
    )

    draw_plate_annotations(
        image_bgr=image_bgr,
        plates=best_frame_plates,
        output_path=output_path,
    )

    output_url = f"/outputs/{output_filename}"
    best["output_filename"] = output_filename
    best["output_image_url"] = output_url
    final_result["best_frame_index"] = final_result.get("best_frame_index") if final_result.get("best_frame_index") is not None else best.get("frame_index")
    final_result["best_frame_image_url"] = best.get("image_url", "")
    final_result["best_frame_output_image_url"] = output_url
    for item in final_result.get("plates", []) or []:
        if isinstance(item, dict) and (item.get("best_frame_index") is None or item.get("best_frame_index") == best.get("frame_index")):
            item["best_output_image_url"] = output_url
    return output_filename, output_url


def aggregate_gesture_frame_results(task_type: str, frame_results: list[dict]) -> dict:
    if task_type == "traffic_gesture":
        final_result = aggregate_traffic_gesture_sequence(frame_results)
        best_frame_index = final_result.get("best_frame_index")

        best = next(
            (item for item in frame_results if item.get("frame_index") == best_frame_index),
            None,
        )
        if best is None:
            best = max(
                frame_results,
                key=lambda item: float(item.get("confidence", 0) or 0),
            )

        final_result["stream_strategy"] = "关键点规则 + 连续帧投票 + 手腕轨迹"
        final_result["sampled_frames"] = len(frame_results)
        final_result["frame_results"] = [
            compact_frame_result(task_type, item) for item in frame_results
        ]

        return {
            "best": best,
            "final_result": final_result,
        }

    valid_items = [
        item for item in frame_results
        if item.get("result", {}).get("gesture") not in (None, "", "unknown")
    ]

    if valid_items:
        counter = Counter(item["result"].get("gesture") for item in valid_items)

        def gesture_score(gesture: str):
            same_gesture_items = [
                item for item in valid_items if item["result"].get("gesture") == gesture
            ]
            avg_confidence = sum(
                float(item.get("confidence", 0) or 0) for item in same_gesture_items
            ) / len(same_gesture_items)
            return counter[gesture], avg_confidence

        selected_gesture = max(counter.keys(), key=gesture_score)
        candidates = [
            item for item in valid_items
            if item["result"].get("gesture") == selected_gesture
        ]
        best = max(candidates, key=lambda item: float(item.get("confidence", 0) or 0))
        strategy = "连续帧抽样 + 手势多数投票"
        stable_frames = counter[selected_gesture]
        vote_ratio = stable_frames / max(1, len(valid_items))
    else:
        best = max(frame_results, key=lambda item: float(item.get("confidence", 0) or 0))
        counter = Counter()
        strategy = "连续帧抽样 + 最高置信度兜底"
        stable_frames = 0
        vote_ratio = 0.0

    final_result = dict(best["result"])
    final_result["stream_strategy"] = strategy
    final_result["best_frame_index"] = best["frame_index"]
    final_result["sampled_frames"] = len(frame_results)
    final_result["stable_frames"] = stable_frames
    final_result["vote_ratio"] = round(vote_ratio, 4)
    final_result["vote_counts"] = dict(counter)
    final_result["frame_results"] = [
        compact_frame_result(task_type, item) for item in frame_results
    ]

    return {
        "best": best,
        "final_result": final_result,
    }


def recognize_stream_for_task(
    task_type: str,
    sampled_frames: list[dict],
    fast_mode: bool = False,
) -> dict:
    frame_results = []

    for frame_info in sampled_frames:
        try:
            frame_results.append(
                recognize_single_sampled_frame(
                    frame_info=frame_info,
                    task_type=task_type,
                    fast_mode=fast_mode,
                )
            )
        except Exception as error:
            frame_results.append(
                {
                    "frame_index": frame_info["frame_index"],
                    "saved_filename": frame_info["saved_filename"],
                    "image_url": frame_info["image_url"],
                    "output_filename": "",
                    "output_image_url": "",
                    "confidence": 0.0,
                    "result": {
                        "model": task_type,
                        "error": str(error),
                    },
                }
            )

    if not frame_results:
        raise HTTPException(status_code=500, detail="连续帧识别没有产生任何结果")

    if task_type == "plate":
        return aggregate_plate_frame_results(frame_results)

    return aggregate_gesture_frame_results(task_type, frame_results)


def maybe_create_stream_alert(record_id: int, task_type: str, result: dict) -> int | None:
    if task_type == "plate":
        return maybe_create_low_confidence_alert(record_id=record_id, result=result)

    if task_type in {"owner_gesture", "traffic_gesture"}:
        confidence = float(result.get("confidence", 0) or 0)
        gesture = result.get("gesture", "unknown")

        if gesture == "unknown" or confidence < 0.6:
            event_type = f"{task_type}_stream_low_confidence"
            summary = "连续帧手势识别置信度偏低"
            reason = f"本次连续帧识别结果为 {result.get('gesture_name', '未知')}，置信度 {confidence:.2f}。"
            suggestion = "建议调整摄像头角度、光照和目标距离，并使用包含完整手势动作的视频流。"

            return insert_alert_event(
                level="warning",
                event_type=event_type,
                summary=summary,
                reason=reason,
                suggestion=suggestion,
                related_record_id=record_id,
            )

    if task_type == "all":
        tasks = result.get("tasks", {})
        failed_tasks = []

        plate_result = tasks.get("plate", {}).get("result", {})
        if not plate_result.get("plates"):
            failed_tasks.append("车牌")

        for key, label in [("traffic_gesture", "交警手势"), ("owner_gesture", "车主手势")]:
            gesture_result = tasks.get(key, {}).get("result", {})
            if gesture_result.get("gesture") in (None, "", "unknown"):
                failed_tasks.append(label)

        if failed_tasks:
            return insert_alert_event(
                level="warning",
                event_type="stream_multi_task_partial_failed",
                summary="综合连续帧识别存在未识别目标",
                reason=f"以下任务未得到稳定识别结果：{'、'.join(failed_tasks)}。",
                suggestion="建议切换更合适的视频源，或分别使用车牌、交警、车主手势专用摄像头进行识别。",
                related_record_id=record_id,
            )

    return None

def maybe_create_low_confidence_alert(record_id: int, result: dict) -> int | None:
    plates = result.get("plates", [])

    if not plates:
        return insert_alert_event(
            level="warning",
            event_type="plate_recognition_failed",
            summary="车牌识别未检测到有效车牌",
            reason="当前图片未返回任何车牌检测结果，可能由图片模糊、角度过大或车辆距离过远导致。",
            suggestion="建议更换更清晰的道路图片，或检查摄像头角度与光照条件。",
            related_record_id=record_id,
        )

    min_confidence = min(float(plate.get("confidence", 0)) for plate in plates)

    if min_confidence < 0.6:
        return insert_alert_event(
            level="warning",
            event_type="low_confidence",
            summary="车牌识别置信度偏低",
            reason=f"本次识别最低置信度为 {min_confidence:.2f}，低于系统阈值 0.60。",
            suggestion="建议检查图片清晰度、车牌遮挡情况、光照条件，必要时切换更适合的识别模型。",
            related_record_id=record_id,
        )

    return None


def _row_value(row: sqlite3.Row, key: str, default=None):
    return row[key] if key in row.keys() else default


def summarize_recognition_result(task_type: str, result: dict) -> str:
    result = result or {}

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
        if values:
            preview = "、".join(values[:3])
            if len(values) > 3:
                preview += f" 等 {len(values)} 个"
            return preview
        return "未识别到有效车牌"

    if task_type == "traffic_gesture":
        gesture_name = result.get("gesture_name") or result.get("gesture") or "未知手势"
        command = result.get("traffic_command") or result.get("command") or ""
        return f"{gesture_name} · {command}" if command else str(gesture_name)

    if task_type == "owner_gesture":
        gesture_name = result.get("gesture_name") or result.get("gesture") or "未知手势"
        action = result.get("description") or result.get("action") or ""
        return f"{gesture_name} · {action}" if action else str(gesture_name)

    if task_type == "all":
        tasks = result.get("tasks") or {}
        labels = []
        for key, label in (
            ("plate", "车牌"),
            ("traffic_gesture", "交警"),
            ("owner_gesture", "车主"),
        ):
            payload = tasks.get(key, {}).get("result", {})
            labels.append(f"{label}:{summarize_recognition_result(key, payload)}")
        return "；".join(labels)

    return (
        result.get("summary")
        or result.get("suggestion")
        or result.get("message")
        or "查看完整结果"
    )


def row_to_record(row: sqlite3.Row) -> dict:
    result_text = _row_value(row, "result_json", "") or ""

    try:
        result = json.loads(result_text) if result_text else {}
    except json.JSONDecodeError:
        result = {"raw": result_text}

    task_type = _row_value(row, "task_type", "")
    return {
        "id": _row_value(row, "id"),
        "user_id": _row_value(row, "user_id"),
        "username": _row_value(row, "username"),
        "user_email": _row_value(row, "user_email"),
        "task_type": task_type,
        "input_type": _row_value(row, "input_type"),
        "original_filename": _row_value(row, "original_filename"),
        "saved_filename": _row_value(row, "saved_filename"),
        "image_url": _row_value(row, "image_url"),
        "output_image_url": _row_value(row, "output_image_url"),
        "result": result,
        "result_summary": summarize_recognition_result(task_type, result),
        "created_at": _row_value(row, "created_at"),
    }


def row_to_alert(row: sqlite3.Row) -> dict:
    total_count = int(_row_value(row, "email_total_count", 0) or 0)
    sent_count = int(_row_value(row, "email_sent_count", 0) or 0)
    failed_count = int(_row_value(row, "email_failed_count", 0) or 0)
    pending_count = int(_row_value(row, "email_pending_count", 0) or 0)
    skipped_count = int(_row_value(row, "email_skipped_count", 0) or 0)

    if total_count == 0:
        email_status = "未创建"
    elif pending_count > 0:
        email_status = "发送中"
    elif sent_count == total_count:
        email_status = "已发送"
    elif sent_count > 0 and (failed_count > 0 or skipped_count > 0):
        email_status = "部分发送"
    elif failed_count > 0:
        email_status = "发送失败"
    elif skipped_count == total_count:
        email_status = "已跳过"
    else:
        email_status = "待发送"

    return {
        "id": _row_value(row, "id"),
        "user_id": _row_value(row, "user_id"),
        "username": _row_value(row, "username"),
        "user_email": _row_value(row, "user_email"),
        "level": _row_value(row, "level"),
        "event_type": _row_value(row, "event_type"),
        "summary": _row_value(row, "summary"),
        "reason": _row_value(row, "reason"),
        "suggestion": _row_value(row, "suggestion"),
        "status": _row_value(row, "status"),
        "related_record_id": _row_value(row, "related_record_id"),
        "created_at": _row_value(row, "created_at"),
        "email_notification_status": email_status,
        "email_total_count": total_count,
        "email_sent_count": sent_count,
        "email_failed_count": failed_count,
        "email_pending_count": pending_count,
        "email_skipped_count": skipped_count,
    }


def row_to_log(row: sqlite3.Row) -> dict:
    detail_text = _row_value(row, "detail", "") or ""

    try:
        detail = json.loads(detail_text) if detail_text else {}
    except json.JSONDecodeError:
        detail = detail_text

    return {
        "id": _row_value(row, "id"),
        "user_id": _row_value(row, "user_id"),
        "username": _row_value(row, "username"),
        "user_email": _row_value(row, "user_email"),
        "action": _row_value(row, "action"),
        "detail": detail,
        "created_at": _row_value(row, "created_at"),
    }

def get_vehicle_state() -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()

    row = cursor.execute(
        """
        SELECT
            id,
            system_awake,
            current_function,
            volume,
            temperature,
            phone_status,
            updated_at
        FROM vehicle_state
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    if row is None:
        raise HTTPException(status_code=500, detail="车辆状态初始化失败")

    return {
        "system_awake": bool(row["system_awake"]),
        "current_function": row["current_function"],
        "volume": row["volume"],
        "temperature": row["temperature"],
        "phone_status": row["phone_status"],
        "updated_at": row["updated_at"],
    }


def update_vehicle_state(state: dict):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE vehicle_state
        SET
            system_awake = ?,
            current_function = ?,
            volume = ?,
            temperature = ?,
            phone_status = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            1 if state["system_awake"] else 0,
            state["current_function"],
            state["volume"],
            state["temperature"],
            state["phone_status"],
            now_text(),
        ),
    )

    conn.commit()
    conn.close()


def apply_owner_gesture(gesture: str) -> dict:
    gesture_map = {
        "open_palm": "手掌张开",
        "fist": "握拳",
        "swipe_left": "左滑",
        "swipe_right": "右滑",
        "thumb_up": "拇指向上",
        "thumb_down": "拇指向下",
        "wave": "挥手",
        "circle": "单指画圈",
    }

    if gesture not in gesture_map:
        raise HTTPException(status_code=400, detail="不支持的车主手势")

    state = get_vehicle_state()
    functions = ["home", "music", "air_conditioner", "phone", "navigation"]

    action = ""
    description = ""

    if gesture == "open_palm":
        state["system_awake"] = True
        action = "wake_system"
        description = "系统已唤醒"

    elif gesture == "fist":
        action = "confirm"
        description = f"确认当前功能：{state['current_function']}"

    elif gesture == "swipe_left":
        index = functions.index(state["current_function"])
        state["current_function"] = functions[(index - 1) % len(functions)]
        action = "previous_function"
        description = f"切换到上一个功能：{state['current_function']}"

    elif gesture == "swipe_right":
        index = functions.index(state["current_function"])
        state["current_function"] = functions[(index + 1) % len(functions)]
        action = "next_function"
        description = f"切换到下一个功能：{state['current_function']}"

    elif gesture == "thumb_up":
        if state["current_function"] == "phone":
            state["phone_status"] = "已接听"
            action = "answer_phone"
            description = "电话已接听"
        else:
            state["volume"] = min(100, state["volume"] + 5)
            action = "volume_up"
            description = f"音量增加至 {state['volume']}"

    elif gesture == "thumb_down":
        if state["current_function"] == "phone":
            state["phone_status"] = "已挂断"
            action = "hang_up_phone"
            description = "电话已挂断"
        else:
            state["volume"] = max(0, state["volume"] - 5)
            action = "volume_down"
            description = f"音量降低至 {state['volume']}"

    elif gesture == "wave":
        state["current_function"] = "home"
        action = "back_home"
        description = "已返回主页"

    elif gesture == "circle":
        if state["current_function"] == "air_conditioner":
            state["temperature"] = min(30, state["temperature"] + 1)
            action = "temperature_up"
            description = f"空调温度调高至 {state['temperature']}℃"
        else:
            state["volume"] = min(100, state["volume"] + 10)
            action = "volume_adjust"
            description = f"音量快速调节至 {state['volume']}"

    update_vehicle_state(state)

    event_id = int(
        dynamic_result.get("event_id") or 0
    ) if dynamic_result else 0

    if triggered and dynamic_phase == "idle":
        event_id = _oc_next_event_id()

    return {
        "gesture": gesture,
        "gesture_name": gesture_map[gesture],
        "action": action,
        "description": description,
        "confidence": 0.91,
        "vehicle_state": get_vehicle_state(),
    }


def recognize_traffic_gesture(gesture: str) -> dict:
    traffic_map = {
        "stop": {
            "gesture_name": "停止信号",
            "command": "车辆停止通行",
            "confidence": 0.93,
        },
        "straight": {
            "gesture_name": "直行信号",
            "command": "车辆允许直行",
            "confidence": 0.90,
        },
        "left_turn": {
            "gesture_name": "左转弯信号",
            "command": "车辆允许左转",
            "confidence": 0.89,
        },
        "left_turn_wait": {
            "gesture_name": "左转弯待转信号",
            "command": "车辆进入待转区",
            "confidence": 0.86,
        },
        "right_turn": {
            "gesture_name": "右转弯信号",
            "command": "车辆允许右转",
            "confidence": 0.88,
        },
        "lane_change": {
            "gesture_name": "变道信号",
            "command": "车辆按指令变道",
            "confidence": 0.84,
        },
        "slow_down": {
            "gesture_name": "减速慢行信号",
            "command": "车辆减速慢行",
            "confidence": 0.87,
        },
        "pull_over": {
            "gesture_name": "靠边停车信号",
            "command": "车辆靠边停车",
            "confidence": 0.85,
        },
    }

    if gesture not in traffic_map:
        raise HTTPException(status_code=400, detail="不支持的交警手势")

    result = traffic_map[gesture]

    return {
        "gesture": gesture,
        "gesture_name": result["gesture_name"],
        "traffic_command": result["command"],
        "confidence": result["confidence"],
        "keypoints": [
            {"name": "left_shoulder", "x": 0.35, "y": 0.42},
            {"name": "right_shoulder", "x": 0.65, "y": 0.42},
            {"name": "left_wrist", "x": 0.25, "y": 0.35},
            {"name": "right_wrist", "x": 0.75, "y": 0.36},
        ],
    }


@app.on_event("startup")
def on_startup():
    init_db()
    start_alert_notification_worker(DB_PATH)


@app.on_event("shutdown")
def on_shutdown():
    """停止所有 ffmpeg 推流进程。"""
    stop_all_streams()


@app.get("/")
def root():
    return {
        "message": "智能车载视觉感知与告警系统后端已启动"
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "message": "backend is running"
    }


@app.get("/api/self-check")
def self_check():
    checks = {
        "backend": True,
        "database": DB_PATH.exists(),
        "upload_dir": UPLOAD_DIR.exists(),
        "output_dir": OUTPUT_DIR.exists(),
        "rtsp_sources": len(RTSP_SOURCES),
    }

    return {
        "status": "success",
        "checks": checks,
        "message": "系统基础环境检查完成",
    }


def build_dashboard_summary(user_id: int | None = None) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")

    where = ""
    params: tuple = ()
    if user_id is not None:
        where = " WHERE user_id = ?"
        params = (int(user_id),)

    total_records = cursor.execute(
        f"SELECT COUNT(*) AS count FROM recognition_records{where}",
        params,
    ).fetchone()["count"]

    if user_id is None:
        today_records = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM recognition_records
            WHERE created_at LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()["count"]
        total_alerts = cursor.execute(
            "SELECT COUNT(*) AS count FROM alert_events"
        ).fetchone()["count"]
        unresolved_alerts = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM alert_events
            WHERE status = '未处理'
            """
        ).fetchone()["count"]
    else:
        today_records = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM recognition_records
            WHERE user_id = ? AND created_at LIKE ?
            """,
            (int(user_id), f"{today}%"),
        ).fetchone()["count"]
        total_alerts = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM alert_events
            WHERE user_id = ?
            """,
            (int(user_id),),
        ).fetchone()["count"]
        unresolved_alerts = cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM alert_events
            WHERE user_id = ? AND status = '未处理'
            """,
            (int(user_id),),
        ).fetchone()["count"]

    conn.close()
    return {
        "total_records": int(total_records),
        "today_records": int(today_records),
        "today_recognition_count": int(today_records),
        "total_alerts": int(total_alerts),
        "unresolved_alerts": int(unresolved_alerts),
    }


@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    current_user: dict = Depends(get_current_user),
):
    summary = build_dashboard_summary(int(current_user["id"]))
    return {
        "status": "success",
        "summary": summary,
        **summary,
    }


@app.get("/api/admin/dashboard/summary")
def get_admin_dashboard_summary(
    admin_user: dict = Depends(require_admin),
):
    summary = build_dashboard_summary(None)
    return {
        "status": "success",
        "summary": summary,
        **summary,
    }


@app.get("/api/rtsp/sources")
def list_rtsp_sources():
    return {
        "status": "success",
        "sources": RTSP_SOURCES,
    }


@app.post("/api/rtsp/recognize")
def recognize_rtsp_frame(request: RtspRecognizeRequest):
    source = next((item for item in RTSP_SOURCES if item["id"] == request.source_id), None)

    if source is None:
        raise HTTPException(status_code=404, detail="未找到指定 RTSP 视频源")

    saved_filename = f"rtsp_{source['id']}_{uuid4().hex}.jpg"
    saved_path = UPLOAD_DIR / saved_filename

    if request.use_mock_frame:
        create_mock_rtsp_frame(source["name"], saved_path)
    else:
        capture_rtsp_frame(source, saved_path)

    image_url = f"/uploads/{saved_filename}"

    output_filename = f"annotated_{saved_filename}"
    output_path = OUTPUT_DIR / output_filename
    output_image_url = f"/outputs/{output_filename}"

    recognition_result = create_annotated_plate_image(
        input_path=saved_path,
        output_path=output_path,
        confidence=0.89,
    )

    recognition_result["source"] = {
        "id": source["id"],
        "name": source["name"],
        "url": source["url"],
        "mode": "mock_frame" if request.use_mock_frame else "real_rtsp",
    }

    record_id = insert_recognition_record(
        task_type="plate",
        input_type="rtsp",
        original_filename=source["name"],
        saved_filename=saved_filename,
        image_url=image_url,
        output_image_url=output_image_url,
        result=recognition_result,
    )

    insert_operation_log(
        action="rtsp_plate_recognition",
        detail={
            "record_id": record_id,
            "source_id": source["id"],
            "source_name": source["name"],
            "use_mock_frame": request.use_mock_frame,
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "task_type": "plate",
        "input_type": "rtsp",
        "source": source,
        "image_url": image_url,
        "output_image_url": output_image_url,
        "result": recognition_result,
    }



def process_stream_recognition_job(
    source: dict,
    task_type: str,
    frame_count: int,
    sample_interval: int,
    use_mock_frame: bool,
    log_action: str = "stream_frame_recognition",
    fast_plate_mode: bool = False,
) -> dict:
    started = time.perf_counter()
    supported_tasks = {"plate", "traffic_gesture", "owner_gesture", "all"}

    if task_type not in supported_tasks:
        raise HTTPException(
            status_code=400,
            detail="task_type 只支持 plate、traffic_gesture、owner_gesture、all",
        )

    frame_count = clamp_int(frame_count, 5, 300)
    sample_interval = clamp_int(sample_interval, 1, 60)

    sampled_frames, frames_read = capture_stream_sampled_frames(
        source=source,
        frame_count=frame_count,
        sample_interval=sample_interval,
        use_mock_frame=use_mock_frame,
    )

    tasks_to_run = (
        ["plate", "traffic_gesture", "owner_gesture"]
        if task_type == "all"
        else [task_type]
    )

    task_outputs: dict[str, dict] = {}
    for task in tasks_to_run:
        task_outputs[task] = recognize_stream_for_task(
            task_type=task,
            sampled_frames=sampled_frames,
            fast_mode=bool(fast_plate_mode and task == "plate"),
        )

    source_mode = "mock_frame" if use_mock_frame else (
        source.get("kind") or _source_kind(source.get("url", ""))
    )

    if task_type == "all":
        first_best = next(
            (output["best"] for output in task_outputs.values() if output.get("best")),
            None,
        )

        if first_best is None:
            raise HTTPException(status_code=500, detail="综合连续帧识别未得到有效结果")

        final_result = {
            "model": "Video Frame Sampling + Multi-task Recognition",
            "stream_strategy": "连续帧抽样 + 多任务分别融合",
            "source": {
                "id": source["id"],
                "name": source["name"],
                "url": source["url"],
                "mode": source_mode,
            },
            "frame_count_requested": frame_count,
            "frames_read": frames_read,
            "sample_interval": sample_interval,
            "sampled_frames": len(sampled_frames),
            "tasks": {
                task: {
                    "best_frame_index": output["best"].get("frame_index"),
                    "image_url": output["best"].get("image_url"),
                    "output_image_url": output["best"].get("output_image_url"),
                    "confidence": output["best"].get("confidence", 0),
                    "result": output["final_result"],
                }
                for task, output in task_outputs.items()
            },
        }

        best_image_url = first_best.get("image_url", "")
        best_output_image_url = first_best.get("output_image_url", "")
        best_saved_filename = first_best.get("saved_filename", "")
    else:
        output = task_outputs[task_type]
        best = output["best"]
        final_result = output["final_result"]
        final_result["source"] = {
            "id": source["id"],
            "name": source["name"],
            "url": source["url"],
            "mode": source_mode,
        }
        final_result["frame_count_requested"] = frame_count
        final_result["frames_read"] = frames_read
        final_result["sample_interval"] = sample_interval

        best_image_url = best.get("image_url", "")
        best_output_image_url = best.get("output_image_url", "")
        best_saved_filename = best.get("saved_filename", "")

    recognition_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    final_result["latency_ms"] = recognition_latency_ms
    input_type = stream_input_type(source, use_mock_frame)

    record_id = insert_recognition_record(
        task_type=task_type,
        input_type=input_type,
        original_filename=source["name"],
        saved_filename=best_saved_filename,
        image_url=best_image_url,
        output_image_url=best_output_image_url,
        result=final_result,
    )

    alert_id = maybe_create_stream_alert(
        record_id=record_id,
        task_type=task_type,
        result=final_result,
    )

    total_latency_ms = round((time.perf_counter() - started) * 1000, 2)

    insert_operation_log(
        action=log_action,
        detail={
            "record_id": record_id,
            "alert_id": alert_id,
            "source_id": source["id"],
            "source_name": source["name"],
            "task_type": task_type,
            "frame_count": frame_count,
            "frames_read": frames_read,
            "sample_interval": sample_interval,
            "sampled_frames": len(sampled_frames),
            "use_mock_frame": use_mock_frame,
            "recognition_latency_ms": recognition_latency_ms,
            "total_latency_ms": total_latency_ms,
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "alert_id": alert_id,
        "task_type": task_type,
        "input_type": input_type,
        "source": source,
        "frame_count": frame_count,
        "frames_read": frames_read,
        "sample_interval": sample_interval,
        "sampled_frames": len(sampled_frames),
        "image_url": best_image_url,
        "output_image_url": best_output_image_url,
        "recognition_latency_ms": recognition_latency_ms,
        "latency_ms": total_latency_ms,
        "result": final_result,
    }


def get_monitor_source_list(source_ids: list[str] | None) -> list[dict]:
    if not source_ids or "all" in source_ids:
        return [dict(source) for source in RTSP_SOURCES]

    sources = []
    missing = []

    for source_id in source_ids:
        source = next((item for item in RTSP_SOURCES if item["id"] == source_id), None)
        if source is None:
            missing.append(source_id)
        else:
            sources.append(dict(source))

    if missing:
        raise HTTPException(status_code=404, detail=f"未找到视频源：{', '.join(missing)}")

    if not sources:
        raise HTTPException(status_code=400, detail="至少需要选择一个视频源")

    return sources


def summarize_monitor_result(result: dict) -> dict:
    task_type = result.get("task_type")
    final_result = result.get("result", {}) or {}
    summary = {
        "task_type": task_type,
        "record_id": result.get("record_id"),
        "alert_id": result.get("alert_id"),
        "input_type": result.get("input_type"),
        "frames_read": result.get("frames_read"),
        "sampled_frames": result.get("sampled_frames"),
        "image_url": result.get("image_url"),
        "output_image_url": result.get("output_image_url"),
    }

    if task_type == "plate":
        plates = final_result.get("plates", []) or []
        summary["plate_count"] = len(plates)
        summary["plates"] = plates
    elif task_type in {"traffic_gesture", "owner_gesture"}:
        summary["gesture"] = final_result.get("gesture")
        summary["gesture_name"] = final_result.get("gesture_name")
        summary["confidence"] = final_result.get("confidence", 0)
    elif task_type == "all":
        tasks = final_result.get("tasks", {}) or {}
        plate_result = tasks.get("plate", {}).get("result", {}) or {}
        summary["plate_count"] = len(plate_result.get("plates", []) or [])
        summary["plates"] = plate_result.get("plates", []) or []
        summary["traffic_gesture"] = tasks.get("traffic_gesture", {}).get("result", {}).get("gesture_name")
        summary["owner_gesture"] = tasks.get("owner_gesture", {}).get("result", {}).get("gesture_name")

    return summary


def monitor_add_event(event: dict):
    with MONITOR_LOCK:
        events = MONITOR_STATE.setdefault("recent_events", [])
        events.insert(0, event)
        del events[30:]


def monitor_worker(config: dict):
    sources = config["sources"]

    while not MONITOR_STOP_EVENT.is_set():
        round_started_at = now_text()

        with MONITOR_LOCK:
            MONITOR_STATE["last_round_at"] = round_started_at

        for source in sources:
            if MONITOR_STOP_EVENT.is_set():
                break

            source_id = source["id"]

            with MONITOR_LOCK:
                MONITOR_STATE.setdefault("source_status", {}).setdefault(source_id, {})
                MONITOR_STATE["source_status"][source_id].update(
                    {
                        "source_id": source_id,
                        "source_name": source["name"],
                        "url": source["url"],
                        "status": "running",
                        "last_started_at": now_text(),
                        "last_error": "",
                    }
                )

            try:
                result = process_stream_recognition_job(
                    source=source,
                    task_type=config["task_type"],
                    frame_count=config["frame_count"],
                    sample_interval=config["sample_interval"],
                    use_mock_frame=config["use_mock_frame"],
                    log_action="auto_monitor_stream_recognition",
                )
                summary = summarize_monitor_result(result)

                with MONITOR_LOCK:
                    MONITOR_STATE["source_status"][source_id].update(
                        {
                            "status": "success",
                            "last_finished_at": now_text(),
                            "last_error": "",
                            "last_result": summary,
                            "last_record_id": result.get("record_id"),
                            "last_alert_id": result.get("alert_id"),
                            "last_plate_count": summary.get("plate_count", 0),
                        }
                    )
                    MONITOR_STATE["total_records_created"] += 1
                    if result.get("alert_id"):
                        MONITOR_STATE["total_alerts_created"] += 1

                monitor_add_event(
                    {
                        "time": now_text(),
                        "source_id": source_id,
                        "source_name": source["name"],
                        "status": "success",
                        "record_id": result.get("record_id"),
                        "alert_id": result.get("alert_id"),
                        "summary": summary,
                    }
                )

            except Exception as error:
                detail = getattr(error, "detail", None) or str(error)
                alert_id = insert_alert_event(
                    level="error",
                    event_type="auto_monitor_source_failed",
                    summary="自动监控视频源识别失败",
                    reason=f"视频源 {source['name']}（{source_id}）本轮自动识别失败：{detail}",
                    suggestion="建议检查 RTSP 地址、沙盘推流状态、网络连通性和视频源是否包含目标对象。",
                    related_record_id=None,
                )

                with MONITOR_LOCK:
                    MONITOR_STATE["source_status"][source_id].update(
                        {
                            "status": "error",
                            "last_finished_at": now_text(),
                            "last_error": str(detail),
                            "last_alert_id": alert_id,
                        }
                    )
                    MONITOR_STATE["total_alerts_created"] += 1

                monitor_add_event(
                    {
                        "time": now_text(),
                        "source_id": source_id,
                        "source_name": source["name"],
                        "status": "error",
                        "alert_id": alert_id,
                        "error": str(detail),
                    }
                )

        with MONITOR_LOCK:
            MONITOR_STATE["rounds_completed"] += 1
            MONITOR_STATE["next_round_after"] = f"{config['interval_seconds']} 秒后"

        # --- AlertAgent 每轮评估 ---
        try:
            snapshot = get_monitor_state_snapshot()
            alerts = _ALERT_AGENT.evaluate(snapshot)
            for alert in alerts:
                try:
                    db_id = insert_alert_event(
                        level=alert.level,
                        event_type=alert.event_type,
                        summary=alert.summary,
                        reason=alert.reason,
                        suggestion=alert.suggestion,
                        related_record_id=alert.related_record_id,
                    )
                    alert_dict = _ALERT_AGENT.to_dict(alert)
                    alert_dict["db_id"] = db_id
                    _broadcast_alert_sse(alert_dict)
                    with MONITOR_LOCK:
                        MONITOR_STATE["total_alerts_created"] += 1
                except Exception:
                    pass
        except Exception:
            pass

        if MONITOR_STOP_EVENT.wait(config["interval_seconds"]):
            break

    with MONITOR_LOCK:
        MONITOR_STATE["running"] = False
        MONITOR_STATE["stopped_at"] = now_text()
        MONITOR_STATE["next_round_after"] = "已停止"


def get_monitor_state_snapshot() -> dict:
    with MONITOR_LOCK:
        return json.loads(json.dumps(MONITOR_STATE, ensure_ascii=False))


@app.post("/api/stream/recognize")
def recognize_stream_frames(request: StreamRecognizeRequest):
    """
    统一视频连续帧识别接口。

    支持：
    - 预设 RTSP source_id
    - custom_rtsp_url（兼容旧前端）
    - custom_source_url（RTSP/HTTP/本地视频路径/摄像头编号）
    - plate / traffic_gesture / owner_gesture / all
    """
    custom_source = request.custom_source_url or request.custom_rtsp_url
    source = get_stream_source(request.source_id, custom_source)

    return process_stream_recognition_job(
        source=source,
        task_type=request.task_type,
        frame_count=request.frame_count,
        sample_interval=request.sample_interval,
        use_mock_frame=request.use_mock_frame,
        log_action="stream_frame_recognition",
        fast_plate_mode=bool(request.fast_mode),
    )


# ---------------------------------------------------------------------------
# 本地视频上传识别：车牌 / 交警手势
# ---------------------------------------------------------------------------

VIDEO_UPLOAD_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
MAX_VIDEO_UPLOAD_BYTES = 500 * 1024 * 1024


def _save_uploaded_recognition_video(file: UploadFile, task_type: str) -> dict:
    """保存浏览器上传的视频，并进行扩展名、空文件和大小检查。"""
    original_filename = file.filename or ""
    suffix = Path(original_filename).suffix.lower()

    if suffix not in VIDEO_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="只支持 mp4、avi、mov、mkv、webm、m4v 格式视频",
        )

    saved_filename = f"{task_type}_video_{uuid4().hex}{suffix}"
    saved_path = UPLOAD_DIR / saved_filename

    try:
        with saved_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer, length=1024 * 1024)
    finally:
        try:
            file.file.close()
        except Exception:
            pass

    file_size = saved_path.stat().st_size if saved_path.exists() else 0

    if file_size <= 0:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传的视频文件为空")

    if file_size > MAX_VIDEO_UPLOAD_BYTES:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="视频文件不能超过 500MB")

    return {
        "original_filename": original_filename,
        "saved_filename": saved_filename,
        "saved_path": saved_path,
        "video_url": f"/uploads/{saved_filename}",
        "file_size_bytes": file_size,
    }


def _read_uploaded_video_metadata(video_path: Path) -> dict:
    """读取上传视频的基础信息，无法解码时直接返回明确错误。"""
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        cap.release()
        raise HTTPException(
            status_code=400,
            detail="视频文件无法打开，请确认文件未损坏且编码格式受 OpenCV/FFmpeg 支持",
        )

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()

    duration_seconds = (
        round(total_frames / fps, 3)
        if fps > 0 and total_frames > 0
        else None
    )

    return {
        "fps": round(fps, 3) if fps > 0 else 0,
        "total_frames": total_frames,
        "duration_seconds": duration_seconds,
        "width": width,
        "height": height,
    }


def process_uploaded_video_recognition(
    file: UploadFile,
    task_type: str,
    frame_count: int,
    sample_interval: int,
    log_action: str = "uploaded_video_recognition",
) -> dict:
    """
    上传视频连续帧识别主流程。

    - plate：HyperLPR3 多车牌检测 + 多帧去重聚合
    - traffic_gesture：MediaPipe Pose + 连续帧投票 + 手腕轨迹
    """
    if task_type not in {"plate", "traffic_gesture"}:
        raise HTTPException(
            status_code=400,
            detail="task_type 只支持 plate 或 traffic_gesture",
        )

    frame_count = clamp_int(frame_count, 5, 300)
    sample_interval = clamp_int(sample_interval, 1, 60)
    started = time.perf_counter()

    upload_info = _save_uploaded_recognition_video(file, task_type)
    video_metadata = _read_uploaded_video_metadata(upload_info["saved_path"])

    source = {
        "id": f"uploaded_{task_type}",
        "name": upload_info["original_filename"] or "上传视频",
        "url": str(upload_info["saved_path"]),
        "kind": "local_video",
    }

    try:
        sampled_frames, frames_read = capture_stream_sampled_frames(
            source=source,
            frame_count=frame_count,
            sample_interval=sample_interval,
            use_mock_frame=False,
        )

        recognition_output = recognize_stream_for_task(
            task_type=task_type,
            sampled_frames=sampled_frames,
        )
    except HTTPException:
        insert_operation_log(
            action=f"{log_action}_failed",
            detail={
                "task_type": task_type,
                "original_filename": upload_info["original_filename"],
                "saved_filename": upload_info["saved_filename"],
            },
        )
        raise
    except Exception as error:
        insert_operation_log(
            action=f"{log_action}_failed",
            detail={
                "task_type": task_type,
                "original_filename": upload_info["original_filename"],
                "saved_filename": upload_info["saved_filename"],
                "reason": str(error),
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"上传视频识别失败：{error}",
        ) from error

    best = recognition_output["best"]
    final_result = recognition_output["final_result"]
    processing_latency_ms = round((time.perf_counter() - started) * 1000, 2)

    final_result["source"] = {
        "id": source["id"],
        "name": source["name"],
        "kind": "uploaded_video",
        "video_url": upload_info["video_url"],
    }
    final_result["video_metadata"] = video_metadata
    final_result["frame_count_requested"] = frame_count
    final_result["frames_read"] = frames_read
    final_result["sample_interval"] = sample_interval
    final_result["sampled_frames"] = len(sampled_frames)
    final_result["processing_latency_ms"] = processing_latency_ms
    final_result["input_mode"] = "uploaded_video"

    if task_type == "plate":
        ensure_plate_best_frame_annotation(best, final_result, prefix="single_video")

    best_image_url = best.get("image_url", "")
    best_output_image_url = best.get("output_image_url", "")
    best_saved_filename = best.get("saved_filename", "")

    record_id = insert_recognition_record(
        task_type=task_type,
        input_type="video_upload",
        original_filename=upload_info["original_filename"],
        saved_filename=upload_info["saved_filename"],
        image_url=best_image_url or upload_info["video_url"],
        output_image_url=best_output_image_url,
        result=final_result,
    )

    alert_id = maybe_create_stream_alert(
        record_id=record_id,
        task_type=task_type,
        result=final_result,
    )

    insert_operation_log(
        action=log_action,
        detail={
            "record_id": record_id,
            "alert_id": alert_id,
            "task_type": task_type,
            "original_filename": upload_info["original_filename"],
            "saved_filename": upload_info["saved_filename"],
            "file_size_bytes": upload_info["file_size_bytes"],
            "frame_count": frame_count,
            "frames_read": frames_read,
            "sample_interval": sample_interval,
            "sampled_frames": len(sampled_frames),
            "processing_latency_ms": processing_latency_ms,
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "alert_id": alert_id,
        "task_type": task_type,
        "input_type": "video_upload",
        "original_filename": upload_info["original_filename"],
        "saved_filename": upload_info["saved_filename"],
        "video_url": upload_info["video_url"],
        "file_size_bytes": upload_info["file_size_bytes"],
        "video_metadata": video_metadata,
        "frame_count": frame_count,
        "frames_read": frames_read,
        "sample_interval": sample_interval,
        "sampled_frames": len(sampled_frames),
        "image_url": best_image_url,
        "output_image_url": best_output_image_url,
        "best_saved_filename": best_saved_filename,
        "processing_latency_ms": processing_latency_ms,
        "result": final_result,
    }


@app.post("/api/video/recognize")
def recognize_uploaded_video(
    file: UploadFile = File(...),
    task_type: str = Query("plate", description="plate 或 traffic_gesture"),
    frame_count: int = Query(120, ge=5, le=300, description="最多连续读取的帧数"),
    sample_interval: int = Query(5, ge=1, le=60, description="每隔多少帧识别一次"),
):
    """统一的本地视频上传识别接口。"""
    return process_uploaded_video_recognition(
        file=file,
        task_type=task_type,
        frame_count=frame_count,
        sample_interval=sample_interval,
        log_action="uploaded_video_recognition",
    )


@app.post("/api/plate/video")
def recognize_plate_from_video(
    file: UploadFile = File(...),
    frame_count: int = Query(120, ge=5, le=300),
    sample_interval: int = Query(5, ge=1, le=60),
):
    """上传本地视频并执行多车牌连续帧识别。"""
    return process_uploaded_video_recognition(
        file=file,
        task_type="plate",
        frame_count=frame_count,
        sample_interval=sample_interval,
        log_action="plate_video_recognition",
    )


@app.post("/api/gesture/traffic/video")
def recognize_traffic_gesture_from_video(
    file: UploadFile = File(...),
    frame_count: int = Query(120, ge=5, le=300),
    sample_interval: int = Query(3, ge=1, le=60),
):
    """上传本地视频并执行交警手势连续帧识别。"""
    return process_uploaded_video_recognition(
        file=file,
        task_type="traffic_gesture",
        frame_count=frame_count,
        sample_interval=sample_interval,
        log_action="traffic_gesture_video_recognition",
    )


# ---------------------------------------------------------------------------
# 批量上传识别：支持图片和视频混合上传
# ---------------------------------------------------------------------------

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
BATCH_MAX_FILES = 50
BATCH_MAX_TOTAL_BYTES = 1024 * 1024 * 1024  # 1GB total


def _is_image_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_SUFFIXES


def _is_video_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_UPLOAD_SUFFIXES


def _process_single_uploaded_file(
    file: UploadFile,
    task_type: str,
    frame_count: int,
    sample_interval: int,
) -> dict:
    """处理单个上传文件：自动判断图片/视频，调用对应识别管线。"""
    original_name = file.filename or ""
    file_type = "image" if _is_image_file(original_name) else "video"
    started = time.perf_counter()

    try:
        if file_type == "image":
            # --- 图片识别 ---
            suffix = Path(original_name).suffix.lower()
            saved_filename = f"batch_{task_type}_{uuid4().hex}{suffix}"
            saved_path = UPLOAD_DIR / saved_filename

            with saved_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer, length=1024 * 1024)

            image_url = f"/uploads/{saved_filename}"
            output_filename = f"annotated_{saved_filename}"
            output_path = OUTPUT_DIR / output_filename
            output_image_url = f"/outputs/{output_filename}"

            if task_type == "plate":
                result = create_annotated_plate_image(
                    input_path=saved_path,
                    output_path=output_path,
                    confidence=0.92,
                )
            elif task_type == "traffic_gesture":
                result = recognize_traffic_gesture_image(
                    input_path=saved_path,
                    output_path=output_path,
                )
            else:
                raise HTTPException(status_code=400, detail=f"不支持的任务类型: {task_type}")

            processing_latency_ms = round((time.perf_counter() - started) * 1000, 2)
            result.setdefault("latency_ms", processing_latency_ms)

            record_id = insert_recognition_record(
                task_type=task_type,
                input_type="image",
                original_filename=original_name,
                saved_filename=saved_filename,
                image_url=image_url,
                output_image_url=output_image_url,
                result=result,
            )

            return {
                "filename": original_name,
                "file_type": "image",
                "status": "success",
                "record_id": record_id,
                "image_url": image_url,
                "output_image_url": output_image_url,
                "processing_latency_ms": processing_latency_ms,
                "result": result,
            }

        else:
            # --- 视频识别 ---
            upload_info = _save_uploaded_recognition_video(file, task_type)
            video_metadata = _read_uploaded_video_metadata(upload_info["saved_path"])

            source = {
                "id": f"batch_{task_type}_{uuid4().hex[:8]}",
                "name": upload_info["original_filename"] or "批量上传视频",
                "url": str(upload_info["saved_path"]),
                "kind": "local_video",
            }

            requested_frame_count = frame_count
            requested_sample_interval = sample_interval

            # 车牌视频不再默认执行几十次 OCR。最多抽取约 12 帧，且跳过
            # 每帧标注图绘制与写盘；最终仍通过跨帧聚合返回稳定结果。
            if task_type == "plate":
                frame_count = min(frame_count, 120)
                target_samples = 12
                sample_interval = max(
                    sample_interval,
                    max(1, frame_count // target_samples),
                )

            sampled_frames, frames_read = capture_stream_sampled_frames(
                source=source,
                frame_count=frame_count,
                sample_interval=sample_interval,
                use_mock_frame=False,
            )

            recognition_output = recognize_stream_for_task(
                task_type=task_type,
                sampled_frames=sampled_frames,
                fast_mode=(task_type == "plate"),
            )

            best = recognition_output["best"]
            final_result = recognition_output["final_result"]
            processing_latency_ms = round((time.perf_counter() - started) * 1000, 2)

            final_result["source"] = {
                "id": source["id"],
                "name": source["name"],
                "kind": "uploaded_video",
                "video_url": upload_info["video_url"],
            }
            final_result["video_metadata"] = video_metadata
            final_result["frame_count_requested"] = requested_frame_count
            final_result["sample_interval_requested"] = requested_sample_interval
            final_result["frame_count_effective"] = frame_count
            final_result["frames_read"] = frames_read
            final_result["sample_interval"] = sample_interval
            final_result["fast_mode"] = task_type == "plate"
            final_result["sampled_frames"] = len(sampled_frames)
            final_result["processing_latency_ms"] = processing_latency_ms
            final_result["input_mode"] = "uploaded_video"

            if task_type == "plate":
                ensure_plate_best_frame_annotation(best, final_result, prefix="batch_video")

            best_image_url = best.get("image_url", "")
            best_output_image_url = best.get("output_image_url", "")

            record_id = insert_recognition_record(
                task_type=task_type,
                input_type="video_upload",
                original_filename=upload_info["original_filename"],
                saved_filename=upload_info["saved_filename"],
                image_url=best_image_url or upload_info["video_url"],
                output_image_url=best_output_image_url,
                result=final_result,
            )

            return {
                "filename": original_name,
                "file_type": "video",
                "status": "success",
                "record_id": record_id,
                "video_url": upload_info["video_url"],
                "image_url": best_image_url,
                "output_image_url": best_output_image_url,
                "frames_read": frames_read,
                "sampled_frames": len(sampled_frames),
                "processing_latency_ms": processing_latency_ms,
                "video_metadata": video_metadata,
                "result": final_result,
            }

    except HTTPException:
        raise
    except Exception as error:
        return {
            "filename": original_name,
            "file_type": file_type,
            "status": "error",
            "error": str(error),
        }


@app.post("/api/recognition/batch")
def recognize_batch_files(
    files: list[UploadFile] = File(...),
    task_type: str = Query("plate", description="plate 或 traffic_gesture"),
    frame_count: int = Query(120, ge=5, le=300),
    sample_interval: int = Query(5, ge=1, le=60),
):
    """
    批量上传识别接口：支持图片和视频混合上传。

    - 自动根据文件扩展名判断图片/视频
    - 图片调用单帧识别管线（HyperLPR3 / MediaPipe）
    - 视频调用连续帧抽帧识别管线
    - 返回每个文件的独立识别结果

    限制：最多 50 个文件，总大小不超过 1GB。
    """
    if task_type not in {"plate", "traffic_gesture"}:
        raise HTTPException(
            status_code=400,
            detail="task_type 只支持 plate 或 traffic_gesture",
        )

    if len(files) > BATCH_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"单次批量上传最多 {BATCH_MAX_FILES} 个文件",
        )

    if len(files) == 0:
        raise HTTPException(status_code=400, detail="请至少选择一个文件")

    frame_count = clamp_int(frame_count, 5, 300)
    sample_interval = clamp_int(sample_interval, 1, 60)

    # 预检文件类型
    for f in files:
        original_name = f.filename or ""
        suffix = Path(original_name).suffix.lower()
        if suffix not in IMAGE_SUFFIXES and suffix not in VIDEO_UPLOAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {original_name}（仅支持图片和视频格式）",
            )

    started = time.perf_counter()
    results = []

    for f in files:
        try:
            file_result = _process_single_uploaded_file(
                file=f,
                task_type=task_type,
                frame_count=frame_count,
                sample_interval=sample_interval,
            )
        except HTTPException:
            raise
        except Exception as error:
            file_result = {
                "filename": f.filename or "unknown",
                "file_type": "unknown",
                "status": "error",
                "error": str(error),
            }
        results.append(file_result)

    total_latency_ms = round((time.perf_counter() - started) * 1000, 2)

    success_count = sum(1 for r in results if r.get("status") == "success")
    error_count = sum(1 for r in results if r.get("status") == "error")

    insert_operation_log(
        action="batch_recognition",
        detail={
            "task_type": task_type,
            "total_files": len(files),
            "success_count": success_count,
            "error_count": error_count,
            "total_latency_ms": total_latency_ms,
        },
    )

    return {
        "status": "success",
        "task_type": task_type,
        "total_files": len(files),
        "success_count": success_count,
        "error_count": error_count,
        "total_latency_ms": total_latency_ms,
        "results": results,
    }


@app.post("/api/monitor/start")
def start_global_monitor(request: MonitorStartRequest):
    supported_tasks = {"plate", "traffic_gesture", "owner_gesture", "all"}

    if request.task_type not in supported_tasks:
        raise HTTPException(
            status_code=400,
            detail="task_type 只支持 plate、traffic_gesture、owner_gesture、all",
        )

    sources = get_monitor_source_list(request.source_ids)
    interval_seconds = clamp_int(request.interval_seconds, 5, 3600)
    frame_count = clamp_int(request.frame_count, 5, 300)
    sample_interval = clamp_int(request.sample_interval, 1, 60)

    global MONITOR_THREAD

    with MONITOR_LOCK:
        if MONITOR_THREAD is not None and MONITOR_THREAD.is_alive():
            return {
                "status": "already_running",
                "message": "全局自动识别监控已经在运行",
                "monitor": get_monitor_state_snapshot(),
            }

        MONITOR_STOP_EVENT.clear()
        MONITOR_STATE.update(
            {
                "running": True,
                "task_type": request.task_type,
                "interval_seconds": interval_seconds,
                "frame_count": frame_count,
                "sample_interval": sample_interval,
                "use_mock_frame": request.use_mock_frame,
                "source_ids": [source["id"] for source in sources],
                "started_at": now_text(),
                "stopped_at": "",
                "last_round_at": "",
                "next_round_after": "启动后立即执行",
                "rounds_completed": 0,
                "total_records_created": 0,
                "total_alerts_created": 0,
                "source_status": {
                    source["id"]: {
                        "source_id": source["id"],
                        "source_name": source["name"],
                        "url": source["url"],
                        "status": "waiting",
                        "last_started_at": "",
                        "last_finished_at": "",
                        "last_error": "",
                        "last_result": None,
                        "last_record_id": None,
                        "last_alert_id": None,
                        "last_plate_count": 0,
                    }
                    for source in sources
                },
                "recent_events": [],
            }
        )

    config = {
        "sources": sources,
        "task_type": request.task_type,
        "interval_seconds": interval_seconds,
        "frame_count": frame_count,
        "sample_interval": sample_interval,
        "use_mock_frame": request.use_mock_frame,
    }

    MONITOR_THREAD = threading.Thread(
        target=monitor_worker,
        args=(config,),
        daemon=True,
        name="global_rtsp_monitor_worker",
    )
    MONITOR_THREAD.start()

    insert_operation_log(
        action="global_monitor_started",
        detail={
            "task_type": request.task_type,
            "source_ids": [source["id"] for source in sources],
            "interval_seconds": interval_seconds,
            "frame_count": frame_count,
            "sample_interval": sample_interval,
            "use_mock_frame": request.use_mock_frame,
        },
    )

    return {
        "status": "success",
        "message": "全局自动识别监控已启动",
        "monitor": get_monitor_state_snapshot(),
    }


@app.post("/api/monitor/stop")
def stop_global_monitor():
    global MONITOR_THREAD

    with MONITOR_LOCK:
        was_running = MONITOR_THREAD is not None and MONITOR_THREAD.is_alive()

    if was_running:
        MONITOR_STOP_EVENT.set()
        MONITOR_THREAD.join(timeout=3)

    with MONITOR_LOCK:
        MONITOR_STATE["running"] = False
        MONITOR_STATE["stopped_at"] = now_text()
        MONITOR_STATE["next_round_after"] = "已停止"

    insert_operation_log(
        action="global_monitor_stopped",
        detail={"was_running": was_running},
    )

    return {
        "status": "success",
        "message": "全局自动识别监控已停止" if was_running else "全局自动识别监控当前未运行",
        "monitor": get_monitor_state_snapshot(),
    }


@app.get("/api/monitor/status")
def get_global_monitor_status():
    return {
        "status": "success",
        "monitor": get_monitor_state_snapshot(),
    }


@app.post("/api/plate/image")
def recognize_plate_image(
    file: UploadFile = File(...),
    simulate_low_confidence: bool = Query(False, description="是否模拟低置信度识别结果"),
):
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    original_name = file.filename or ""
    suffix = Path(original_name).suffix.lower()

    if suffix not in allowed_suffixes:
        insert_operation_log(
            action="upload_image_failed",
            detail={
                "filename": original_name,
                "reason": "unsupported_file_type",
            },
        )
        raise HTTPException(
            status_code=400,
            detail="只支持 jpg、jpeg、png、bmp、webp 格式图片"
        )

    saved_filename = f"{uuid4().hex}{suffix}"
    saved_path = UPLOAD_DIR / saved_filename

    with saved_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    image_url = f"/uploads/{saved_filename}"

    output_filename = f"annotated_{saved_filename}"
    output_path = OUTPUT_DIR / output_filename
    output_image_url = f"/outputs/{output_filename}"

    confidence = 0.42 if simulate_low_confidence else 0.92

    recognition_result = create_annotated_plate_image(
        input_path=saved_path,
        output_path=output_path,
        confidence=confidence,
    )

    record_id = insert_recognition_record(
        task_type="plate",
        input_type="image",
        original_filename=original_name,
        saved_filename=saved_filename,
        image_url=image_url,
        output_image_url=output_image_url,
        result=recognition_result,
    )

    insert_operation_log(
        action="plate_image_recognition",
        detail={
            "record_id": record_id,
            "original_filename": original_name,
            "saved_filename": saved_filename,
            "simulate_low_confidence": simulate_low_confidence,
        },
    )

    alert_id = maybe_create_low_confidence_alert(
        record_id=record_id,
        result=recognition_result,
    )

    return {
        "status": "success",
        "record_id": record_id,
        "alert_id": alert_id,
        "task_type": "plate",
        "input_type": "image",
        "original_filename": original_name,
        "saved_filename": saved_filename,
        "image_url": image_url,
        "output_image_url": output_image_url,
        "result": recognition_result,
    }


@app.post("/api/gesture/owner/simulate")
def simulate_owner_gesture(request: GestureRequest):
    """
    车主手势模拟接口：
    保留按钮模拟功能，用于前端快速演示和对照测试。
    """
    result = apply_owner_gesture(request.gesture)

    record_id = insert_recognition_record(
        task_type="owner_gesture",
        input_type="simulate",
        original_filename="",
        saved_filename="",
        image_url="",
        output_image_url="",
        result=result,
    )

    insert_operation_log(
        action="owner_gesture_control",
        detail={
            "record_id": record_id,
            "gesture": result["gesture"],
            "gesture_name": result["gesture_name"],
            "action": result["action"],
            "description": result["description"],
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "result": result,
    }


@app.post("/api/gesture/owner/image")
def recognize_owner_gesture_from_image(
    file: UploadFile = File(...),
    apply_control: bool = Query(True, description="是否将识别到的手势映射为车辆控制动作"),
):
    """
    车主手势图片 AI 识别接口：
    1. 上传手势图片
    2. 使用 MediaPipe Hands 检测 21 个手部关键点
    3. 根据关键点分类手势
    4. 生成手部骨架标注图
    5. 可选：将识别到的手势映射为车辆控制动作
    6. 写入历史记录、操作日志和低置信度告警
    """
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    original_name = file.filename or ""
    suffix = Path(original_name).suffix.lower()

    if suffix not in allowed_suffixes:
        insert_operation_log(
            action="owner_gesture_image_failed",
            detail={
                "filename": original_name,
                "reason": "unsupported_file_type",
            },
        )
        raise HTTPException(
            status_code=400,
            detail="只支持 jpg、jpeg、png、bmp、webp 格式图片",
        )

    saved_filename = f"owner_gesture_{uuid4().hex}{suffix}"
    saved_path = UPLOAD_DIR / saved_filename

    with saved_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    image_url = f"/uploads/{saved_filename}"

    output_filename = f"annotated_{saved_filename}"
    output_path = OUTPUT_DIR / output_filename
    output_image_url = f"/outputs/{output_filename}"

    try:
        ai_result = recognize_owner_gesture_image(
            input_path=saved_path,
            output_path=output_path,
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"车主手势 AI 识别失败：{error}",
        )

    gesture = ai_result.get("gesture", "unknown")

    if apply_control and gesture != "unknown":
        control_result = apply_owner_gesture(gesture)

        ai_result["action"] = control_result["action"]
        ai_result["description"] = control_result["description"]
        ai_result["vehicle_state"] = control_result["vehicle_state"]
    else:
        ai_result["action"] = "none"
        ai_result["description"] = "未执行车辆控制动作"
        ai_result["vehicle_state"] = get_vehicle_state()

    record_id = insert_recognition_record(
        task_type="owner_gesture",
        input_type="image",
        original_filename=original_name,
        saved_filename=saved_filename,
        image_url=image_url,
        output_image_url=output_image_url,
        result=ai_result,
    )

    alert_id = None

    if ai_result.get("confidence", 0) < 0.6:
        alert_id = insert_alert_event(
            level="warning",
            event_type="owner_gesture_low_confidence",
            summary="车主手势识别置信度偏低",
            reason=f"本次手势识别置信度为 {ai_result.get('confidence', 0):.2f}，低于系统阈值 0.60。",
            suggestion="建议调整手部姿态、摄像头角度和光照条件，或使用更清晰的手势图片。",
            related_record_id=record_id,
        )

    insert_operation_log(
        action="owner_gesture_image_recognition",
        detail={
            "record_id": record_id,
            "alert_id": alert_id,
            "original_filename": original_name,
            "gesture": ai_result.get("gesture"),
            "gesture_name": ai_result.get("gesture_name"),
            "confidence": ai_result.get("confidence"),
            "apply_control": apply_control,
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "alert_id": alert_id,
        "task_type": "owner_gesture",
        "input_type": "image",
        "original_filename": original_name,
        "saved_filename": saved_filename,
        "image_url": image_url,
        "output_image_url": output_image_url,
        "result": ai_result,
    }


@app.get("/api/vehicle/state")
def get_current_vehicle_state():
    return {
        "status": "success",
        "state": get_vehicle_state(),
    }


@app.post("/api/gesture/traffic/simulate")
def simulate_traffic_gesture(request: GestureRequest):
    """
    交警手势模拟接口：
    保留按钮模拟功能，用于前端快速演示和对照测试。
    """
    result = recognize_traffic_gesture(request.gesture)

    record_id = insert_recognition_record(
        task_type="traffic_gesture",
        input_type="simulate",
        original_filename="",
        saved_filename="",
        image_url="",
        output_image_url="",
        result=result,
    )

    insert_operation_log(
        action="traffic_gesture_recognition",
        detail={
            "record_id": record_id,
            "gesture": result["gesture"],
            "gesture_name": result["gesture_name"],
            "traffic_command": result["traffic_command"],
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "result": result,
    }


@app.post("/api/gesture/traffic/image")
def recognize_traffic_gesture_from_image(
    file: UploadFile = File(...),
):
    """
    交警手势图片 AI 识别接口：
    1. 上传交警手势图片
    2. 使用 MediaPipe Pose 检测人体姿态关键点
    3. 根据人体关键点规则分类 8 类交警手势
    4. 生成姿态骨架标注图
    5. 写入历史记录、操作日志和低置信度告警
    """
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    original_name = file.filename or ""
    suffix = Path(original_name).suffix.lower()

    if suffix not in allowed_suffixes:
        insert_operation_log(
            action="traffic_gesture_image_failed",
            detail={
                "filename": original_name,
                "reason": "unsupported_file_type",
            },
        )
        raise HTTPException(
            status_code=400,
            detail="只支持 jpg、jpeg、png、bmp、webp 格式图片",
        )

    saved_filename = f"traffic_gesture_{uuid4().hex}{suffix}"
    saved_path = UPLOAD_DIR / saved_filename

    with saved_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    image_url = f"/uploads/{saved_filename}"

    output_filename = f"annotated_{saved_filename}"
    output_path = OUTPUT_DIR / output_filename
    output_image_url = f"/outputs/{output_filename}"

    try:
        ai_result = recognize_traffic_gesture_image(
            input_path=saved_path,
            output_path=output_path,
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"交警手势 AI 识别失败：{error}",
        )

    record_id = insert_recognition_record(
        task_type="traffic_gesture",
        input_type="image",
        original_filename=original_name,
        saved_filename=saved_filename,
        image_url=image_url,
        output_image_url=output_image_url,
        result=ai_result,
    )

    alert_id = None

    if ai_result.get("confidence", 0) < 0.6:
        alert_id = insert_alert_event(
            level="warning",
            event_type="traffic_gesture_low_confidence",
            summary="交警手势识别置信度偏低",
            reason=f"本次交警手势识别置信度为 {ai_result.get('confidence', 0):.2f}，低于系统阈值 0.60。",
            suggestion="建议调整人体姿态、拍摄距离、摄像头角度和光照条件，或使用连续视频帧进行识别。",
            related_record_id=record_id,
        )

    insert_operation_log(
        action="traffic_gesture_image_recognition",
        detail={
            "record_id": record_id,
            "alert_id": alert_id,
            "original_filename": original_name,
            "gesture": ai_result.get("gesture"),
            "gesture_name": ai_result.get("gesture_name"),
            "traffic_command": ai_result.get("traffic_command"),
            "confidence": ai_result.get("confidence"),
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "alert_id": alert_id,
        "task_type": "traffic_gesture",
        "input_type": "image",
        "original_filename": original_name,
        "saved_filename": saved_filename,
        "image_url": image_url,
        "output_image_url": output_image_url,
        "result": ai_result,
    }



def normalize_alert_type(event_type: str, summary: str | None = None) -> str:
    """把底层告警类型归并成前端和报告里更容易理解的风险类型。"""
    text = f"{event_type or ''} {summary or ''}"

    if "plate" in text or "车牌" in text:
        return "车牌识别异常"
    if "traffic_gesture" in text or "交警" in text or "人体" in text or "姿态" in text:
        return "交警手势识别异常"
    if "owner_gesture" in text or "车主" in text or "手部" in text:
        return "车主手势识别异常"
    if "stream" in text or "RTSP" in text or "视频" in text:
        return "视频流识别异常"
    if "low_confidence" in text or "置信度" in text:
        return "低置信度告警"

    return "其他系统告警"


def build_alert_analysis(alerts: list[dict]) -> dict:
    total_count = len(alerts)
    unresolved_alerts = [item for item in alerts if item.get("status") == "未处理"]
    unresolved_count = len(unresolved_alerts)

    warning_count = sum(1 for item in alerts if item.get("level") == "warning")
    critical_count = sum(1 for item in alerts if item.get("level") == "critical")

    type_counter = Counter(
        normalize_alert_type(item.get("event_type", ""), item.get("summary", ""))
        for item in alerts
    )

    event_counter = Counter(item.get("event_type", "unknown") for item in alerts)

    if critical_count > 0 or unresolved_count >= 8:
        risk_level = "high"
        risk_level_name = "高风险"
    elif unresolved_count >= 3 or warning_count >= 5:
        risk_level = "medium"
        risk_level_name = "中风险"
    elif unresolved_count > 0:
        risk_level = "low"
        risk_level_name = "低风险"
    else:
        risk_level = "normal"
        risk_level_name = "正常"

    main_risk_types = [name for name, _ in type_counter.most_common(3)]
    main_event_types = [
        {"event_type": name, "count": count}
        for name, count in event_counter.most_common(5)
    ]
    risk_type_stats = [
        {"risk_type": name, "count": count}
        for name, count in type_counter.most_common()
    ]

    if total_count == 0:
        analysis = "当前系统暂无告警记录，识别链路未发现异常事件。"
    elif unresolved_count == 0:
        analysis = f"当前共有 {total_count} 条告警记录，均已处理，系统处于可控状态。"
    else:
        risk_text = "、".join(main_risk_types) if main_risk_types else "未分类告警"
        analysis = (
            f"当前共有 {total_count} 条告警记录，其中 {unresolved_count} 条未处理。"
            f"主要风险集中在：{risk_text}。"
        )

    suggestions = []

    if total_count == 0:
        suggestions.append("继续执行车牌、交警手势、车主手势和 RTSP 连续帧识别测试，积累运行数据。")
    else:
        if any("车牌" in item for item in main_risk_types):
            suggestions.append("车牌识别异常较多时，优先检查沙盘车牌清晰度、摄像头角度、车辆距离和光照条件。")
            suggestions.append("若真实车牌可识别但沙盘车牌识别率低，后续应采集沙盘车牌数据并训练适配沙盘场景的检测模型。")
        if any("交警" in item for item in main_risk_types):
            suggestions.append("交警手势识别需要画面中包含清晰的人体上半身或全身姿态，建议切换到交警指挥区域摄像头或使用专门演示视频源。")
        if any("车主" in item for item in main_risk_types):
            suggestions.append("车主手势识别需要手部近景画面，建议使用车内摄像头、电脑摄像头或手势演示视频源。")
        if any("视频流" in item for item in main_risk_types):
            suggestions.append("视频流异常时，检查 RTSP 地址、沙盘推流状态、网络连通性和帧读取参数。")
        if unresolved_count > 0:
            suggestions.append("建议优先处理未处理告警，并在处理后点击“标记处理”关闭告警事件。")

    if not suggestions:
        suggestions.append("当前告警风险较低，建议继续保持日志记录和周期性检查。")

    return {
        "total_count": total_count,
        "unresolved_count": unresolved_count,
        "warning_count": warning_count,
        "critical_count": critical_count,
        "risk_level": risk_level,
        "risk_level_name": risk_level_name,
        "main_risk_types": main_risk_types,
        "risk_type_stats": risk_type_stats,
        "main_event_types": main_event_types,
        "analysis": analysis,
        "suggestions": suggestions,
        "generated_at": now_text(),
    }

RECORD_SELECT_COLUMNS = """
    r.id,
    r.user_id,
    u.username,
    u.email AS user_email,
    r.task_type,
    r.input_type,
    r.original_filename,
    r.saved_filename,
    r.image_url,
    r.output_image_url,
    r.result_json,
    r.created_at
"""


@app.get("/api/records")
def list_recognition_records(
    limit: int = Query(30, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """普通用户仅查看自己的识别记录。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT {RECORD_SELECT_COLUMNS}
        FROM recognition_records r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.user_id = ?
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (int(current_user["id"]), int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    records = [row_to_record(row) for row in rows]
    return {
        "status": "success",
        "scope": "current_user",
        "total": len(records),
        "records": records,
    }


@app.get("/api/records/{record_id}")
def get_recognition_record_detail(
    record_id: int,
    current_user: dict = Depends(get_current_user),
):
    """查看完整识别结果；普通用户只能访问自己的记录，管理员可访问全部。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    row = cursor.execute(
        f"""
        SELECT {RECORD_SELECT_COLUMNS}
        FROM recognition_records r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.id = ?
        """,
        (int(record_id),),
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="识别记录不存在")

    if (
        current_user.get("role") != "admin"
        and int(row["user_id"] or 0) != int(current_user["id"])
    ):
        raise HTTPException(status_code=403, detail="无权查看其他用户的识别记录")

    return {
        "status": "success",
        "record": row_to_record(row),
    }


@app.get("/api/admin/records")
def list_admin_recognition_records(
    limit: int = Query(100, ge=1, le=500),
    user_id: int | None = Query(None, ge=1),
    task_type: str | None = Query(None),
    keyword: str | None = Query(None),
    admin_user: dict = Depends(require_admin),
):
    """管理员查看全部用户的识别记录。"""
    conditions = []
    params: list = []

    if user_id is not None:
        conditions.append("r.user_id = ?")
        params.append(int(user_id))
    if task_type:
        conditions.append("r.task_type = ?")
        params.append(task_type.strip())
    if keyword:
        keyword_value = f"%{keyword.strip()}%"
        conditions.append(
            "(u.username LIKE ? OR u.email LIKE ? OR r.original_filename LIKE ?)"
        )
        params.extend([keyword_value, keyword_value, keyword_value])

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT {RECORD_SELECT_COLUMNS}
        FROM recognition_records r
        LEFT JOIN users u ON u.id = r.user_id
        {where_sql}
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    records = [row_to_record(row) for row in rows]
    return {
        "status": "success",
        "scope": "admin_global",
        "total": len(records),
        "records": records,
    }


@app.get("/api/admin/users")
def list_admin_users(
    limit: int = Query(200, ge=1, le=500),
    role: str | None = Query(None),
    keyword: str | None = Query(None),
    admin_user: dict = Depends(require_admin),
):
    """管理员用户列表，附带每个账号的业务数据数量。"""
    conditions = []
    params: list = []

    if role:
        normalized_role = role.strip().lower()
        if normalized_role not in {"user", "admin"}:
            raise HTTPException(status_code=400, detail="role 只支持 user 或 admin")
        conditions.append("u.role = ?")
        params.append(normalized_role)

    if keyword:
        keyword_value = f"%{keyword.strip()}%"
        conditions.append("(u.username LIKE ? OR u.email LIKE ?)")
        params.extend([keyword_value, keyword_value])

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            u.id,
            u.username,
            u.email,
            u.role,
            u.created_at,
            (
                SELECT COUNT(*)
                FROM recognition_records r
                WHERE r.user_id = u.id
            ) AS recognition_count,
            (
                SELECT COUNT(*)
                FROM alert_events a
                WHERE a.user_id = u.id
            ) AS alert_count,
            (
                SELECT COUNT(*)
                FROM operation_logs l
                WHERE l.user_id = u.id
            ) AS operation_count
        FROM users u
        {where_sql}
        ORDER BY u.id ASC
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()

    users = [
        {
            "id": int(row["id"]),
            "username": row["username"],
            "email": row["email"],
            "role": row["role"],
            "status": "正常",
            "enabled": True,
            "created_at": row["created_at"],
            "recognition_count": int(row["recognition_count"] or 0),
            "alert_count": int(row["alert_count"] or 0),
            "operation_count": int(row["operation_count"] or 0),
        }
        for row in rows
    ]
    return {
        "status": "success",
        "total": len(users),
        "users": users,
    }


ALERT_SELECT_COLUMNS = """
    a.id,
    a.user_id,
    u.username,
    u.email AS user_email,
    a.level,
    a.event_type,
    a.summary,
    a.reason,
    a.suggestion,
    a.status,
    a.related_record_id,
    a.created_at,
    (
        SELECT COUNT(*)
        FROM alert_notifications n
        WHERE n.alert_id = a.id
    ) AS email_total_count,
    (
        SELECT COUNT(*)
        FROM alert_notifications n
        WHERE n.alert_id = a.id AND n.status = 'sent'
    ) AS email_sent_count,
    (
        SELECT COUNT(*)
        FROM alert_notifications n
        WHERE n.alert_id = a.id AND n.status = 'failed'
    ) AS email_failed_count,
    (
        SELECT COUNT(*)
        FROM alert_notifications n
        WHERE n.alert_id = a.id
          AND n.status IN ('pending', 'processing', 'retrying')
    ) AS email_pending_count,
    (
        SELECT COUNT(*)
        FROM alert_notifications n
        WHERE n.alert_id = a.id AND n.status = 'skipped'
    ) AS email_skipped_count
"""


@app.get("/api/alerts")
def list_alert_events(
    limit: int = Query(30, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """普通用户仅查看自己的告警记录。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT {ALERT_SELECT_COLUMNS}
        FROM alert_events a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.user_id = ?
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (int(current_user["id"]), int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    alerts = [row_to_alert(row) for row in rows]
    return {
        "status": "success",
        "scope": "current_user",
        "total": len(alerts),
        "alerts": alerts,
    }


@app.get("/api/admin/alerts")
def list_admin_alert_events(
    limit: int = Query(100, ge=1, le=500),
    user_id: int | None = Query(None, ge=1),
    admin_user: dict = Depends(require_admin),
):
    conditions = []
    params: list = []
    if user_id is not None:
        conditions.append("a.user_id = ?")
        params.append(int(user_id))

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT {ALERT_SELECT_COLUMNS}
        FROM alert_events a
        LEFT JOIN users u ON u.id = a.user_id
        {where_sql}
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    alerts = [row_to_alert(row) for row in rows]
    return {
        "status": "success",
        "scope": "admin_global",
        "total": len(alerts),
        "alerts": alerts,
    }



def _ensure_alert_access(
    alert_id: int,
    current_user: dict,
) -> sqlite3.Row:
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, user_id
        FROM alert_events
        WHERE id = ?
        """,
        (int(alert_id),),
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="告警不存在")

    if (
        current_user.get("role") != "admin"
        and int(row["user_id"] or 0) != int(current_user["id"])
    ):
        raise HTTPException(status_code=403, detail="无权访问其他用户的告警通知")

    return row


@app.get("/api/alert-notifications/config")
def get_alert_email_config(
    current_user: dict = Depends(get_current_user),
):
    return {
        "status": "success",
        "config": alert_email_config_summary(),
    }


@app.get("/api/alert-notifications/{alert_id}")
def get_alert_email_notifications(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
):
    _ensure_alert_access(alert_id, current_user)

    return {
        "status": "success",
        "alert_id": int(alert_id),
        "summary": get_alert_notification_summary(DB_PATH, alert_id),
        "notifications": list_alert_notifications(
            DB_PATH,
            alert_id=alert_id,
            limit=100,
        ),
    }


@app.post("/api/alert-notifications/{alert_id}/retry")
def retry_alert_email(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
):
    _ensure_alert_access(alert_id, current_user)
    retry_count = retry_alert_notifications(DB_PATH, alert_id)

    insert_operation_log(
        action="retry_alert_email",
        detail={
            "alert_id": int(alert_id),
            "retry_count": retry_count,
        },
    )

    return {
        "status": "success",
        "alert_id": int(alert_id),
        "retry_count": retry_count,
        "message": (
            "失败邮件已重新加入发送队列"
            if retry_count > 0
            else "当前没有可重试的失败邮件"
        ),
    }


@app.get("/api/admin/alert-notifications")
def list_admin_alert_email_notifications(
    limit: int = Query(200, ge=1, le=1000),
    admin_user: dict = Depends(require_admin),
):
    items = list_alert_notifications(
        DB_PATH,
        limit=limit,
    )

    return {
        "status": "success",
        "total": len(items),
        "items": items,
    }


@app.post("/api/admin/alert-notifications/retry-failed")
def retry_all_failed_alert_emails(
    admin_user: dict = Depends(require_admin),
):
    retry_count = retry_all_failed_notifications(DB_PATH)

    insert_operation_log(
        action="retry_all_failed_alert_emails",
        detail={
            "retry_count": retry_count,
        },
    )

    return {
        "status": "success",
        "retry_count": retry_count,
        "message": (
            f"已重新排队 {retry_count} 封失败邮件"
            if retry_count > 0
            else "当前没有失败邮件"
        ),
    }


@app.get("/api/alerts/analysis")
def analyze_alert_events(limit: int = 100):
    """
    告警智能分析接口：
    读取最近告警记录，归并风险类型，计算风险等级，并生成处理建议。
    当前版本采用规则推理，后续可替换为大模型或更复杂的告警智能体。
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit 必须大于 0")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            level,
            event_type,
            summary,
            reason,
            suggestion,
            status,
            related_record_id,
            created_at
        FROM alert_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()
    conn.close()

    alerts = [row_to_alert(row) for row in rows]
    analysis = build_alert_analysis(alerts)

    insert_operation_log(
        action="analyze_alert_events",
        detail={
            "limit": limit,
            "total_count": analysis["total_count"],
            "unresolved_count": analysis["unresolved_count"],
            "risk_level": analysis["risk_level"],
        },
    )

    return {
        "status": "success",
        "analysis": analysis,
    }


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
):
    conn = get_db_connection()
    cursor = conn.cursor()
    existing = cursor.execute(
        """
        SELECT id, user_id
        FROM alert_events
        WHERE id = ?
        """,
        (int(alert_id),),
    ).fetchone()

    if existing is None:
        conn.close()
        raise HTTPException(status_code=404, detail="告警不存在")

    if (
        current_user.get("role") != "admin"
        and int(existing["user_id"] or 0) != int(current_user["id"])
    ):
        conn.close()
        raise HTTPException(status_code=403, detail="无权处理其他用户的告警")

    cursor.execute(
        """
        UPDATE alert_events
        SET status = '已处理'
        WHERE id = ?
        """,
        (int(alert_id),),
    )
    conn.commit()
    conn.close()

    insert_operation_log(
        action="resolve_alert",
        detail={"alert_id": int(alert_id)},
    )

    return {
        "status": "success",
        "alert_id": int(alert_id),
        "message": "告警已标记为已处理",
    }


# ---------------------------------------------------------------------------
# AlertAgent 集成端点
# ---------------------------------------------------------------------------

def _alert_agent_evaluate_from_snapshot(snapshot: dict) -> list[dict]:
    """使用 AlertAgent 评估系统快照，返回告警列表，并写入数据库。"""
    alerts: list[Alert] = _ALERT_AGENT.evaluate(snapshot)
    result: list[dict] = []
    for alert in alerts:
        db_id = insert_alert_event(
            level=alert.level,
            event_type=alert.event_type,
            summary=alert.summary,
            reason=alert.reason,
            suggestion=alert.suggestion,
            related_record_id=alert.related_record_id,
        )
        d = _ALERT_AGENT.to_dict(alert)
        d["db_id"] = db_id
        result.append(d)
    return result


def _broadcast_alert_sse(alert_data: dict) -> None:
    """向所有 SSE 客户端广播新告警。"""
    with _ALERT_SSE_LOCK:
        for q in _ALERT_SSE_QUEUES[:]:
            try:
                q.put_nowait(alert_data)
            except Exception:
                pass


@app.post("/api/alerts/agent/evaluate")
def alert_agent_evaluate(payload: dict | None = Body(None)):
    """
    手动触发 AlertAgent 评估。

    两种用法：
    1. 不传 body → 自动从 DB 识别记录 + 监控状态构建快照
    2. 传 JSON body → 直接用你提供的数据评估（不写 DB，只返回结果）

    Body 字段（全部可选，按需填）：
    {
      "performance": {"latency_ms": 150},
      "plate": {
        "available": true, "input_type": "rtsp_stream",
        "plate_count": 0, "plates": [],
        "consecutive_empty_count": 12
      },
      "traffic_gesture": {
        "available": true, "gesture": "stop",
        "gesture_name": "停止", "confidence": 0.92
      },
      "owner_gesture": {
        "available": true, "gesture": "go_straight",
        "gesture_name": "直行", "confidence": 0.88
      }
    }
    """
    try:
        snapshot: dict = {}

        if payload:
            # 用户自定义测试数据
            snapshot = {
                "performance": payload.get("performance", {}),
                "plate": payload.get("plate", {}),
                "traffic_gesture": payload.get("traffic_gesture", {}),
                "owner_gesture": payload.get("owner_gesture", {}),
            }
        else:
            # 自动采集真实数据
            snapshot = get_monitor_state_snapshot()
            snapshot.setdefault("plate", {})
            snapshot.setdefault("traffic_gesture", {})
            snapshot.setdefault("owner_gesture", {})
            snapshot.setdefault("performance", {})

            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT task_type, input_type, result_json, created_at "
                    "FROM recognition_records ORDER BY id DESC LIMIT 50"
                )
                rows = cursor.fetchall()
                empty_count = 0
                latest_perf = {}
                for row in rows:
                    try:
                        result = json.loads(row["result_json"]) if row["result_json"] else {}
                    except Exception:
                        result = {}
                    task = row["task_type"] or ""
                    if "plate" in task and not snapshot["plate"].get("plates"):
                        plates = result.get("plates") or []
                        plate_count = result.get("plate_count", len(plates))
                        if plate_count == 0:
                            empty_count += 1
                        snapshot["plate"] = {
                            "available": True,
                            "input_type": row["input_type"] or "",
                            "plate_count": plate_count,
                            "plates": plates,
                            "consecutive_empty_count": empty_count if plate_count == 0 else 0,
                        }
                        latest_perf["plate_latency"] = result.get("latency_ms")
                    elif "traffic" in task and not snapshot["traffic_gesture"].get("gesture"):
                        snapshot["traffic_gesture"] = {
                            "available": True,
                            "gesture": result.get("gesture", ""),
                            "gesture_name": result.get("gesture_name", ""),
                            "confidence": result.get("confidence", 0),
                        }
                        latest_perf["traffic_latency"] = result.get("latency_ms")
                    elif "owner" in task and not snapshot["owner_gesture"].get("gesture"):
                        snapshot["owner_gesture"] = {
                            "available": True,
                            "gesture": result.get("gesture", ""),
                            "gesture_name": result.get("gesture_name", ""),
                            "confidence": result.get("confidence", 0),
                        }
                        latest_perf["owner_latency"] = result.get("latency_ms")
                snapshot.setdefault("performance", latest_perf)
            finally:
                conn.close()

            if isinstance(snapshot, dict):
                last_round = snapshot.get("last_round_at")
                if last_round:
                    from datetime import datetime as _dt
                    try:
                        last_dt = _dt.strptime(last_round, "%Y-%m-%d %H:%M:%S")
                        idle_sec = (_dt.now() - last_dt).total_seconds()
                        snapshot["monitor_stats"] = {"seconds_since_last_recognition": idle_sec}
                    except Exception:
                        pass

        # 用户自定义数据：使用临时 AlertAgent，不写入 DB、不广播 SSE、不共享抑制器
        save_to_db = not bool(payload)

        if save_to_db:
            triggered = _alert_agent_evaluate_from_snapshot(snapshot)
            for alert_dict in triggered:
                _broadcast_alert_sse(alert_dict)
        else:
            temp_agent = AlertAgent(cooldown_seconds=0)  # 无冷却，每次测试独立
            triggered = [temp_agent.to_dict(a) for a in temp_agent.evaluate(snapshot)]

        for alert_dict in triggered:
            _broadcast_alert_sse(alert_dict)

        stats = _ALERT_AGENT.get_statistics()
        return {
            "status": "success",
            "triggered_count": len(triggered),
            "triggered": triggered,
            "stats": {
                "total": stats.total,
                "critical": stats.critical,
                "high": stats.high,
                "medium": stats.medium,
                "low": stats.low,
                "info": stats.info,
                "open": stats.open,
                "resolved": stats.resolved,
                "avg_per_minute": stats.avg_per_minute,
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AlertAgent 评估失败：{exc}")


@app.get("/api/alerts/agent/stats")
def alert_agent_stats():
    """获取 AlertAgent 告警统计。"""
    stats = _ALERT_AGENT.get_statistics()
    return {
        "status": "success",
        "stats": {
            "total": stats.total,
            "critical": stats.critical,
            "high": stats.high,
            "medium": stats.medium,
            "low": stats.low,
            "info": stats.info,
            "open": stats.open,
            "resolved": stats.resolved,
            "by_type": stats.by_type,
            "avg_per_minute": stats.avg_per_minute,
            "recent": _ALERT_AGENT.from_alert_list(stats.recent[-10:]),
        },
    }


class LlmConfigRequest(BaseModel):
    enabled: bool | None = None
    api_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.post("/api/alerts/agent/llm-config")
def alert_agent_llm_config(request: LlmConfigRequest):
    """配置 AlertAgent 的 LLM 摘要生成选项。"""
    config = _ALERT_AGENT.configure_llm(
        enabled=request.enabled,
        api_url=request.api_url,
        api_key=request.api_key,
        model=request.model,
    )
    return {
        "status": "success",
        "message": "LLM 配置已更新",
        "config": config,
    }


@app.get("/api/alerts/agent/llm-config")
def alert_agent_get_llm_config():
    """查看当前 LLM 摘要配置。"""
    config = _ALERT_AGENT.configure_llm()
    return {
        "status": "success",
        "config": config,
    }


@app.get("/api/alerts/stream")
async def alert_sse_stream():
    """
    SSE 实时告警推送。

    前端通过 EventSource 连接此端点，
    当 AlertAgent 检测到新告警时自动推送。
    """
    async def event_generator():
        q: queue.Queue = queue.Queue()
        with _ALERT_SSE_LOCK:
            _ALERT_SSE_QUEUES.append(q)

        try:
            # 发送初始连接确认
            yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE 连接成功'})}\n\n"

            while True:
                try:
                    alert_data = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: q.get(timeout=30)
                    )
                    payload = json.dumps({
                        "type": "alert",
                        "data": alert_data,
                    }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                except Exception:
                    # 30 秒超时发送心跳
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            with _ALERT_SSE_LOCK:
                if q in _ALERT_SSE_QUEUES:
                    _ALERT_SSE_QUEUES.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


LOG_SELECT_COLUMNS = """
    l.id,
    l.user_id,
    u.username,
    u.email AS user_email,
    l.action,
    l.detail,
    l.created_at
"""


@app.get("/api/logs")
def list_operation_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """普通用户仅查看自己的操作日志。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT {LOG_SELECT_COLUMNS}
        FROM operation_logs l
        LEFT JOIN users u ON u.id = l.user_id
        WHERE l.user_id = ?
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (int(current_user["id"]), int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    logs = [row_to_log(row) for row in rows]
    return {
        "status": "success",
        "scope": "current_user",
        "total": len(logs),
        "logs": logs,
    }


@app.get("/api/admin/logs")
def list_admin_operation_logs(
    limit: int = Query(100, ge=1, le=500),
    user_id: int | None = Query(None, ge=1),
    admin_user: dict = Depends(require_admin),
):
    conditions = []
    params: list = []
    if user_id is not None:
        conditions.append("l.user_id = ?")
        params.append(int(user_id))

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT {LOG_SELECT_COLUMNS}
        FROM operation_logs l
        LEFT JOIN users u ON u.id = l.user_id
        {where_sql}
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    rows = cursor.fetchall()
    conn.close()
    logs = [row_to_log(row) for row in rows]
    return {
        "status": "success",
        "scope": "admin_global",
        "total": len(logs),
        "logs": logs,
    }


# OWNER_GESTURE_CONTROL_PATCH_V2
# 车主手势控车增强：车辆状态映射 + 上传视频识别接口
from datetime import datetime as _owner_datetime
from pathlib import Path as _OwnerPath
from uuid import uuid4 as _owner_uuid4
import shutil as _owner_shutil

from fastapi import UploadFile as _OwnerUploadFile
from fastapi import File as _OwnerFile
from fastapi import Query as _OwnerQuery
from fastapi import HTTPException as _OwnerHTTPException


_OWNER_FUNCTIONS = ["home", "music", "air_conditioner", "phone", "navigation"]


_OWNER_GESTURE_NAMES = {
    "open_palm": "手掌张开",
    "fist": "握拳",
    "one": "单指",
    "two": "双指",
    "thumb_up": "拇指向上",
    "thumb_down": "拇指向下",
    "ok": "OK手势",
    "swipe_left": "左滑",
    "swipe_right": "右滑",
    "wave": "挥手",
    "circle": "单指画圈",
    "unknown": "未知手势",
}


_OWNER_DEFAULT_VEHICLE_STATE = {
    "system_awake": False,
    "current_function": "home",
    "volume": 50,
    "temperature": 24,
    "phone_status": "空闲",
    "updated_at": "",
}


def _owner_now_text() -> str:
    return _owner_datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_owner_vehicle_state() -> dict:
    """
    尽量复用 main.py 原有车辆状态变量。
    如果旧代码里有 vehicle_state 或 VEHICLE_STATE，就直接更新它。
    如果没有，就使用增强模块自己的默认状态。
    """
    global _OWNER_DEFAULT_VEHICLE_STATE

    old_state = globals().get("vehicle_state")
    if isinstance(old_state, dict):
        return old_state

    old_state_upper = globals().get("VEHICLE_STATE")
    if isinstance(old_state_upper, dict):
        return old_state_upper

    return _OWNER_DEFAULT_VEHICLE_STATE


def _next_function(current: str, step: int) -> str:
    if current not in _OWNER_FUNCTIONS:
        current = "home"
    index = _OWNER_FUNCTIONS.index(current)
    return _OWNER_FUNCTIONS[(index + step) % len(_OWNER_FUNCTIONS)]


def apply_owner_gesture(gesture: str) -> dict:
    """
    增强版车主手势到车辆控制操作映射。
    会覆盖旧版同名函数，旧的图片接口也会自动使用这套新映射。
    """
    state = _get_owner_vehicle_state()

    state.setdefault("system_awake", False)
    state.setdefault("current_function", "home")
    state.setdefault("volume", 50)
    state.setdefault("temperature", 24)
    state.setdefault("phone_status", "空闲")

    gesture_name = _OWNER_GESTURE_NAMES.get(gesture, "未知手势")
    action = "no_action"
    description = "未触发车辆控制操作"

    if gesture == "open_palm":
        state["system_awake"] = True
        action = "wake_system"
        description = "系统已唤醒"

    elif gesture in {"fist", "ok"}:
        action = "confirm_action"
        description = f"已确认当前功能：{state.get('current_function', 'home')}"

    elif gesture == "one":
        state["volume"] = min(100, int(state.get("volume", 50)) + 5)
        action = "volume_up"
        description = f"音量增加至 {state['volume']}"

    elif gesture == "two":
        state["volume"] = max(0, int(state.get("volume", 50)) - 5)
        action = "volume_down"
        description = f"音量降低至 {state['volume']}"

    elif gesture == "circle":
        state["volume"] = min(100, int(state.get("volume", 50)) + 10)
        action = "adjust_volume"
        description = f"单指画圈调节音量，当前音量 {state['volume']}"

    elif gesture == "thumb_up":
        state["phone_status"] = "通话中"
        state["current_function"] = "phone"
        action = "answer_call"
        description = "已接听电话"

    elif gesture == "thumb_down":
        state["phone_status"] = "已挂断"
        state["current_function"] = "phone"
        action = "hang_up_call"
        description = "已挂断电话"

    elif gesture == "swipe_left":
        state["current_function"] = _next_function(str(state.get("current_function", "home")), -1)
        action = "previous_function"
        description = f"已切换到上一个功能：{state['current_function']}"

    elif gesture == "swipe_right":
        state["current_function"] = _next_function(str(state.get("current_function", "home")), 1)
        action = "next_function"
        description = f"已切换到下一个功能：{state['current_function']}"

    elif gesture == "wave":
        state["current_function"] = "home"
        action = "back_home"
        description = "已返回主页"

    state["updated_at"] = _owner_now_text()

    return {
        "gesture": gesture,
        "gesture_name": gesture_name,
        "action": action,
        "description": description,
        "vehicle_state": dict(state),
    }


@app.post("/api/gesture/owner/video")
def recognize_owner_gesture_from_video(
    file: _OwnerUploadFile = _OwnerFile(...),
    apply_control: bool = _OwnerQuery(True, description="是否将识别到的手势映射为车辆控制动作"),
    frame_sample_interval: int = _OwnerQuery(3, ge=1, le=30, description="视频抽帧间隔"),
    stable_threshold: int = _OwnerQuery(3, ge=1, le=20, description="误触发抑制阈值：至少多少帧确认后触发"),
):
    """
    车主手势视频识别接口：
    1. 上传 mp4 / avi / mov / mkv / webm
    2. 后端抽帧识别手势
    3. 静态手势做多帧投票
    4. 动态手势做轨迹判断
    5. 达到阈值后才触发车辆控制操作
    """
    from algorithm.owner_gesture_recognizer import recognize_owner_gesture_video

    allowed_suffixes = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    original_name = file.filename or ""
    suffix = _OwnerPath(original_name).suffix.lower()

    if suffix not in allowed_suffixes:
        insert_operation_log(
            action="owner_gesture_video_failed",
            detail={
                "filename": original_name,
                "reason": "unsupported_file_type",
            },
        )
        raise _OwnerHTTPException(
            status_code=400,
            detail="只支持 mp4、avi、mov、mkv、webm 格式视频",
        )

    saved_filename = f"owner_gesture_video_{_owner_uuid4().hex}{suffix}"
    saved_path = UPLOAD_DIR / saved_filename

    with saved_path.open("wb") as buffer:
        _owner_shutil.copyfileobj(file.file, buffer)

    image_url = f"/uploads/{saved_filename}"

    output_filename = f"annotated_owner_gesture_video_{_OwnerPath(saved_filename).stem}.jpg"
    output_path = OUTPUT_DIR / output_filename
    output_image_url = f"/outputs/{output_filename}"

    try:
        recognition_result = recognize_owner_gesture_video(
            input_path=saved_path,
            output_path=output_path,
            frame_sample_interval=frame_sample_interval,
            stable_threshold=stable_threshold,
        )
    except Exception as exc:
        insert_operation_log(
            action="owner_gesture_video_failed",
            detail={
                "filename": original_name,
                "saved_filename": saved_filename,
                "reason": str(exc),
            },
        )
        raise _OwnerHTTPException(
            status_code=500,
            detail=f"车主手势视频识别失败：{exc}",
        )

    control_result = {}
    if apply_control and recognition_result.get("triggered") and recognition_result.get("gesture") != "unknown":
        control_result = apply_owner_gesture(recognition_result["gesture"])
        recognition_result.update({
            "action": control_result.get("action"),
            "description": control_result.get("description"),
            "vehicle_state": control_result.get("vehicle_state"),
        })
    else:
        recognition_result.update({
            "action": "no_action",
            "description": recognition_result.get("trigger_reason", "未触发车辆控制操作"),
            "vehicle_state": dict(_get_owner_vehicle_state()),
        })

    record_id = insert_recognition_record(
        task_type="owner_gesture",
        input_type="video",
        original_filename=original_name,
        saved_filename=saved_filename,
        image_url=image_url,
        output_image_url=output_image_url,
        result=recognition_result,
    )

    insert_operation_log(
        action="owner_gesture_video_recognition",
        detail={
            "record_id": record_id,
            "original_filename": original_name,
            "saved_filename": saved_filename,
            "gesture": recognition_result.get("gesture"),
            "gesture_name": recognition_result.get("gesture_name"),
            "triggered": recognition_result.get("triggered"),
            "action": recognition_result.get("action"),
            "frame_sample_interval": frame_sample_interval,
            "stable_threshold": stable_threshold,
        },
    )

    return {
        "status": "success",
        "record_id": record_id,
        "alert_id": None,
        "task_type": "owner_gesture",
        "input_type": "video",
        "original_filename": original_name,
        "saved_filename": saved_filename,
        "image_url": image_url,
        "output_image_url": output_image_url,
        "result": recognition_result,
    }


# FUSION_DECISION_AGENT_PATCH_V1
# 融合决策智能体接口：跨模块综合车牌、交警手势、车主手势识别结果
from pathlib import Path as _FusionPath
from datetime import datetime as _FusionDateTime
import sqlite3 as _fusion_sqlite3
import json as _fusion_json

from fastapi import Body as _FusionBody
from fastapi import Query as _FusionQuery
from fastapi import HTTPException as _FusionHTTPException


def _fusion_db_path() -> _FusionPath:
    db_path = globals().get("DB_PATH")
    if db_path:
        return _FusionPath(db_path)
    return _FusionPath(__file__).resolve().parent / "data" / "app.db"


def _fusion_connect():
    db_path = _fusion_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _fusion_sqlite3.connect(str(db_path))
    conn.row_factory = _fusion_sqlite3.Row
    return conn


def _fusion_now_text() -> str:
    return _FusionDateTime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fusion_safe_json_loads(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value

    try:
        return _fusion_json.loads(value)
    except Exception:
        return value


def _fusion_table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _fusion_get_columns(conn, table_name: str) -> list[str]:
    if not _fusion_table_exists(conn, table_name):
        return []
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def _fusion_init_table():
    with _fusion_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT UNIQUE NOT NULL,
                scenario TEXT,
                risk_level TEXT,
                risk_score INTEGER,
                suggestion TEXT,
                reason TEXT,
                decision_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _fusion_row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _fusion_parse_record(row_dict: dict) -> dict:
    if not row_dict:
        return {}

    result_value = None
    for key in ["result", "result_json", "recognition_result"]:
        if key in row_dict:
            result_value = _fusion_safe_json_loads(row_dict.get(key))
            break

    if result_value is None:
        result_value = {}

    if isinstance(result_value, str):
        result_value = {"raw": result_value}

    created_at = (
        row_dict.get("created_at")
        or row_dict.get("created_time")
        or row_dict.get("timestamp")
        or ""
    )

    return {
        "record_id": row_dict.get("id") or row_dict.get("record_id"),
        "id": row_dict.get("id") or row_dict.get("record_id"),
        "task_type": row_dict.get("task_type", ""),
        "input_type": row_dict.get("input_type", ""),
        "original_filename": row_dict.get("original_filename", ""),
        "saved_filename": row_dict.get("saved_filename", ""),
        "image_url": row_dict.get("image_url", ""),
        "output_image_url": row_dict.get("output_image_url", ""),
        "created_at": created_at,
        "result": result_value,
    }


def _fusion_fetch_latest_record(task_type: str) -> dict:
    with _fusion_connect() as conn:
        if not _fusion_table_exists(conn, "recognition_records"):
            return {}

        columns = _fusion_get_columns(conn, "recognition_records")
        if "task_type" not in columns:
            return {}

        order_column = "id" if "id" in columns else None
        if order_column:
            sql = "SELECT * FROM recognition_records WHERE task_type=? ORDER BY id DESC LIMIT 1"
        else:
            sql = "SELECT * FROM recognition_records WHERE task_type=? LIMIT 1"

        row = conn.execute(sql, (task_type,)).fetchone()
        return _fusion_parse_record(_fusion_row_to_dict(row))


def _fusion_fetch_recent_alerts(limit: int = 5) -> list[dict]:
    with _fusion_connect() as conn:
        if not _fusion_table_exists(conn, "alert_events"):
            return []

        columns = _fusion_get_columns(conn, "alert_events")
        order_sql = "ORDER BY id DESC" if "id" in columns else ""
        rows = conn.execute(f"SELECT * FROM alert_events {order_sql} LIMIT ?", (limit,)).fetchall()

        result = []
        for row in rows:
            item = _fusion_row_to_dict(row)
            for key in ["detail", "result", "payload"]:
                if key in item:
                    item[key] = _fusion_safe_json_loads(item[key])
            result.append(item)

        return result


def _fusion_build_latest_payload() -> dict:
    return {
        "plate": _fusion_fetch_latest_record("plate"),
        "traffic_gesture": _fusion_fetch_latest_record("traffic_gesture"),
        "owner_gesture": _fusion_fetch_latest_record("owner_gesture"),
        "alerts": _fusion_fetch_recent_alerts(limit=5),
        "performance": {},
    }


def _fusion_save_decision(decision: dict) -> int:
    _fusion_init_table()

    with _fusion_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO fusion_decisions (
                decision_id,
                scenario,
                risk_level,
                risk_score,
                suggestion,
                reason,
                decision_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.get("decision_id"),
                decision.get("scenario"),
                decision.get("risk_level"),
                int(decision.get("risk_score") or 0),
                decision.get("suggestion"),
                decision.get("reason"),
                _fusion_json.dumps(decision, ensure_ascii=False),
                decision.get("created_at") or _fusion_now_text(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _fusion_write_operation_log(action: str, detail: dict):
    log_func = globals().get("insert_operation_log")
    if callable(log_func):
        try:
            log_func(action=action, detail=detail)
        except Exception:
            pass


@app.get("/api/fusion/latest")
def get_fusion_latest():
    """
    获取最近一次车牌识别、交警手势识别、车主手势识别结果。
    用于融合决策前的证据汇总。
    """
    latest = _fusion_build_latest_payload()
    return {
        "status": "success",
        "latest": latest,
    }


@app.post("/api/fusion/decision")
def create_fusion_decision(
    payload: dict | None = _FusionBody(default=None),
    save: bool = _FusionQuery(True, description="是否保存融合决策记录"),
):
    """
    融合决策接口。

    用法：
    1. 不传 payload 或传空对象：自动读取数据库中最近的三类识别结果。
    2. 传 payload：使用调用方提供的车牌、交警手势、车主手势结果进行融合推理。
    """
    from algorithm.fusion_decision_agent import FusionDecisionAgent

    try:
        if not payload:
            payload = _fusion_build_latest_payload()
        elif "latest" in payload and isinstance(payload["latest"], dict):
            payload = payload["latest"]

        agent = FusionDecisionAgent()
        decision = agent.make_decision(payload)

        saved_id = None
        if save:
            saved_id = _fusion_save_decision(decision)

        _fusion_write_operation_log(
            action="fusion_decision",
            detail={
                "saved_id": saved_id,
                "decision_id": decision.get("decision_id"),
                "scenario": decision.get("scenario"),
                "risk_level": decision.get("risk_level"),
                "risk_score": decision.get("risk_score"),
            },
        )

        return {
            "status": "success",
            "saved_id": saved_id,
            "decision": decision,
        }

    except Exception as exc:
        _fusion_write_operation_log(
            action="fusion_decision_failed",
            detail={"reason": str(exc)},
        )
        raise _FusionHTTPException(status_code=500, detail=f"融合决策失败：{exc}")


@app.get("/api/fusion/history")
def get_fusion_history(
    limit: int = _FusionQuery(20, ge=1, le=100),
):
    """
    获取融合决策历史记录。
    """
    _fusion_init_table()

    with _fusion_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, decision_id, scenario, risk_level, risk_score,
                   suggestion, reason, decision_json, created_at
            FROM fusion_decisions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    items = []
    for row in rows:
        item = _fusion_row_to_dict(row)
        item["decision"] = _fusion_safe_json_loads(item.get("decision_json"))
        item.pop("decision_json", None)
        items.append(item)

    return {
        "status": "success",
        "total": len(items),
        "items": items,
    }


# PERFORMANCE_MONITOR_PATCH_V1
# 端到端实时性 / 延迟测试接口
from pathlib import Path as _PerfPath
from datetime import datetime as _PerfDateTime
import sqlite3 as _perf_sqlite3
import json as _perf_json
import time as _perf_time
import statistics as _perf_statistics

from fastapi import Body as _PerfBody
from fastapi import Query as _PerfQuery
from fastapi import HTTPException as _PerfHTTPException


def _perf_db_path() -> _PerfPath:
    db_path = globals().get("DB_PATH")
    if db_path:
        return _PerfPath(db_path)
    return _PerfPath(__file__).resolve().parent / "data" / "app.db"


def _perf_connect():
    db_path = _perf_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _perf_sqlite3.connect(str(db_path))
    conn.row_factory = _perf_sqlite3.Row
    return conn


def _perf_now_text() -> str:
    return _PerfDateTime.now().strftime("%Y-%m-%d %H:%M:%S")


def _perf_init_table():
    with _perf_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS performance_latency_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL,
                task_type TEXT,
                input_type TEXT,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                status_code INTEGER,
                success INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                threshold_ms INTEGER NOT NULL,
                is_realtime INTEGER NOT NULL,
                request_meta TEXT,
                response_meta TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_performance_latency_target
            ON performance_latency_records(target)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_performance_latency_created_at
            ON performance_latency_records(created_at)
            """
        )
        conn.commit()


def _perf_row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _perf_json_dumps(value) -> str:
    return _perf_json.dumps(value, ensure_ascii=False, default=str)


def _perf_json_loads(value):
    if not value:
        return {}
    try:
        return _perf_json.loads(value)
    except Exception:
        return {"raw": value}


def _perf_write_operation_log(action: str, detail: dict):
    log_func = globals().get("insert_operation_log")
    if callable(log_func):
        try:
            log_func(action=action, detail=detail)
        except Exception:
            pass


def _perf_save_record(record: dict) -> int:
    _perf_init_table()

    with _perf_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO performance_latency_records (
                target,
                task_type,
                input_type,
                endpoint,
                method,
                status_code,
                success,
                latency_ms,
                threshold_ms,
                is_realtime,
                request_meta,
                response_meta,
                error_message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("target"),
                record.get("task_type"),
                record.get("input_type"),
                record.get("endpoint"),
                record.get("method"),
                record.get("status_code"),
                1 if record.get("success") else 0,
                float(record.get("latency_ms") or 0),
                int(record.get("threshold_ms") or 1000),
                1 if record.get("is_realtime") else 0,
                _perf_json_dumps(record.get("request_meta") or {}),
                _perf_json_dumps(record.get("response_meta") or {}),
                record.get("error_message") or "",
                record.get("created_at") or _perf_now_text(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _perf_project_root() -> _PerfPath:
    return _PerfPath(__file__).resolve().parent.parent


def _perf_demo_path(filename: str) -> _PerfPath:
    return _perf_project_root() / "demo" / filename


def _perf_content_type(path: _PerfPath) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".avi":
        return "video/x-msvideo"
    if suffix == ".mov":
        return "video/quicktime"
    return "application/octet-stream"


def _perf_build_target_specs(payload: dict) -> dict:
    """
    测试目标配置。
    默认使用 demo 目录里的测试文件：
    - demo/test.png
    - demo/hand.jpg
    - demo/traffic.png
    - demo/owner_gesture.mp4 如果存在则测试视频手势
    """
    source_id = payload.get("source_id", "live12")
    frame_count = int(payload.get("frame_count", 20))
    sample_interval = int(payload.get("sample_interval", 5))
    stream_task_type = payload.get("stream_task_type", "plate")

    owner_frame_interval = int(payload.get("owner_frame_sample_interval", 3))
    owner_stable_threshold = int(payload.get("owner_stable_threshold", 3))

    return {
        "health": {
            "target": "health",
            "task_type": "system",
            "input_type": "api",
            "method": "GET",
            "endpoint": "/api/health",
        },
        "plate_image": {
            "target": "plate_image",
            "task_type": "plate",
            "input_type": "image",
            "method": "POST",
            "endpoint": "/api/plate/image",
            "file_path": _perf_demo_path("test.png"),
            "file_field": "file",
        },
        "owner_image": {
            "target": "owner_image",
            "task_type": "owner_gesture",
            "input_type": "image",
            "method": "POST",
            "endpoint": "/api/gesture/owner/image",
            "file_path": _perf_demo_path("hand.jpg"),
            "file_field": "file",
        },
        "owner_video": {
            "target": "owner_video",
            "task_type": "owner_gesture",
            "input_type": "video",
            "method": "POST",
            "endpoint": (
                "/api/gesture/owner/video"
                f"?frame_sample_interval={owner_frame_interval}"
                f"&stable_threshold={owner_stable_threshold}"
            ),
            "file_path": _perf_demo_path("owner_gesture.mp4"),
            "file_field": "file",
        },
        "traffic_image": {
            "target": "traffic_image",
            "task_type": "traffic_gesture",
            "input_type": "image",
            "method": "POST",
            "endpoint": "/api/gesture/traffic/image",
            "file_path": _perf_demo_path("traffic.png"),
            "file_field": "file",
        },
        "stream_mock": {
            "target": "stream_mock",
            "task_type": stream_task_type,
            "input_type": "mock_stream",
            "method": "POST",
            "endpoint": "/api/stream/recognize",
            "json": {
                "source_id": source_id,
                "task_type": stream_task_type,
                "frame_count": frame_count,
                "sample_interval": sample_interval,
                "use_mock_frame": True,
            },
        },
        "stream_rtsp": {
            "target": "stream_rtsp",
            "task_type": stream_task_type,
            "input_type": "rtsp_stream",
            "method": "POST",
            "endpoint": "/api/stream/recognize",
            "json": {
                "source_id": source_id,
                "task_type": stream_task_type,
                "frame_count": frame_count,
                "sample_interval": sample_interval,
                "use_mock_frame": False,
            },
        },
        "fusion_decision": {
            "target": "fusion_decision",
            "task_type": "fusion",
            "input_type": "latest_records",
            "method": "POST",
            "endpoint": "/api/fusion/decision?save=false",
            "json": {},
        },
    }


def _perf_compact_response_meta(response) -> dict:
    meta = {
        "status_code": response.status_code,
        "response_size_bytes": len(response.content or b""),
    }

    try:
        data = response.json()
    except Exception:
        data = None

    if isinstance(data, dict):
        meta["status"] = data.get("status")
        meta["record_id"] = data.get("record_id")
        meta["saved_id"] = data.get("saved_id")

        if isinstance(data.get("result"), dict):
            result = data["result"]
            meta["gesture"] = result.get("gesture")
            meta["gesture_name"] = result.get("gesture_name")
            meta["plate_count"] = result.get("plate_count")
            meta["triggered"] = result.get("triggered")

        if isinstance(data.get("decision"), dict):
            decision = data["decision"]
            meta["scenario"] = decision.get("scenario")
            meta["risk_level"] = decision.get("risk_level")
            meta["risk_score"] = decision.get("risk_score")

    return meta


def _perf_run_one_target(client, spec: dict, threshold_ms: int) -> dict:
    target = spec["target"]
    method = spec["method"].upper()
    endpoint = spec["endpoint"]

    request_meta = {
        "target": target,
        "endpoint": endpoint,
        "method": method,
    }

    file_path = spec.get("file_path")

    if file_path is not None:
        file_path = _PerfPath(file_path)
        request_meta["file_path"] = str(file_path)

        if not file_path.exists():
            record = {
                "target": target,
                "task_type": spec.get("task_type"),
                "input_type": spec.get("input_type"),
                "endpoint": endpoint,
                "method": method,
                "status_code": 0,
                "success": False,
                "latency_ms": 0,
                "threshold_ms": threshold_ms,
                "is_realtime": False,
                "request_meta": request_meta,
                "response_meta": {"skipped": True},
                "error_message": f"测试文件不存在：{file_path}",
                "created_at": _perf_now_text(),
            }
            record["id"] = _perf_save_record(record)
            record["skipped"] = True
            return record

    start = _perf_time.perf_counter()
    status_code = 0
    response_meta = {}
    error_message = ""
    success = False

    try:
        if method == "GET":
            response = client.get(endpoint)
        elif method == "POST" and file_path is not None:
            field = spec.get("file_field", "file")
            with file_path.open("rb") as file_obj:
                files = {
                    field: (
                        file_path.name,
                        file_obj,
                        _perf_content_type(file_path),
                    )
                }
                response = client.post(endpoint, files=files)
        elif method == "POST":
            response = client.post(endpoint, json=spec.get("json") or {})
            request_meta["json"] = spec.get("json") or {}
        else:
            raise ValueError(f"暂不支持 method={method}")

        status_code = response.status_code
        response_meta = _perf_compact_response_meta(response)
        success = response.status_code < 400

        if not success:
            try:
                error_message = str(response.json())
            except Exception:
                error_message = response.text

    except Exception as exc:
        error_message = str(exc)
        success = False

    end = _perf_time.perf_counter()
    latency_ms = round((end - start) * 1000, 2)
    is_realtime = success and latency_ms <= threshold_ms

    record = {
        "target": target,
        "task_type": spec.get("task_type"),
        "input_type": spec.get("input_type"),
        "endpoint": endpoint,
        "method": method,
        "status_code": status_code,
        "success": success,
        "latency_ms": latency_ms,
        "threshold_ms": threshold_ms,
        "is_realtime": is_realtime,
        "request_meta": request_meta,
        "response_meta": response_meta,
        "error_message": error_message,
        "created_at": _perf_now_text(),
    }

    record["id"] = _perf_save_record(record)
    return record


def _perf_summarize_records(records: list[dict], threshold_ms: int) -> dict:
    latencies = [
        float(item.get("latency_ms") or 0)
        for item in records
        if not item.get("skipped")
    ]

    if not latencies:
        return {
            "count": 0,
            "avg_latency_ms": 0,
            "min_latency_ms": 0,
            "max_latency_ms": 0,
            "p95_latency_ms": 0,
            "pass_count": 0,
            "fail_count": 0,
            "pass_rate": 0,
            "threshold_ms": threshold_ms,
            "is_realtime": False,
        }

    sorted_values = sorted(latencies)

    def percentile(values, p):
        if len(values) == 1:
            return round(values[0], 2)
        rank = (p / 100) * (len(values) - 1)
        lower = int(rank)
        upper = min(lower + 1, len(values) - 1)
        weight = rank - lower
        return round(values[lower] * (1 - weight) + values[upper] * weight, 2)

    pass_count = sum(1 for item in records if item.get("success") and item.get("latency_ms", 0) <= threshold_ms)
    fail_count = len([item for item in records if not item.get("skipped")]) - pass_count

    return {
        "count": len(latencies),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2),
        "min_latency_ms": round(min(latencies), 2),
        "max_latency_ms": round(max(latencies), 2),
        "p95_latency_ms": percentile(sorted_values, 95),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate": round(pass_count / len(latencies), 4),
        "threshold_ms": threshold_ms,
        "is_realtime": fail_count == 0,
    }


@app.post("/api/performance/test")
def run_performance_test(
    payload: dict | None = _PerfBody(default=None),
):
    """
    端到端实时性测试接口。

    默认测试：
    - /api/health
    - /api/plate/image
    - /api/gesture/owner/image
    - /api/gesture/traffic/image
    - /api/stream/recognize 模拟流
    - /api/fusion/decision

    如果 demo/owner_gesture.mp4 存在，也可加入 owner_video。
    如果需要真实 RTSP，把 targets 里加入 stream_rtsp。
    """
    payload = payload or {}

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        raise _PerfHTTPException(
            status_code=500,
            detail=f"性能测试需要 fastapi.testclient/httpx 支持，请执行 pip install httpx。原始错误：{exc}",
        )

    repeat = int(payload.get("repeat", 1))
    repeat = max(1, min(repeat, 20))

    threshold_ms = int(payload.get("threshold_ms", 1000))
    threshold_ms = max(1, threshold_ms)

    default_targets = [
        "health",
        "plate_image",
        "owner_image",
        "traffic_image",
        "stream_mock",
        "fusion_decision",
    ]

    targets = payload.get("targets") or default_targets
    if isinstance(targets, str):
        targets = [targets]

    target_specs = _perf_build_target_specs(payload)

    unknown_targets = [target for target in targets if target not in target_specs]
    if unknown_targets:
        raise _PerfHTTPException(
            status_code=400,
            detail=f"未知性能测试目标：{unknown_targets}。可用目标：{list(target_specs.keys())}",
        )

    client = TestClient(app)
    records = []

    for round_index in range(repeat):
        for target in targets:
            spec = dict(target_specs[target])
            spec["round_index"] = round_index + 1
            record = _perf_run_one_target(client, spec, threshold_ms)
            record["round_index"] = round_index + 1
            records.append(record)

    summary = _perf_summarize_records(records, threshold_ms)

    by_target = {}
    for target in targets:
        target_records = [item for item in records if item.get("target") == target]
        by_target[target] = _perf_summarize_records(target_records, threshold_ms)

    _perf_write_operation_log(
        action="performance_test",
        detail={
            "targets": targets,
            "repeat": repeat,
            "threshold_ms": threshold_ms,
            "summary": summary,
        },
    )

    return {
        "status": "success",
        "threshold_ms": threshold_ms,
        "constraint": "端到端识别延迟建议 <= 1000ms",
        "repeat": repeat,
        "targets": targets,
        "summary": summary,
        "by_target": by_target,
        "records": records,
    }


@app.get("/api/performance/latency-records")
def get_performance_latency_records(
    limit: int = _PerfQuery(50, ge=1, le=500),
    target: str | None = _PerfQuery(None),
):
    """
    获取最近延迟测试记录。
    """
    _perf_init_table()

    with _perf_connect() as conn:
        if target:
            rows = conn.execute(
                """
                SELECT *
                FROM performance_latency_records
                WHERE target=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (target, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM performance_latency_records
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    items = []
    for row in rows:
        item = _perf_row_to_dict(row)
        item["success"] = bool(item.get("success"))
        item["is_realtime"] = bool(item.get("is_realtime"))
        item["request_meta"] = _perf_json_loads(item.get("request_meta"))
        item["response_meta"] = _perf_json_loads(item.get("response_meta"))
        items.append(item)

    return {
        "status": "success",
        "total": len(items),
        "items": items,
    }


@app.get("/api/performance/summary")
def get_performance_summary(
    limit: int = _PerfQuery(200, ge=1, le=2000),
    threshold_ms: int = _PerfQuery(1000, ge=1),
):
    """
    获取性能测试汇总：
    - 总体平均延迟
    - P95 延迟
    - 最大延迟
    - 达标率
    - 各目标接口延迟统计
    """
    _perf_init_table()

    with _perf_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM performance_latency_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    records = []
    for row in rows:
        item = _perf_row_to_dict(row)
        item["success"] = bool(item.get("success"))
        item["is_realtime"] = bool(item.get("is_realtime"))
        records.append(item)

    overall = _perf_summarize_records(records, threshold_ms)

    by_target = {}
    targets = sorted({item.get("target") for item in records if item.get("target")})
    for target_name in targets:
        target_records = [item for item in records if item.get("target") == target_name]
        by_target[target_name] = _perf_summarize_records(target_records, threshold_ms)

    realtime_status = "pass" if overall.get("is_realtime") else "warning"

    return {
        "status": "success",
        "constraint": "端到端识别延迟建议 <= 1000ms",
        "threshold_ms": threshold_ms,
        "record_count": len(records),
        "realtime_status": realtime_status,
        "overall": overall,
        "by_target": by_target,
    }


# MULTISTREAM_CONCURRENCY_PATCH_V1
# 多路视频流并发处理接口：支持车牌、交警手势、车主手势并行消费不同视频源
from pathlib import Path as _MSPath
from datetime import datetime as _MSDateTime
import threading as _ms_threading
import time as _ms_time
import sqlite3 as _ms_sqlite3
import json as _ms_json
import uuid as _ms_uuid

from fastapi import Body as _MSBody
from fastapi import Query as _MSQuery
from fastapi import HTTPException as _MSHTTPException


_MS_LOCK = _ms_threading.RLock()
_MS_STOP_EVENT = _ms_threading.Event()
_MS_THREADS = {}
_MS_FUSION_THREAD = None

_MS_STATE = {
    "running": False,
    "started_at": None,
    "stopped_at": None,
    "enable_fusion": False,
    "fusion_interval_seconds": 5,
    "workers": {},
    "latest_results": [],
    "latest_fusion": None,
}


def _ms_now_text() -> str:
    return _MSDateTime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ms_db_path() -> _MSPath:
    db_path = globals().get("DB_PATH")
    if db_path:
        return _MSPath(db_path)
    return _MSPath(__file__).resolve().parent / "data" / "app.db"


def _ms_connect():
    db_path = _ms_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _ms_sqlite3.connect(str(db_path))
    conn.row_factory = _ms_sqlite3.Row
    return conn


def _ms_init_table():
    with _ms_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS multistream_worker_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT NOT NULL,
                source_id TEXT,
                source_url TEXT,
                task_type TEXT NOT NULL,
                input_type TEXT,
                endpoint TEXT,
                cycle_index INTEGER,
                success INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                result_summary TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_multistream_worker_id
            ON multistream_worker_records(worker_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_multistream_created_at
            ON multistream_worker_records(created_at)
            """
        )
        conn.commit()


def _ms_json_dumps(value) -> str:
    return _ms_json.dumps(value, ensure_ascii=False, default=str)


def _ms_json_loads(value):
    if not value:
        return {}
    try:
        return _ms_json.loads(value)
    except Exception:
        return {"raw": value}


def _ms_row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _ms_save_record(record: dict) -> int:
    _ms_init_table()

    with _ms_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO multistream_worker_records (
                worker_id,
                source_id,
                source_url,
                task_type,
                input_type,
                endpoint,
                cycle_index,
                success,
                latency_ms,
                result_summary,
                error_message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("worker_id"),
                record.get("source_id"),
                record.get("source_url"),
                record.get("task_type"),
                record.get("input_type"),
                record.get("endpoint"),
                int(record.get("cycle_index") or 0),
                1 if record.get("success") else 0,
                float(record.get("latency_ms") or 0),
                _ms_json_dumps(record.get("result_summary") or {}),
                record.get("error_message") or "",
                record.get("created_at") or _ms_now_text(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _ms_project_root() -> _MSPath:
    return _MSPath(__file__).resolve().parent.parent


def _ms_demo_path(filename: str) -> _MSPath:
    return _ms_project_root() / "demo" / filename


def _ms_upload_dir() -> _MSPath:
    path = _MSPath(__file__).resolve().parent / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ms_content_type(path: _MSPath) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


def _ms_compact_response_meta(response) -> dict:
    meta = {
        "status_code": response.status_code,
        "response_size_bytes": len(response.content or b""),
    }

    try:
        data = response.json()
    except Exception:
        data = None

    if not isinstance(data, dict):
        return meta

    meta["status"] = data.get("status")
    meta["record_id"] = data.get("record_id")
    meta["saved_id"] = data.get("saved_id")

    if isinstance(data.get("result"), dict):
        result = data["result"]
        meta["model"] = result.get("model")
        meta["gesture"] = result.get("gesture")
        meta["gesture_name"] = result.get("gesture_name")
        meta["traffic_command"] = result.get("traffic_command")
        meta["action"] = result.get("action")
        meta["description"] = result.get("description")
        meta["confidence"] = result.get("confidence")
        meta["plate_count"] = result.get("plate_count")
        meta["plates"] = result.get("plates")
        meta["frames_read"] = result.get("frames_read")
        meta["sampled_frames"] = result.get("sampled_frames")

    if isinstance(data.get("decision"), dict):
        decision = data["decision"]
        meta["scenario"] = decision.get("scenario")
        meta["risk_level"] = decision.get("risk_level")
        meta["risk_score"] = decision.get("risk_score")
        meta["suggestion"] = decision.get("suggestion")

    return meta


def _ms_update_worker(worker_id: str, **kwargs):
    with _MS_LOCK:
        worker = _MS_STATE["workers"].setdefault(worker_id, {})
        worker.update(kwargs)
        worker["updated_at"] = _ms_now_text()


def _ms_append_latest_result(record: dict):
    with _MS_LOCK:
        _MS_STATE["latest_results"].insert(0, record)
        _MS_STATE["latest_results"] = _MS_STATE["latest_results"][:50]


def _ms_public_state() -> dict:
    with _MS_LOCK:
        data = _ms_json.loads(_ms_json_dumps(_MS_STATE))

    for worker_id, thread in list(_MS_THREADS.items()):
        if worker_id in data.get("workers", {}):
            data["workers"][worker_id]["thread_alive"] = thread.is_alive()

    if _MS_FUSION_THREAD is not None:
        data["fusion_thread_alive"] = _MS_FUSION_THREAD.is_alive()
    else:
        data["fusion_thread_alive"] = False

    return data


def _ms_default_workers() -> list[dict]:
    """
    默认使用 mock/demo 模式，保证没有真实 RTSP 时也能演示三路并发。
    后续接 MediaMTX 时，将 use_mock_frame 改为 false，并填 source_url 即可。
    """
    return [
        {
            "worker_id": "plate_stream_worker",
            "source_id": "live12",
            "task_type": "plate",
            "use_mock_frame": True,
            "interval_seconds": 5,
            "frame_count": 20,
            "sample_interval": 5,
        },
        {
            "worker_id": "traffic_stream_worker",
            "source_id": "traffic_demo",
            "task_type": "traffic_gesture",
            "use_mock_frame": True,
            "demo_file": "traffic.png",
            "interval_seconds": 5,
        },
        {
            "worker_id": "owner_stream_worker",
            "source_id": "owner_demo",
            "task_type": "owner_gesture",
            "use_mock_frame": True,
            "demo_file": "hand.jpg",
            "interval_seconds": 5,
        },
    ]


def _ms_normalize_workers(payload: dict) -> list[dict]:
    workers = payload.get("workers") or payload.get("sources") or _ms_default_workers()

    if not isinstance(workers, list) or not workers:
        raise _MSHTTPException(status_code=400, detail="workers 必须是非空数组。")

    result = []
    allowed_tasks = {"plate", "traffic_gesture", "owner_gesture"}

    for index, item in enumerate(workers):
        if not isinstance(item, dict):
            raise _MSHTTPException(status_code=400, detail=f"第 {index + 1} 个 worker 配置不是对象。")

        task_type = str(item.get("task_type") or "").strip()
        if task_type not in allowed_tasks:
            raise _MSHTTPException(
                status_code=400,
                detail=f"不支持的 task_type={task_type}，只支持 {sorted(allowed_tasks)}。",
            )

        source_id = str(item.get("source_id") or f"source_{index + 1}").strip()
        worker_id = str(item.get("worker_id") or f"{task_type}_{source_id}_{index + 1}").strip()

        config = dict(item)
        config["worker_id"] = worker_id
        config["source_id"] = source_id
        config["task_type"] = task_type
        config["interval_seconds"] = max(1, int(item.get("interval_seconds", 5)))
        config["frame_count"] = max(1, int(item.get("frame_count", 20)))
        config["sample_interval"] = max(1, int(item.get("sample_interval", 5)))
        config["warmup_frames"] = max(0, int(item.get("warmup_frames", 3)))
        config["use_mock_frame"] = bool(item.get("use_mock_frame", False))
        config["fallback_demo"] = bool(item.get("fallback_demo", True))

        result.append(config)

    worker_ids = [item["worker_id"] for item in result]
    if len(worker_ids) != len(set(worker_ids)):
        raise _MSHTTPException(status_code=400, detail="worker_id 不能重复。")

    return result


def _ms_resolve_source_url(client, config: dict) -> str:
    if config.get("source_url"):
        return str(config["source_url"])

    source_id = config.get("source_id")
    if not source_id:
        return ""

    try:
        response = client.get("/api/rtsp/sources")
        data = response.json()
    except Exception:
        return ""

    sources = data.get("sources") or data.get("items") or data.get("data") or []
    if isinstance(sources, dict):
        sources = list(sources.values())

    if not isinstance(sources, list):
        return ""

    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("id")) == str(source_id):
            return str(source.get("url") or "")

    return ""


def _ms_capture_frame_from_rtsp(source_url: str, worker_id: str, warmup_frames: int = 3) -> _MSPath:
    if not source_url:
        raise RuntimeError("缺少 source_url，无法从真实视频流抽帧。")

    try:
        import cv2 as _ms_cv2
    except Exception as exc:
        raise RuntimeError(f"OpenCV 不可用，无法读取 RTSP：{exc}")

    cap = _ms_cv2.VideoCapture(source_url)

    try:
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频流：{source_url}")

        frame = None
        ok = False

        for _ in range(max(0, warmup_frames)):
            cap.read()

        for _ in range(5):
            ok, frame = cap.read()
            if ok and frame is not None:
                break

        if not ok or frame is None:
            raise RuntimeError(f"视频流读取失败：{source_url}")

        output_path = _ms_upload_dir() / f"multistream_{worker_id}_{_MSDateTime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        success = _ms_cv2.imwrite(str(output_path), frame)

        if not success:
            raise RuntimeError(f"抽帧图片保存失败：{output_path}")

        return output_path

    finally:
        cap.release()


def _ms_prepare_image_input(client, config: dict) -> tuple[_MSPath, dict]:
    task_type = config.get("task_type")
    use_mock = bool(config.get("use_mock_frame", False))

    if task_type == "traffic_gesture":
        demo_file = config.get("demo_file") or "traffic.png"
    elif task_type == "owner_gesture":
        demo_file = config.get("demo_file") or "hand.jpg"
    else:
        demo_file = config.get("demo_file") or "test.png"

    meta = {
        "use_mock_frame": use_mock,
        "demo_file": demo_file,
    }

    if use_mock:
        path = _ms_demo_path(demo_file)
        if not path.exists():
            raise FileNotFoundError(f"demo 测试文件不存在：{path}")
        meta["input_path"] = str(path)
        return path, meta

    source_url = _ms_resolve_source_url(client, config)
    meta["source_url"] = source_url

    try:
        frame_path = _ms_capture_frame_from_rtsp(
            source_url=source_url,
            worker_id=config.get("worker_id", "worker"),
            warmup_frames=int(config.get("warmup_frames", 3)),
        )
        meta["input_path"] = str(frame_path)
        return frame_path, meta
    except Exception:
        if not bool(config.get("fallback_demo", True)):
            raise

        fallback_path = _ms_demo_path(demo_file)
        if not fallback_path.exists():
            raise

        meta["fallback_to_demo"] = True
        meta["input_path"] = str(fallback_path)
        return fallback_path, meta


def _ms_run_plate_worker_once(client, config: dict, cycle_index: int, threshold_ms: int) -> dict:
    endpoint = "/api/stream/recognize"

    body = {
        "source_id": config.get("source_id", "live12"),
        "task_type": "plate",
        "frame_count": int(config.get("frame_count", 20)),
        "sample_interval": int(config.get("sample_interval", 5)),
        "use_mock_frame": bool(config.get("use_mock_frame", True)),
    }

    start = _ms_time.perf_counter()
    response = client.post(endpoint, json=body)
    latency_ms = round((_ms_time.perf_counter() - start) * 1000, 2)

    success = response.status_code < 400
    response_meta = _ms_compact_response_meta(response)

    error_message = ""
    if not success:
        try:
            error_message = str(response.json())
        except Exception:
            error_message = response.text

    return {
        "worker_id": config.get("worker_id"),
        "source_id": config.get("source_id"),
        "source_url": config.get("source_url", ""),
        "task_type": "plate",
        "input_type": "mock_stream" if body["use_mock_frame"] else "rtsp_stream",
        "endpoint": endpoint,
        "cycle_index": cycle_index,
        "success": success,
        "latency_ms": latency_ms,
        "threshold_ms": threshold_ms,
        "is_realtime": success and latency_ms <= threshold_ms,
        "result_summary": response_meta,
        "error_message": error_message,
        "created_at": _ms_now_text(),
    }


def _ms_run_image_stream_worker_once(client, config: dict, cycle_index: int, threshold_ms: int) -> dict:
    task_type = config.get("task_type")

    if task_type == "traffic_gesture":
        endpoint = "/api/gesture/traffic/image"
    elif task_type == "owner_gesture":
        endpoint = "/api/gesture/owner/image"
    else:
        raise RuntimeError(f"不支持的图片流 worker task_type={task_type}")

    start = _ms_time.perf_counter()
    success = False
    response_meta = {}
    error_message = ""
    status_code = 0
    input_meta = {}

    try:
        image_path, input_meta = _ms_prepare_image_input(client, config)

        with image_path.open("rb") as file_obj:
            files = {
                "file": (
                    image_path.name,
                    file_obj,
                    _ms_content_type(image_path),
                )
            }
            response = client.post(endpoint, files=files)

        status_code = response.status_code
        success = response.status_code < 400
        response_meta = _ms_compact_response_meta(response)
        response_meta["input_meta"] = input_meta

        if not success:
            try:
                error_message = str(response.json())
            except Exception:
                error_message = response.text

    except Exception as exc:
        error_message = str(exc)

    latency_ms = round((_ms_time.perf_counter() - start) * 1000, 2)

    return {
        "worker_id": config.get("worker_id"),
        "source_id": config.get("source_id"),
        "source_url": config.get("source_url", "") or input_meta.get("source_url", ""),
        "task_type": task_type,
        "input_type": "mock_frame" if bool(config.get("use_mock_frame", False)) else "rtsp_frame",
        "endpoint": endpoint,
        "cycle_index": cycle_index,
        "success": success,
        "latency_ms": latency_ms,
        "threshold_ms": threshold_ms,
        "is_realtime": success and latency_ms <= threshold_ms,
        "result_summary": response_meta,
        "error_message": error_message,
        "created_at": _ms_now_text(),
        "status_code": status_code,
    }


def _ms_run_worker_once(client, config: dict, cycle_index: int, threshold_ms: int) -> dict:
    task_type = config.get("task_type")

    if task_type == "plate":
        return _ms_run_plate_worker_once(client, config, cycle_index, threshold_ms)

    if task_type in {"traffic_gesture", "owner_gesture"}:
        return _ms_run_image_stream_worker_once(client, config, cycle_index, threshold_ms)

    raise RuntimeError(f"不支持的 task_type={task_type}")


def _ms_worker_loop(config: dict, threshold_ms: int):
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        _ms_update_worker(
            config.get("worker_id", "unknown"),
            status="failed",
            error_message=f"缺少 TestClient/httpx2 支持：{exc}",
        )
        return

    worker_id = config["worker_id"]
    client = TestClient(app)
    cycle_index = 0

    _ms_update_worker(
        worker_id,
        worker_id=worker_id,
        source_id=config.get("source_id"),
        source_url=config.get("source_url", ""),
        task_type=config.get("task_type"),
        status="running",
        started_at=_ms_now_text(),
        cycle_count=0,
        success_count=0,
        fail_count=0,
        last_latency_ms=None,
        is_realtime=None,
        config=config,
    )

    while not _MS_STOP_EVENT.is_set():
        cycle_index += 1

        _ms_update_worker(
            worker_id,
            status="running",
            current_cycle=cycle_index,
            last_started_at=_ms_now_text(),
        )

        try:
            record = _ms_run_worker_once(client, config, cycle_index, threshold_ms)
        except Exception as exc:
            record = {
                "worker_id": worker_id,
                "source_id": config.get("source_id"),
                "source_url": config.get("source_url", ""),
                "task_type": config.get("task_type"),
                "input_type": "",
                "endpoint": "",
                "cycle_index": cycle_index,
                "success": False,
                "latency_ms": 0,
                "threshold_ms": threshold_ms,
                "is_realtime": False,
                "result_summary": {},
                "error_message": str(exc),
                "created_at": _ms_now_text(),
            }

        try:
            record["id"] = _ms_save_record(record)
        except Exception as exc:
            record["db_error"] = str(exc)

        _ms_append_latest_result(record)

        with _MS_LOCK:
            worker = _MS_STATE["workers"].setdefault(worker_id, {})
            worker["cycle_count"] = int(worker.get("cycle_count") or 0) + 1

            if record.get("success"):
                worker["success_count"] = int(worker.get("success_count") or 0) + 1
            else:
                worker["fail_count"] = int(worker.get("fail_count") or 0) + 1

            worker["status"] = "running"
            worker["last_latency_ms"] = record.get("latency_ms")
            worker["is_realtime"] = record.get("is_realtime")
            worker["last_result_summary"] = record.get("result_summary")
            worker["last_error_message"] = record.get("error_message")
            worker["last_record_id"] = record.get("id")
            worker["updated_at"] = _ms_now_text()

        interval = max(1, int(config.get("interval_seconds", 5)))
        _MS_STOP_EVENT.wait(interval)

    _ms_update_worker(
        worker_id,
        status="stopped",
        stopped_at=_ms_now_text(),
    )


def _ms_fusion_loop(interval_seconds: int):
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        with _MS_LOCK:
            _MS_STATE["latest_fusion"] = {
                "status": "failed",
                "error_message": f"缺少 TestClient/httpx2 支持：{exc}",
                "created_at": _ms_now_text(),
            }
        return

    client = TestClient(app)

    while not _MS_STOP_EVENT.is_set():
        start = _ms_time.perf_counter()

        try:
            response = client.post("/api/fusion/decision?save=true", json={})
            latency_ms = round((_ms_time.perf_counter() - start) * 1000, 2)
            success = response.status_code < 400
            summary = _ms_compact_response_meta(response)

            with _MS_LOCK:
                _MS_STATE["latest_fusion"] = {
                    "status": "success" if success else "failed",
                    "latency_ms": latency_ms,
                    "summary": summary,
                    "created_at": _ms_now_text(),
                }

        except Exception as exc:
            with _MS_LOCK:
                _MS_STATE["latest_fusion"] = {
                    "status": "failed",
                    "error_message": str(exc),
                    "created_at": _ms_now_text(),
                }

        _MS_STOP_EVENT.wait(max(1, int(interval_seconds)))


def _ms_stop_all(join_timeout: float = 5.0) -> dict:
    global _MS_FUSION_THREAD

    _MS_STOP_EVENT.set()

    for thread in list(_MS_THREADS.values()):
        if thread.is_alive():
            thread.join(timeout=join_timeout)

    if _MS_FUSION_THREAD is not None and _MS_FUSION_THREAD.is_alive():
        _MS_FUSION_THREAD.join(timeout=join_timeout)

    with _MS_LOCK:
        _MS_STATE["running"] = False
        _MS_STATE["stopped_at"] = _ms_now_text()

    _MS_THREADS.clear()
    _MS_FUSION_THREAD = None

    return _ms_public_state()




# MULTISTREAM_WORKER_SAFE_FIX_V2
# 修复多路并发 worker 线程异常不可见的问题，并在启动前预注册 worker 状态
def _ms_worker_loop_safe(config: dict, threshold_ms: int):
    worker_id = str(config.get("worker_id") or "unknown_worker")

    try:
        _ms_update_worker(
            worker_id,
            source_id=config.get("source_id"),
            source_url=config.get("source_url", ""),
            task_type=config.get("task_type"),
            status="starting",
            started_at=_ms_now_text(),
            cycle_count=0,
            success_count=0,
            fail_count=0,
            last_latency_ms=None,
            is_realtime=None,
            last_result_summary=None,
            last_error_message="",
            config=config,
        )

        _ms_worker_loop(config, threshold_ms)

    except BaseException as exc:
        _ms_update_worker(
            worker_id,
            source_id=config.get("source_id"),
            source_url=config.get("source_url", ""),
            task_type=config.get("task_type"),
            status="failed",
            last_error_message=repr(exc),
            stopped_at=_ms_now_text(),
            config=config,
        )

        try:
            record = {
                "worker_id": worker_id,
                "source_id": config.get("source_id"),
                "source_url": config.get("source_url", ""),
                "task_type": config.get("task_type", ""),
                "input_type": "",
                "endpoint": "",
                "cycle_index": 0,
                "success": False,
                "latency_ms": 0,
                "threshold_ms": threshold_ms,
                "is_realtime": False,
                "result_summary": {"thread_error": repr(exc)},
                "error_message": repr(exc),
                "created_at": _ms_now_text(),
            }
            record["id"] = _ms_save_record(record)
            _ms_append_latest_result(record)
        except Exception:
            pass


@app.post("/api/multistream/start")
def start_multistream_processing(payload: dict | None = _MSBody(default=None)):
    """
    启动多路视频流并发处理。

    worker.task_type 支持：
    - plate：调用 /api/stream/recognize
    - traffic_gesture：从视频源抽帧后调用 /api/gesture/traffic/image
    - owner_gesture：从视频源抽帧后调用 /api/gesture/owner/image
    """
    global _MS_FUSION_THREAD

    payload = payload or {}

    try:
        from fastapi.testclient import TestClient  # noqa: F401
    except Exception as exc:
        raise _MSHTTPException(
            status_code=500,
            detail=f"多路并发处理需要 fastapi.testclient/httpx2 支持，请确认 httpx2 已安装在后端虚拟环境。原始错误：{exc}",
        )

    with _MS_LOCK:
        if _MS_STATE.get("running"):
            raise _MSHTTPException(status_code=400, detail="多路视频流并发处理已经在运行，请先调用 /api/multistream/stop。")

    workers = _ms_normalize_workers(payload)
    threshold_ms = int(payload.get("threshold_ms", 1000))
    enable_fusion = bool(payload.get("enable_fusion", True))
    fusion_interval_seconds = max(1, int(payload.get("fusion_interval_seconds", 5)))

    _MS_STOP_EVENT.clear()
    _MS_THREADS.clear()

    with _MS_LOCK:
        _MS_STATE["running"] = True
        _MS_STATE["started_at"] = _ms_now_text()
        _MS_STATE["stopped_at"] = None
        _MS_STATE["enable_fusion"] = enable_fusion
        _MS_STATE["fusion_interval_seconds"] = fusion_interval_seconds
        _MS_STATE["workers"] = {}
        _MS_STATE["latest_results"] = []
        _MS_STATE["latest_fusion"] = None

    for config in workers:
        worker_id = config["worker_id"]

        _ms_update_worker(
            worker_id,
            source_id=config.get("source_id"),
            source_url=config.get("source_url", ""),
            task_type=config.get("task_type"),
            status="starting",
            started_at=_ms_now_text(),
            cycle_count=0,
            success_count=0,
            fail_count=0,
            last_latency_ms=None,
            is_realtime=None,
            last_result_summary=None,
            last_error_message="",
            config=config,
        )

        thread = _ms_threading.Thread(
            target=_ms_worker_loop_safe,
            args=(config, threshold_ms),
            daemon=True,
            name=f"multistream-{worker_id}",
        )

        _MS_THREADS[worker_id] = thread
        thread.start()

    if enable_fusion:
        _MS_FUSION_THREAD = _ms_threading.Thread(
            target=_ms_fusion_loop,
            args=(fusion_interval_seconds,),
            daemon=True,
            name="multistream-fusion",
        )
        _MS_FUSION_THREAD.start()
    else:
        _MS_FUSION_THREAD = None

    return {
        "status": "success",
        "message": "多路视频流并发处理已启动",
        "worker_count": len(workers),
        "enable_fusion": enable_fusion,
        "threshold_ms": threshold_ms,
        "state": _ms_public_state(),
    }


@app.post("/api/multistream/stop")
def stop_multistream_processing():
    """
    停止多路视频流并发处理。
    """
    state = _ms_stop_all()
    return {
        "status": "success",
        "message": "多路视频流并发处理已停止",
        "state": state,
    }


@app.get("/api/multistream/status")
def get_multistream_status():
    """
    获取多路视频流并发处理运行状态。
    """
    return {
        "status": "success",
        "state": _ms_public_state(),
    }


@app.get("/api/multistream/latest")
def get_multistream_latest_records(
    limit: int = _MSQuery(50, ge=1, le=500),
    worker_id: str | None = _MSQuery(None),
):
    """
    获取多路并发 worker 最近运行记录。
    """
    _ms_init_table()

    with _ms_connect() as conn:
        if worker_id:
            rows = conn.execute(
                """
                SELECT *
                FROM multistream_worker_records
                WHERE worker_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (worker_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM multistream_worker_records
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    items = []
    for row in rows:
        item = _ms_row_to_dict(row)
        item["success"] = bool(item.get("success"))
        item["result_summary"] = _ms_json_loads(item.get("result_summary"))
        items.append(item)

    return {
        "status": "success",
        "total": len(items),
        "items": items,
    }


# MULTISTREAM_UPDATE_WORKER_OVERRIDE_V1
# 兼容修复：允许 _ms_update_worker(worker_id, worker_id=worker_id, ...) 这种重复传参写法
def _ms_update_worker(*args, **kwargs):
    """
    多路并发 worker 状态更新函数。

    修复点：
    - 旧代码里部分调用同时传了位置参数 worker_id 和关键字参数 worker_id。
    - 如果函数签名写成 def _ms_update_worker(worker_id, **kwargs)，会触发：
      TypeError: got multiple values for argument 'worker_id'
    - 这里改成 *args, **kwargs，手动解析 worker_id，避免线程启动后直接失败。
    """
    positional_worker_id = None

    if args:
        positional_worker_id = args[0]

    keyword_worker_id = kwargs.pop("worker_id", None)

    worker_id = positional_worker_id or keyword_worker_id

    if worker_id is None:
        worker_id = kwargs.get("source_id") or kwargs.get("task_type") or "unknown_worker"

    worker_id = str(worker_id)

    with _MS_LOCK:
        worker = _MS_STATE["workers"].setdefault(worker_id, {})
        worker["worker_id"] = worker_id

        if keyword_worker_id is not None:
            worker["configured_worker_id"] = str(keyword_worker_id)

        worker.update(kwargs)
        worker["updated_at"] = _ms_now_text()

    return worker


# OWNER_CAMERA_REALTIME_ENDPOINT_V1
# 车主手势：电脑摄像头实时帧专用识别接口
from fastapi import UploadFile as _OCUploadFile, File as _OCFile, HTTPException as _OCHTTPException
from pathlib import Path as _OCPath
from datetime import datetime as _OCDatetime
import time as _oc_time
import uuid as _oc_uuid
import math as _oc_math
import cv2 as _oc_cv2
import numpy as _oc_np


_OC_MP = None
_OC_HANDS = None
_OC_HANDS_INIT_LOCK = threading.RLock()
_OC_HANDS_PROCESS_LOCK = threading.RLock()
_OC_HISTORY = []
_OC_HISTORY_MAX_SECONDS = 2.6

# 动态轨迹状态：
# - 容忍摄像头运动模糊造成的短暂丢手，避免一次丢帧清空整段轨迹。
# - 动态手势识别后短暂保持显示，避免下一帧立即退回“手掌张开”。
_OC_MISSING_HAND_FRAMES = 0
_OC_MISSING_HAND_RESET_AFTER = 3
_OC_DYNAMIC_LOCK_UNTIL = 0.0
_OC_DYNAMIC_HOLD = {
    "gesture": "",
    "gesture_name": "",
    "confidence": 0.0,
    "until": 0.0,
}
_OC_DYNAMIC_HOLD_SECONDS = 1.45
_OC_DYNAMIC_LOCK_SECONDS = 0.45

# 摄像头预览属于镜像交互：
# 用户实际向右移动，在摄像头原始坐标中表现为向左。
_OC_CAMERA_MIRROR_DIRECTIONS = True

# 动态动作只触发一次。完成后必须先静止复位或移出画面，
# 才允许识别下一次动态动作。
_OC_DYNAMIC_REARM_REQUIRED = False
_OC_DYNAMIC_REARM_STILL_SINCE = 0.0
_OC_DYNAMIC_REARM_STILL_SECONDS = 0.72
_OC_DYNAMIC_EVENT_SEQUENCE = 0
_OC_LAST_DYNAMIC_EVENT_ID = 0

# 静态手势确认与锁存：
# open_palm 延迟确认，给左右滑和挥手留出轨迹采集时间；
# 同一静态姿势持续保持时只触发一次。
_OC_STABLE_TRACK = {
    "gesture": "",
    "count": 0,
}
_OC_STATIC_STABLE_FRAMES = 3
_OC_STATIC_REQUIRED_FRAMES = {
    "open_palm": 6,
    "fist": 3,
    "thumb_up": 3,
    "thumb_down": 3,
}
_OC_STATIC_LATCH_GESTURE = ""

# 同一控制动作设置冷却时间，避免 400ms 摄像头轮询导致音量连续暴增。
_OC_ACTION_COOLDOWN_SECONDS = 1.2
_OC_LAST_ACTION_AT: dict[str, float] = {}

_OC_FUNCTION_ORDER = [
    "home",
    "media",
    "climate",
    "phone",
    "navigation",
]

_OC_GESTURE_MAPPING_VERSION = "owner_gesture_v4_mirror_commit_state"
_OC_GESTURE_MAPPING = [
    {
        "gesture": "open_palm",
        "gesture_name": "手掌张开",
        "action": "wake_system",
        "description": "启动/唤醒系统",
        "type": "static",
    },
    {
        "gesture": "fist",
        "gesture_name": "握拳",
        "action": "confirm",
        "description": "确认/执行",
        "type": "static",
    },
    {
        "gesture": "circle_clockwise",
        "gesture_name": "单指顺时针画圈",
        "action": "volume_up",
        "description": "调高音量",
        "type": "dynamic",
    },
    {
        "gesture": "circle_counterclockwise",
        "gesture_name": "单指逆时针画圈",
        "action": "volume_down",
        "description": "调低音量",
        "type": "dynamic",
    },
    {
        "gesture": "swipe_left",
        "gesture_name": "向左滑动",
        "action": "previous_function",
        "description": "切换到上一功能",
        "type": "dynamic",
    },
    {
        "gesture": "swipe_right",
        "gesture_name": "向右滑动",
        "action": "next_function",
        "description": "切换到下一功能",
        "type": "dynamic",
    },
    {
        "gesture": "thumb_up",
        "gesture_name": "拇指向上",
        "action": "answer_call",
        "description": "接听电话",
        "type": "static",
    },
    {
        "gesture": "thumb_down",
        "gesture_name": "拇指向下",
        "action": "hang_up_call",
        "description": "挂断电话",
        "type": "static",
    },
    {
        "gesture": "wave",
        "gesture_name": "挥手",
        "action": "back_home",
        "description": "返回主页",
        "type": "dynamic",
    },
]

_OC_VEHICLE_STATE = {
    "system_awake": False,
    "current_function": "home",
    "volume": 50,
    "temperature": 24,
    "phone_status": "空闲",
    "last_action": "none",
    "last_description": "未触发车辆控制",
    "updated_at": "",
}


def _oc_now_text() -> str:
    return _OCDatetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _oc_backend_dir() -> _OCPath:
    return _OCPath(__file__).resolve().parent


def _oc_upload_dir() -> _OCPath:
    path = _oc_backend_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _oc_output_dir() -> _OCPath:
    path = _oc_backend_dir() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _oc_get_hands():
    global _OC_MP, _OC_HANDS

    if _OC_HANDS is not None:
        return _OC_HANDS

    with _OC_HANDS_INIT_LOCK:
        if _OC_HANDS is None:
            import mediapipe as _mp
            _OC_MP = _mp
            _OC_HANDS = _mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                model_complexity=0,
                min_detection_confidence=0.55,
                min_tracking_confidence=0.50,
            )

    return _OC_HANDS


def _oc_dist(a, b) -> float:
    return _oc_math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def _oc_to_landmark_dicts(hand_landmarks, width: int, height: int) -> list[dict]:
    items = []

    for idx, lm in enumerate(hand_landmarks.landmark):
        items.append({
            "index": idx,
            "x": round(float(lm.x), 4),
            "y": round(float(lm.y), 4),
            "z": round(float(lm.z), 4),
            "pixel_x": int(lm.x * width),
            "pixel_y": int(lm.y * height),
        })

    return items


def _oc_finger_extended(lms, tip: int, pip: int, mcp: int, wrist: int = 0) -> bool:
    """
    摄像头场景下的手指伸展判定。
    兼顾 y 方向和相对掌心距离，避免轻微弯曲造成误判。
    """
    tip_lm = lms[tip]
    pip_lm = lms[pip]
    mcp_lm = lms[mcp]
    wrist_lm = lms[wrist]

    y_extended = tip_lm.y < pip_lm.y - 0.025
    distance_extended = _oc_dist(tip_lm, wrist_lm) > _oc_dist(pip_lm, wrist_lm) * 1.05

    # 如果手指横向伸出，y 条件可能不明显，这里用 tip 到 mcp 距离兜底。
    long_enough = _oc_dist(tip_lm, mcp_lm) > 0.11

    return bool((y_extended and distance_extended) or (distance_extended and long_enough and tip_lm.y < mcp_lm.y + 0.02))


def _oc_reset_tracking(
    *,
    clear_dynamic_hold: bool = True,
) -> None:
    global _OC_MISSING_HAND_FRAMES
    global _OC_DYNAMIC_LOCK_UNTIL
    global _OC_DYNAMIC_REARM_REQUIRED
    global _OC_DYNAMIC_REARM_STILL_SINCE
    global _OC_STATIC_LATCH_GESTURE

    _OC_HISTORY.clear()
    _OC_STABLE_TRACK["gesture"] = ""
    _OC_STABLE_TRACK["count"] = 0
    _OC_MISSING_HAND_FRAMES = 0
    _OC_DYNAMIC_LOCK_UNTIL = 0.0
    _OC_DYNAMIC_REARM_REQUIRED = False
    _OC_DYNAMIC_REARM_STILL_SINCE = 0.0
    _OC_STATIC_LATCH_GESTURE = ""

    if clear_dynamic_hold:
        _OC_DYNAMIC_HOLD.update({
            "gesture": "",
            "gesture_name": "",
            "confidence": 0.0,
            "until": 0.0,
        })


def _oc_note_missing_hand() -> bool:
    """
    返回是否已经真正清空轨迹。

    动态动作中出现一两帧运动模糊非常常见，不能一丢手就重置。
    """
    global _OC_MISSING_HAND_FRAMES

    _OC_MISSING_HAND_FRAMES += 1

    if _OC_MISSING_HAND_FRAMES >= _OC_MISSING_HAND_RESET_AFTER:
        _oc_reset_tracking()
        return True

    return False


def _oc_note_hand_detected() -> None:
    global _OC_MISSING_HAND_FRAMES
    _OC_MISSING_HAND_FRAMES = 0


def _oc_palm_center(hand_landmarks) -> dict:
    """
    使用腕点和四个 MCP 关节计算掌心。

    不能使用全部 21 点平均值：手指弯曲或张开会改变全点中心，
    容易把“手型变化”误当成“手掌移动”。
    """
    lms = hand_landmarks.landmark
    ids = (0, 5, 9, 13, 17)

    return {
        "x": round(
            sum(float(lms[index].x) for index in ids)
            / len(ids),
            4,
        ),
        "y": round(
            sum(float(lms[index].y) for index in ids)
            / len(ids),
            4,
        ),
    }


def _oc_index_tip(hand_landmarks) -> dict:
    tip = hand_landmarks.landmark[8]
    return {
        "x": round(float(tip.x), 4),
        "y": round(float(tip.y), 4),
    }


def _oc_update_history(
    hand_center: dict,
    static_gesture: str,
    index_tip: dict,
) -> None:
    now = _oc_time.time()
    raw_x = float(hand_center["x"])
    raw_y = float(hand_center["y"])

    # 轻量指数平滑，降低 MediaPipe 单点抖动造成的假方向变化。
    if (
        _OC_HISTORY
        and now - float(_OC_HISTORY[-1].get("t") or now) < 0.65
    ):
        previous = _OC_HISTORY[-1]
        alpha = 0.58
        smooth_x = (
            alpha * raw_x
            + (1.0 - alpha) * float(previous["x"])
        )
        smooth_y = (
            alpha * raw_y
            + (1.0 - alpha) * float(previous["y"])
        )
    else:
        smooth_x = raw_x
        smooth_y = raw_y

    _OC_HISTORY.append({
        "t": now,
        "x": smooth_x,
        "y": smooth_y,
        "raw_x": raw_x,
        "raw_y": raw_y,
        "index_x": float(index_tip["x"]),
        "index_y": float(index_tip["y"]),
        "gesture": static_gesture,
    })

    while (
        _OC_HISTORY
        and now - float(_OC_HISTORY[0]["t"])
        > _OC_HISTORY_MAX_SECONDS
    ):
        _OC_HISTORY.pop(0)


def _oc_recent_history(seconds: float) -> list[dict]:
    if not _OC_HISTORY:
        return []

    now = float(_OC_HISTORY[-1]["t"])
    return [
        item
        for item in _OC_HISTORY
        if now - float(item.get("t") or now) <= seconds
    ]


def _oc_direction_signs(
    values: list[float],
    min_delta: float = 0.009,
) -> list[int]:
    signs: list[int] = []

    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        if abs(delta) < min_delta:
            continue

        sign = 1 if delta > 0 else -1
        if not signs or signs[-1] != sign:
            signs.append(sign)

    return signs


def _oc_direction_change_count(
    values: list[float],
    min_delta: float = 0.009,
) -> int:
    signs = _oc_direction_signs(
        values,
        min_delta=min_delta,
    )
    return max(0, len(signs) - 1)


def _oc_path_length(
    xs: list[float],
    ys: list[float],
) -> float:
    return sum(
        _oc_math.sqrt(
            (xs[index] - xs[index - 1]) ** 2
            + (ys[index] - ys[index - 1]) ** 2
        )
        for index in range(1, len(xs))
    )


def _oc_open_ratio(history: list[dict]) -> float:
    if not history:
        return 0.0

    open_count = sum(
        1
        for item in history
        if item.get("gesture") in {
            "open_palm",
            "wave",
            "swipe_left",
            "swipe_right",
        }
    )
    return open_count / len(history)


def _oc_detect_swipe() -> tuple[str | None, dict]:
    """
    单向滑动检测。

    关键变化：
    - 阈值由 0.22 降到约 0.13；
    - 使用轨迹效率和方向一致性抑制挥手误判；
    - 支持“移动后短暂停顿”作为滑动结束信号。
    """
    history = _oc_recent_history(1.15)
    debug = {
        "kind": "swipe",
        "frame_count": len(history),
    }

    if len(history) < 4:
        return None, debug

    xs = [float(item["x"]) for item in history]
    ys = [float(item["y"]) for item in history]
    times = [float(item["t"]) for item in history]

    dx = xs[-1] - xs[0]
    dy = ys[-1] - ys[0]
    path_length = _oc_path_length(xs, ys)
    horizontal_path = sum(
        abs(xs[index] - xs[index - 1])
        for index in range(1, len(xs))
    )
    duration = max(0.001, times[-1] - times[0])
    efficiency = abs(dx) / max(horizontal_path, 1e-6)
    sign_changes = _oc_direction_change_count(
        xs,
        min_delta=0.010,
    )
    open_ratio = _oc_open_ratio(history)

    recent_deltas = [
        abs(xs[index] - xs[index - 1])
        for index in range(
            max(1, len(xs) - 2),
            len(xs),
        )
    ]
    terminal_slow = (
        bool(recent_deltas)
        and sum(recent_deltas) / len(recent_deltas) < 0.018
    )

    debug.update({
        "dx": round(dx, 4),
        "dy": round(dy, 4),
        "duration": round(duration, 4),
        "path_length": round(path_length, 4),
        "horizontal_path": round(horizontal_path, 4),
        "efficiency": round(efficiency, 4),
        "sign_changes": sign_changes,
        "open_ratio": round(open_ratio, 4),
        "terminal_slow": terminal_slow,
    })

    if abs(dx) < 0.13:
        return None, debug
    if abs(dy) > 0.15:
        return None, debug
    if open_ratio < 0.55:
        return None, debug
    if efficiency < 0.68:
        return None, debug
    if sign_changes > 1:
        return None, debug
    if duration < 0.22:
        return None, debug

    # 较短位移要求动作末端有停顿；大幅滑动可直接确认。
    if abs(dx) < 0.19 and not terminal_slow:
        return None, debug

    return (
        "swipe_right" if dx > 0 else "swipe_left",
        debug,
    )


def _oc_detect_wave() -> tuple[bool, dict]:
    """
    挥手要求至少一次明确往返。

    与滑动的区别：
    - 横向路径明显大于净位移；
    - 至少一次方向反转；
    - 最终位置接近起点或轨迹效率较低。
    """
    history = _oc_recent_history(1.95)
    debug = {
        "kind": "wave",
        "frame_count": len(history),
    }

    if len(history) < 5:
        return False, debug

    xs = [float(item["x"]) for item in history]
    ys = [float(item["y"]) for item in history]

    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    net_dx = xs[-1] - xs[0]
    horizontal_path = sum(
        abs(xs[index] - xs[index - 1])
        for index in range(1, len(xs))
    )
    efficiency = abs(net_dx) / max(horizontal_path, 1e-6)
    sign_changes = _oc_direction_change_count(
        xs,
        min_delta=0.012,
    )
    open_ratio = _oc_open_ratio(history)

    debug.update({
        "x_span": round(x_span, 4),
        "y_span": round(y_span, 4),
        "net_dx": round(net_dx, 4),
        "horizontal_path": round(horizontal_path, 4),
        "efficiency": round(efficiency, 4),
        "sign_changes": sign_changes,
        "open_ratio": round(open_ratio, 4),
    })

    detected = (
        x_span >= 0.12
        and y_span <= 0.24
        and horizontal_path >= 0.24
        and sign_changes >= 1
        and open_ratio >= 0.60
        and (
            abs(net_dx) <= 0.10
            or efficiency <= 0.48
        )
    )

    return detected, debug


def _oc_detect_circle() -> tuple[str | None, dict]:
    """
    单指画圈使用食指指尖轨迹，而不是掌心轨迹。
    """
    history = _oc_recent_history(2.4)
    one_items = [
        item
        for item in history
        if item.get("gesture") in {
            "one",
            "circle_clockwise",
            "circle_counterclockwise",
        }
    ]

    debug = {
        "kind": "circle",
        "frame_count": len(one_items),
    }

    if len(one_items) < 6:
        return None, debug

    xs = [float(item["index_x"]) for item in one_items]
    ys = [float(item["index_y"]) for item in one_items]

    x_range = max(xs) - min(xs)
    y_range = max(ys) - min(ys)

    debug.update({
        "x_range": round(x_range, 4),
        "y_range": round(y_range, 4),
    })

    if x_range < 0.09 or y_range < 0.09:
        return None, debug

    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)

    angles: list[float] = []

    for x, y in zip(xs, ys):
        radius = _oc_math.sqrt(
            (x - center_x) ** 2
            + (y - center_y) ** 2
        )
        if radius < 0.028:
            continue
        angles.append(
            _oc_math.atan2(
                y - center_y,
                x - center_x,
            )
        )

    if len(angles) < 5:
        return None, debug

    total_angle = 0.0

    for index in range(1, len(angles)):
        delta = angles[index] - angles[index - 1]

        while delta > _oc_math.pi:
            delta -= 2 * _oc_math.pi
        while delta < -_oc_math.pi:
            delta += 2 * _oc_math.pi

        if abs(delta) <= 1.55:
            total_angle += delta

    start_end_distance = _oc_math.sqrt(
        (xs[-1] - xs[0]) ** 2
        + (ys[-1] - ys[0]) ** 2
    )

    debug.update({
        "total_angle": round(total_angle, 4),
        "start_end_distance": round(
            start_end_distance,
            4,
        ),
    })

    if abs(total_angle) < 4.15:
        return None, debug
    if start_end_distance > 0.20:
        return None, debug

    return (
        (
            "circle_clockwise"
            if total_angle > 0
            else "circle_counterclockwise"
        ),
        debug,
    )


def _oc_motion_pending_debug() -> tuple[bool, dict]:
    history = _oc_recent_history(0.95)
    debug = {
        "kind": "pending",
        "frame_count": len(history),
    }

    if len(history) < 3:
        return False, debug

    xs = [float(item["x"]) for item in history]
    ys = [float(item["y"]) for item in history]

    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    path_length = _oc_path_length(xs, ys)
    open_ratio = _oc_open_ratio(history)

    debug.update({
        "x_span": round(x_span, 4),
        "y_span": round(y_span, 4),
        "path_length": round(path_length, 4),
        "open_ratio": round(open_ratio, 4),
    })

    pending = (
        open_ratio >= 0.50
        and x_span >= 0.045
        and path_length >= 0.065
        and y_span <= 0.25
    )

    return pending, debug



def _oc_mirror_camera_dynamic_gesture(
    gesture: str,
) -> str:
    """
    将摄像头原始坐标中的方向转换成用户在镜像预览中的实际方向。
    """
    if not _OC_CAMERA_MIRROR_DIRECTIONS:
        return gesture

    mirror_mapping = {
        "swipe_left": "swipe_right",
        "swipe_right": "swipe_left",
        "circle_clockwise": "circle_counterclockwise",
        "circle_counterclockwise": "circle_clockwise",
    }
    return mirror_mapping.get(gesture, gesture)


def _oc_next_event_id() -> int:
    global _OC_DYNAMIC_EVENT_SEQUENCE

    _OC_DYNAMIC_EVENT_SEQUENCE += 1
    return _OC_DYNAMIC_EVENT_SEQUENCE


def _oc_waiting_next_result() -> dict:
    return {
        "gesture": "waiting_next",
        "gesture_name": "等待下一个动作",
        "confidence": 0.0,
        "phase": "waiting",
        "is_event": False,
        "event_id": _OC_LAST_DYNAMIC_EVENT_ID,
        "debug": {
            "rearm_required": True,
        },
    }


def _oc_try_rearm_dynamic(
    static_gesture: str,
) -> bool:
    """
    动态动作完成后，手掌需要短暂静止，才重新允许下一次识别。

    这样连续挥动过程中不会反复触发：
    挥手 → 判定中 → 挥手 → 判定中。
    """
    global _OC_DYNAMIC_REARM_REQUIRED
    global _OC_DYNAMIC_REARM_STILL_SINCE

    if not _OC_DYNAMIC_REARM_REQUIRED:
        return True

    history = _oc_recent_history(0.50)

    if len(history) < 3:
        _OC_DYNAMIC_REARM_STILL_SINCE = 0.0
        return False

    xs = [float(item["x"]) for item in history]
    ys = [float(item["y"]) for item in history]

    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    now = _oc_time.time()

    # 允许张开手掌或单指姿势在动作结束后静止复位。
    valid_reset_pose = static_gesture in {
        "open_palm",
        "one",
        "unknown",
    }
    is_still = (
        valid_reset_pose
        and x_span <= 0.025
        and y_span <= 0.030
    )

    if not is_still:
        _OC_DYNAMIC_REARM_STILL_SINCE = 0.0
        return False

    if _OC_DYNAMIC_REARM_STILL_SINCE <= 0:
        _OC_DYNAMIC_REARM_STILL_SINCE = now
        return False

    if (
        now - _OC_DYNAMIC_REARM_STILL_SINCE
        < _OC_DYNAMIC_REARM_STILL_SECONDS
    ):
        return False

    _OC_DYNAMIC_REARM_REQUIRED = False
    _OC_DYNAMIC_REARM_STILL_SINCE = 0.0
    _OC_HISTORY.clear()
    _OC_STABLE_TRACK["gesture"] = ""
    _OC_STABLE_TRACK["count"] = 0
    return True


def _oc_register_dynamic_event(
    gesture: str,
    gesture_name: str,
    confidence: float,
) -> int:
    global _OC_DYNAMIC_LOCK_UNTIL
    global _OC_DYNAMIC_REARM_REQUIRED
    global _OC_DYNAMIC_REARM_STILL_SINCE
    global _OC_LAST_DYNAMIC_EVENT_ID

    now = _oc_time.time()
    event_id = _oc_next_event_id()
    _OC_LAST_DYNAMIC_EVENT_ID = event_id

    _OC_DYNAMIC_LOCK_UNTIL = (
        now + _OC_DYNAMIC_LOCK_SECONDS
    )
    _OC_DYNAMIC_REARM_REQUIRED = True
    _OC_DYNAMIC_REARM_STILL_SINCE = 0.0

    _OC_DYNAMIC_HOLD.update({
        "gesture": gesture,
        "gesture_name": gesture_name,
        "confidence": confidence,
        "event_id": event_id,
        "until": now + _OC_DYNAMIC_HOLD_SECONDS,
    })

    # 动态事件已经结束，清空旧轨迹，避免同一次动作连续触发。
    _OC_HISTORY.clear()
    _OC_STABLE_TRACK["gesture"] = ""
    _OC_STABLE_TRACK["count"] = 0
    return event_id


def _oc_dynamic_hold_result() -> dict | None:
    now = _oc_time.time()

    if (
        _OC_DYNAMIC_HOLD.get("gesture")
        and now < float(_OC_DYNAMIC_HOLD.get("until") or 0)
    ):
        return {
            "gesture": _OC_DYNAMIC_HOLD["gesture"],
            "gesture_name": _OC_DYNAMIC_HOLD["gesture_name"],
            "confidence": float(
                _OC_DYNAMIC_HOLD.get("confidence") or 0
            ),
            "phase": "hold",
            "is_event": False,
            "event_id": int(
                _OC_DYNAMIC_HOLD.get("event_id") or 0
            ),
            "debug": {
                "hold_remaining_ms": max(
                    0,
                    int(
                        (
                            float(_OC_DYNAMIC_HOLD["until"])
                            - now
                        )
                        * 1000
                    ),
                ),
            },
        }

    return None


def _oc_detect_dynamic_gesture(
    hand_center: dict,
    static_gesture: str,
    index_tip: dict,
) -> dict | None:
    _oc_update_history(
        hand_center,
        static_gesture,
        index_tip,
    )

    # 已确认的动作保持显示，不重复分析同一段轨迹。
    hold = _oc_dynamic_hold_result()
    if hold:
        return hold

    # 保持结束后先等待手掌静止复位，再开始下一次动作识别。
    if _OC_DYNAMIC_REARM_REQUIRED:
        if not _oc_try_rearm_dynamic(static_gesture):
            return _oc_waiting_next_result()

        # 本帧只完成复位，不立即拿旧姿势开始新动作。
        return {
            "gesture": "waiting_next",
            "gesture_name": "等待下一个动作",
            "confidence": 0.0,
            "phase": "ready",
            "is_event": False,
            "event_id": _OC_LAST_DYNAMIC_EVENT_ID,
            "debug": {
                "rearmed": True,
            },
        }

    circle_raw, circle_debug = _oc_detect_circle()
    if circle_raw:
        circle = _oc_mirror_camera_dynamic_gesture(
            circle_raw
        )
        name = (
            "单指顺时针画圈"
            if circle == "circle_clockwise"
            else "单指逆时针画圈"
        )
        circle_debug.update({
            "raw_gesture": circle_raw,
            "mirror_corrected_gesture": circle,
        })
        event_id = _oc_register_dynamic_event(
            circle,
            name,
            0.88,
        )
        return {
            "gesture": circle,
            "gesture_name": name,
            "confidence": 0.88,
            "phase": "event",
            "is_event": True,
            "event_id": event_id,
            "debug": circle_debug,
        }

    # 单向滑动先判断。检测到的是摄像头原始坐标方向，
    # 对外返回前进行镜像方向校正。
    swipe_raw, swipe_debug = _oc_detect_swipe()
    if swipe_raw:
        swipe = _oc_mirror_camera_dynamic_gesture(
            swipe_raw
        )
        name = (
            "向左滑动"
            if swipe == "swipe_left"
            else "向右滑动"
        )
        swipe_debug.update({
            "raw_gesture": swipe_raw,
            "mirror_corrected_gesture": swipe,
        })
        event_id = _oc_register_dynamic_event(
            swipe,
            name,
            0.88,
        )
        return {
            "gesture": swipe,
            "gesture_name": name,
            "confidence": 0.88,
            "phase": "event",
            "is_event": True,
            "event_id": event_id,
            "debug": swipe_debug,
        }

    wave, wave_debug = _oc_detect_wave()
    if wave:
        event_id = _oc_register_dynamic_event(
            "wave",
            "挥手",
            0.89,
        )
        return {
            "gesture": "wave",
            "gesture_name": "挥手",
            "confidence": 0.89,
            "phase": "event",
            "is_event": True,
            "event_id": event_id,
            "debug": wave_debug,
        }

    pending, pending_debug = _oc_motion_pending_debug()
    if pending:
        return {
            "gesture": "motion_pending",
            "gesture_name": "内部轨迹采集中",
            "confidence": 0.55,
            "phase": "pending",
            "is_event": False,
            "event_id": 0,
            "debug": pending_debug,
        }

    return None



def _oc_resolve_camera_gesture(
    hand_landmarks,
) -> dict:
    static_gesture, static_name, confidence, features = (
        _oc_classify_static_hand(hand_landmarks)
    )

    hand_center = _oc_palm_center(hand_landmarks)
    index_tip = _oc_index_tip(hand_landmarks)

    dynamic_result = _oc_detect_dynamic_gesture(
        hand_center,
        static_gesture,
        index_tip,
    )

    dynamic_phase = (
        dynamic_result.get("phase")
        if dynamic_result
        else "idle"
    )
    dynamic_is_event = bool(
        dynamic_result
        and dynamic_result.get("is_event")
    )

    if dynamic_result:
        gesture = str(dynamic_result["gesture"])
        gesture_name = str(
            dynamic_result["gesture_name"]
        )
        confidence = float(
            dynamic_result.get("confidence") or confidence
        )
    else:
        gesture = static_gesture
        gesture_name = static_name

    if dynamic_phase == "event":
        confirmed = True
        stable_count = 1
        required_stable_frames = 1
    elif dynamic_phase in {
        "pending",
        "hold",
        "waiting",
        "ready",
    }:
        confirmed = False
        stable_count = 0
        required_stable_frames = 1
    else:
        (
            confirmed,
            stable_count,
            required_stable_frames,
        ) = _oc_confirm_gesture(
            gesture,
            dynamic=False,
        )

    if dynamic_phase == "pending":
        action = "none"
        description = "内部正在采集动作轨迹"
        vehicle_state = dict(_OC_VEHICLE_STATE)
        triggered = False
        cooldown_remaining_ms = 0
    elif dynamic_phase == "hold":
        action = "none"
        description = "动作已确认，结果保持中"
        vehicle_state = dict(_OC_VEHICLE_STATE)
        triggered = False
        cooldown_remaining_ms = 0
    elif dynamic_phase in {"waiting", "ready"}:
        action = "none"
        description = "等待下一个完整动作"
        vehicle_state = dict(_OC_VEHICLE_STATE)
        triggered = False
        cooldown_remaining_ms = 0
    else:
        (
            action,
            description,
            vehicle_state,
            triggered,
            cooldown_remaining_ms,
        ) = _oc_apply_vehicle_action(
            gesture,
            confirmed=confirmed,
            dynamic=dynamic_is_event,
            stable_count=stable_count,
            required_stable_frames=required_stable_frames,
        )

    return {
        "gesture": gesture,
        "gesture_name": gesture_name,
        "static_gesture": static_gesture,
        "static_gesture_name": static_name,
        "confidence": round(float(confidence), 4),
        "features": features,
        "hand_center": hand_center,
        "index_tip": index_tip,
        "action": action,
        "description": description,
        "vehicle_state": vehicle_state,
        "triggered": triggered,
        "stable_count": stable_count,
        "required_stable_frames": required_stable_frames,
        "dynamic_gesture": (
            gesture
            if dynamic_phase in {"event", "hold"}
            else ""
        ),
        "dynamic_phase": dynamic_phase,
        "event_id": event_id,
        "display_committed": bool(
            triggered or dynamic_phase == "event"
        ),
        "camera_mirror_corrected": (
            _OC_CAMERA_MIRROR_DIRECTIONS
        ),
        "motion_state": (
            "tracking"
            if dynamic_phase == "pending"
            else (
                "waiting"
                if dynamic_phase in {"waiting", "ready"}
                else dynamic_phase
            )
        ),
        "motion_debug": (
            dynamic_result.get("debug", {})
            if dynamic_result
            else {}
        ),
        "cooldown_remaining_ms": cooldown_remaining_ms,
    }


def _oc_confirm_gesture(
    gesture: str,
    *,
    dynamic: bool = False,
) -> tuple[bool, int, int]:
    global _OC_STATIC_LATCH_GESTURE

    required_frames = int(
        _OC_STATIC_REQUIRED_FRAMES.get(
            gesture,
            _OC_STATIC_STABLE_FRAMES,
        )
    )

    if gesture in {
        "",
        "unknown",
        "no_hand",
        "motion_pending",
        "waiting_next",
        "one",
        "two",
        "ok",
    }:
        _OC_STABLE_TRACK["gesture"] = gesture
        _OC_STABLE_TRACK["count"] = 0
        return False, 0, required_frames

    if dynamic:
        _OC_STABLE_TRACK["gesture"] = gesture
        _OC_STABLE_TRACK["count"] = 1
        return True, 1, 1

    if _OC_STABLE_TRACK.get("gesture") == gesture:
        _OC_STABLE_TRACK["count"] = (
            int(_OC_STABLE_TRACK.get("count") or 0) + 1
        )
    else:
        _OC_STABLE_TRACK["gesture"] = gesture
        _OC_STABLE_TRACK["count"] = 1

    stable_count = int(_OC_STABLE_TRACK["count"])

    if stable_count < required_frames:
        return False, stable_count, required_frames

    # 同一个姿势一直保持时只确认一次。
    if _OC_STATIC_LATCH_GESTURE == gesture:
        return False, stable_count, required_frames

    _OC_STATIC_LATCH_GESTURE = gesture
    return True, stable_count, required_frames



def _oc_next_function(direction: int) -> str:
    current = _OC_VEHICLE_STATE.get("current_function") or "home"

    try:
        index = _OC_FUNCTION_ORDER.index(current)
    except ValueError:
        index = 0

    index = (index + direction) % len(_OC_FUNCTION_ORDER)
    return _OC_FUNCTION_ORDER[index]


def _oc_classify_static_hand(hand_landmarks) -> tuple[str, str, float, dict]:
    lms = hand_landmarks.landmark

    wrist = lms[0]
    thumb_tip = lms[4]
    thumb_ip = lms[3]
    thumb_mcp = lms[2]
    index_mcp = lms[5]
    index_tip = lms[8]

    index_extended = _oc_finger_extended(lms, 8, 6, 5)
    middle_extended = _oc_finger_extended(lms, 12, 10, 9)
    ring_extended = _oc_finger_extended(lms, 16, 14, 13)
    pinky_extended = _oc_finger_extended(lms, 20, 18, 17)

    extended_map = {
        "index": index_extended,
        "middle": middle_extended,
        "ring": ring_extended,
        "pinky": pinky_extended,
    }

    extended_count = sum(1 for value in extended_map.values() if value)

    thumb_index_distance = _oc_dist(thumb_tip, index_tip)
    thumb_to_wrist = _oc_dist(thumb_tip, wrist)
    palm_size = max(_oc_dist(wrist, lms[9]), 1e-6)

    # 收紧 thumb_up / thumb_down：
    # 只有拇指明显竖直向上/向下，且四指没有伸出，才判定为拇指手势。
    thumb_far_enough = thumb_to_wrist > palm_size * 0.72
    thumb_up = (
        extended_count == 0
        and thumb_far_enough
        and thumb_tip.y < wrist.y - 0.13
        and thumb_tip.y < index_mcp.y - 0.06
        and thumb_tip.y < thumb_ip.y - 0.025
    )
    thumb_down = (
        extended_count == 0
        and thumb_far_enough
        and thumb_tip.y > wrist.y + 0.13
        and thumb_tip.y > index_mcp.y + 0.06
        and thumb_tip.y > thumb_ip.y + 0.025
    )

    ok_gesture = (
        thumb_index_distance < 0.075
        and middle_extended
        and ring_extended
        and pinky_extended
    )

    features = {
        "index_extended": index_extended,
        "middle_extended": middle_extended,
        "ring_extended": ring_extended,
        "pinky_extended": pinky_extended,
        "non_thumb_extended_count": extended_count,
        "thumb_index_distance": round(thumb_index_distance, 4),
        "thumb_to_wrist": round(thumb_to_wrist, 4),
        "palm_size": round(palm_size, 4),
        "thumb_up_condition": bool(thumb_up),
        "thumb_down_condition": bool(thumb_down),
    }

    if ok_gesture:
        return "ok", "OK 手势", 0.86, features

    if thumb_up:
        return "thumb_up", "拇指向上", 0.84, features

    if thumb_down:
        return "thumb_down", "拇指向下", 0.84, features

    if extended_count >= 4:
        return "open_palm", "手掌张开", 0.88, features

    # 握拳优先于 one/two 之外的模糊状态，避免误判为 thumb_up。
    if extended_count == 0:
        return "fist", "握拳", 0.82, features

    if index_extended and not middle_extended and not ring_extended and not pinky_extended:
        return "one", "单指", 0.80, features

    if index_extended and middle_extended and not ring_extended and not pinky_extended:
        return "two", "双指", 0.80, features

    if extended_count >= 3:
        return "open_palm", "手掌张开", 0.76, features

    return "unknown", "未识别手势", 0.45, features


def _oc_apply_vehicle_action(
    gesture: str,
    *,
    confirmed: bool,
    dynamic: bool,
    stable_count: int,
    required_stable_frames: int,
) -> tuple[str, str, dict, bool, int]:
    state = _OC_VEHICLE_STATE
    now = _oc_time.time()
    now_text = _oc_now_text()

    action = "none"
    description = "未触发车辆控制"
    triggered = False
    cooldown_remaining_ms = 0

    if not confirmed:
        if gesture == "one":
            description = "保持单指并画圈以调节音量"
        elif gesture in {"two", "ok"}:
            description = "该静态手势不执行车辆控制"
        elif gesture not in {"", "unknown", "no_hand"}:
            description = (
                f"等待手势稳定确认 "
                f"({stable_count}/{required_stable_frames})"
            )

        state["last_action"] = action
        state["last_description"] = description
        state["updated_at"] = now_text
        return action, description, dict(state), triggered, cooldown_remaining_ms

    action_map = {
        "open_palm": "wake_system",
        "fist": "confirm",
        "circle_clockwise": "volume_up",
        "circle_counterclockwise": "volume_down",
        "swipe_left": "previous_function",
        "swipe_right": "next_function",
        "thumb_up": "answer_call",
        "thumb_down": "hang_up_call",
        "wave": "back_home",
        # 兼容旧版 OK 手势，但不作为教师要求的六类主手势。
        "ok": "confirm",
    }

    action = action_map.get(gesture, "none")
    if action == "none":
        state["last_action"] = action
        state["last_description"] = description
        state["updated_at"] = now_text
        return action, description, dict(state), triggered, cooldown_remaining_ms

    last_time = float(_OC_LAST_ACTION_AT.get(action) or 0)
    elapsed = now - last_time

    if elapsed < _OC_ACTION_COOLDOWN_SECONDS:
        cooldown_remaining_ms = max(
            0,
            int(round((_OC_ACTION_COOLDOWN_SECONDS - elapsed) * 1000)),
        )
        action = "cooldown"
        description = f"动作冷却中，还需等待约 {cooldown_remaining_ms} ms"
        state["last_action"] = action
        state["last_description"] = description
        state["updated_at"] = now_text
        return action, description, dict(state), triggered, cooldown_remaining_ms

    state["system_awake"] = True

    if gesture == "open_palm":
        state["current_function"] = "home"
        description = "系统已启动并唤醒"

    elif gesture in {"fist", "ok"}:
        description = "已确认并执行当前功能"

    elif gesture == "circle_clockwise":
        state["current_function"] = "media"
        state["volume"] = min(100, int(state.get("volume", 50)) + 5)
        description = f"音量已调高至 {state['volume']}"

    elif gesture == "circle_counterclockwise":
        state["current_function"] = "media"
        state["volume"] = max(0, int(state.get("volume", 50)) - 5)
        description = f"音量已调低至 {state['volume']}"

    elif gesture == "swipe_left":
        state["current_function"] = _oc_next_function(-1)
        description = f"已切换到上一功能：{state['current_function']}"

    elif gesture == "swipe_right":
        state["current_function"] = _oc_next_function(1)
        description = f"已切换到下一功能：{state['current_function']}"

    elif gesture == "thumb_up":
        state["current_function"] = "phone"
        state["phone_status"] = "通话中"
        description = "已接听电话"

    elif gesture == "thumb_down":
        state["current_function"] = "phone"
        state["phone_status"] = "已挂断"
        description = "已挂断电话"

    elif gesture == "wave":
        state["current_function"] = "home"
        description = "已返回主页"

    _OC_LAST_ACTION_AT[action] = now
    state["last_action"] = action
    state["last_description"] = description
    state["updated_at"] = now_text
    triggered = True

    if dynamic:
        _OC_HISTORY.clear()

    return action, description, dict(state), triggered, cooldown_remaining_ms


@app.get("/api/gesture/owner/mapping")
def get_owner_gesture_mapping():
    return {
        "status": "success",
        "version": _OC_GESTURE_MAPPING_VERSION,
        "minimum_supported_gestures": 6,
        "items": _OC_GESTURE_MAPPING,
        "vehicle_state": dict(_OC_VEHICLE_STATE),
        "misoperation_suppression": {
            "static_stable_frames": _OC_STATIC_STABLE_FRAMES,
            "action_cooldown_seconds": _OC_ACTION_COOLDOWN_SECONDS,
            "dynamic_trajectory_window_seconds": _OC_HISTORY_MAX_SECONDS,
            "missing_hand_tolerance_frames": _OC_MISSING_HAND_RESET_AFTER - 1,
            "dynamic_result_hold_seconds": _OC_DYNAMIC_HOLD_SECONDS,
            "camera_mirror_direction_correction": _OC_CAMERA_MIRROR_DIRECTIONS,
            "dynamic_rearm_still_seconds": _OC_DYNAMIC_REARM_STILL_SECONDS,
            "display_policy": "动作确认后才更新结果面板；确认后等待静止复位",
            "swipe_policy": "镜像校正后输出用户实际方向；单向横移并停顿",
            "wave_policy": "张开手掌至少完成一次横向往返；一次动作只触发一次",
        },
    }


@app.post("/api/gesture/owner/reset-state")
def reset_owner_vehicle_state():
    _OC_VEHICLE_STATE.update({
        "system_awake": False,
        "current_function": "home",
        "volume": 50,
        "temperature": 24,
        "phone_status": "空闲",
        "last_action": "none",
        "last_description": "车辆交互状态已重置",
        "updated_at": _oc_now_text(),
    })
    _OC_LAST_ACTION_AT.clear()
    _oc_reset_tracking()

    return {
        "status": "success",
        "message": "车主手势控制状态已重置",
        "vehicle_state": dict(_OC_VEHICLE_STATE),
    }


@app.post("/api/gesture/owner/camera-frame")
def recognize_owner_gesture_camera_frame(file: _OCUploadFile = _OCFile(...)):
    """
    电脑摄像头实时帧识别接口。

    与 /api/gesture/owner/image 的区别：
    - 专门用于前端摄像头循环抽帧
    - 保留连续帧轨迹，用于识别 wave 挥手
    - 收紧 thumb_up 规则，降低握拳误判
    """
    start_time = _oc_time.perf_counter()

    try:
        content = file.file.read()

        if not content:
            raise _OCHTTPException(status_code=400, detail="上传图片为空。")

        image_array = _oc_np.frombuffer(content, dtype=_oc_np.uint8)
        image_bgr = _oc_cv2.imdecode(image_array, _oc_cv2.IMREAD_COLOR)

        if image_bgr is None:
            raise _OCHTTPException(status_code=400, detail="无法解析摄像头帧图片。")

        height, width = image_bgr.shape[:2]

        saved_name = f"owner_camera_{_oc_uuid.uuid4().hex}.jpg"
        output_name = f"annotated_owner_camera_{_oc_uuid.uuid4().hex}.jpg"

        saved_path = _oc_upload_dir() / saved_name
        output_path = _oc_output_dir() / output_name

        _oc_cv2.imwrite(str(saved_path), image_bgr)

        image_rgb = _oc_cv2.cvtColor(image_bgr, _oc_cv2.COLOR_BGR2RGB)
        image_rgb.flags.writeable = False
        hands = _oc_get_hands()
        with _OC_HANDS_PROCESS_LOCK:
            result = hands.process(image_rgb)

        annotated = image_bgr.copy()

        if not result.multi_hand_landmarks:
            tracking_reset = _oc_note_missing_hand()
            latency_ms = round((_oc_time.perf_counter() - start_time) * 1000, 2)
            _oc_cv2.imwrite(str(output_path), annotated)

            return {
                "status": "success",
                "record_id": None,
                "input_type": "camera_frame",
                "original_filename": file.filename,
                "saved_filename": saved_name,
                "image_url": f"/uploads/{saved_name}",
                "output_image_url": f"/outputs/{output_name}",
                "latency_ms": latency_ms,
                "result": {
                    "model": "MediaPipe Hands",
                    "gesture": "no_hand",
                    "gesture_name": "未检测到手部",
                    "confidence": 0,
                    "action": "none",
                    "description": "未检测到手部",
                    "vehicle_state": dict(_OC_VEHICLE_STATE),
                    "landmarks": [],
                    "hand_center": None,
                    "camera_mode": True,
                    "motion_state": (
                        "reset"
                        if tracking_reset
                        else "temporarily_lost"
                    ),
                    "missing_hand_frames": _OC_MISSING_HAND_FRAMES,
                },
            }

        hand_landmarks = result.multi_hand_landmarks[0]
        _oc_note_hand_detected()

        resolved = _oc_resolve_camera_gesture(
            hand_landmarks,
        )

        gesture = resolved["gesture"]
        gesture_name = resolved["gesture_name"]
        static_gesture = resolved["static_gesture"]
        static_name = resolved["static_gesture_name"]
        confidence = resolved["confidence"]
        features = resolved["features"]
        hand_center = resolved["hand_center"]
        action = resolved["action"]
        description = resolved["description"]
        vehicle_state = resolved["vehicle_state"]
        triggered = resolved["triggered"]
        stable_count = resolved["stable_count"]
        required_stable_frames = resolved[
            "required_stable_frames"
        ]
        dynamic_gesture = resolved["dynamic_gesture"]
        cooldown_remaining_ms = resolved[
            "cooldown_remaining_ms"
        ]

        if _OC_MP is not None:
            _OC_MP.solutions.drawing_utils.draw_landmarks(
                annotated,
                hand_landmarks,
                _OC_MP.solutions.hands.HAND_CONNECTIONS,
            )

        _oc_cv2.imwrite(str(output_path), annotated)

        latency_ms = round((_oc_time.perf_counter() - start_time) * 1000, 2)

        return {
            "status": "success",
            "record_id": None,
            "input_type": "camera_frame",
            "original_filename": file.filename,
            "saved_filename": saved_name,
            "image_url": f"/uploads/{saved_name}",
            "output_image_url": f"/outputs/{output_name}",
            "latency_ms": latency_ms,
            "result": {
                "model": "MediaPipe Hands",
                "gesture": gesture,
                "gesture_name": gesture_name,
                "static_gesture": static_gesture,
                "static_gesture_name": static_name,
                "confidence": confidence,
                "handedness": (
                    result.multi_handedness[0].classification[0].label
                    if result.multi_handedness else ""
                ),
                "landmarks": _oc_to_landmark_dicts(hand_landmarks, width, height),
                "finger_features": features,
                "hand_center": hand_center,
                "action": action,
                "description": description,
                "triggered": triggered,
                "stable_count": stable_count,
                "required_stable_frames": required_stable_frames,
                "dynamic_gesture": dynamic_gesture or "",
                "dynamic_phase": resolved["dynamic_phase"],
                "event_id": resolved["event_id"],
                "display_committed": resolved["display_committed"],
                "camera_mirror_corrected": resolved[
                    "camera_mirror_corrected"
                ],
                "motion_state": resolved["motion_state"],
                "motion_debug": resolved["motion_debug"],
                "index_tip": resolved["index_tip"],
                "cooldown_remaining_ms": cooldown_remaining_ms,
                "gesture_mapping_version": _OC_GESTURE_MAPPING_VERSION,
                "supported_gestures": _OC_GESTURE_MAPPING,
                "vehicle_state": vehicle_state,
                "camera_mode": True,
                "dynamic_policy": "V4：摄像头镜像方向校正；动作确认后单次触发并等待复位",
            },
        }

    except _OCHTTPException:
        raise
    except Exception as exc:
        raise _OCHTTPException(status_code=500, detail=f"摄像头手势识别失败：{exc}")


# OWNER_CAMERA_FAST_ENDPOINT_V1
# 车主手势：电脑摄像头快速实时帧接口
@app.post("/api/gesture/owner/camera-fast-frame")
def recognize_owner_gesture_camera_fast_frame(file: _OCUploadFile = _OCFile(...)):
    """
    电脑摄像头快速识别接口。

    与 /api/gesture/owner/camera-frame 的区别：
    - 不保存上传图片
    - 不绘制/保存骨架图
    - 只返回手势结果、车辆控制状态、关键点坐标和延迟
    - 适合前端高频实时识别
    """
    start_time = _oc_time.perf_counter()

    try:
        content = file.file.read()

        if not content:
            raise _OCHTTPException(status_code=400, detail="上传图片为空。")

        image_array = _oc_np.frombuffer(content, dtype=_oc_np.uint8)
        image_bgr = _oc_cv2.imdecode(image_array, _oc_cv2.IMREAD_COLOR)

        if image_bgr is None:
            raise _OCHTTPException(status_code=400, detail="无法解析摄像头帧图片。")

        height, width = image_bgr.shape[:2]

        image_rgb = _oc_cv2.cvtColor(image_bgr, _oc_cv2.COLOR_BGR2RGB)
        image_rgb.flags.writeable = False
        hands = _oc_get_hands()
        with _OC_HANDS_PROCESS_LOCK:
            result = hands.process(image_rgb)

        latency_ms = round((_oc_time.perf_counter() - start_time) * 1000, 2)

        if not result.multi_hand_landmarks:
            tracking_reset = _oc_note_missing_hand()

            return {
                "status": "success",
                "record_id": None,
                "input_type": "camera_fast_frame",
                "output_image_url": "",
                "latency_ms": latency_ms,
                "result": {
                    "model": "MediaPipe Hands",
                    "gesture": "no_hand",
                    "gesture_name": "未检测到手部",
                    "confidence": 0,
                    "action": "none",
                    "description": "未检测到手部",
                    "vehicle_state": dict(_OC_VEHICLE_STATE),
                    "landmarks": [],
                    "hand_center": None,
                    "camera_mode": True,
                    "fast_mode": True,
                    "motion_state": (
                        "reset"
                        if tracking_reset
                        else "temporarily_lost"
                    ),
                    "missing_hand_frames": _OC_MISSING_HAND_FRAMES,
                },
            }

        hand_landmarks = result.multi_hand_landmarks[0]
        _oc_note_hand_detected()

        resolved = _oc_resolve_camera_gesture(
            hand_landmarks,
        )

        gesture = resolved["gesture"]
        gesture_name = resolved["gesture_name"]
        static_gesture = resolved["static_gesture"]
        static_name = resolved["static_gesture_name"]
        confidence = resolved["confidence"]
        features = resolved["features"]
        hand_center = resolved["hand_center"]
        action = resolved["action"]
        description = resolved["description"]
        vehicle_state = resolved["vehicle_state"]
        triggered = resolved["triggered"]
        stable_count = resolved["stable_count"]
        required_stable_frames = resolved[
            "required_stable_frames"
        ]
        dynamic_gesture = resolved["dynamic_gesture"]
        cooldown_remaining_ms = resolved[
            "cooldown_remaining_ms"
        ]

        return {
            "status": "success",
            "record_id": None,
            "input_type": "camera_fast_frame",
            "output_image_url": "",
            "latency_ms": latency_ms,
            "result": {
                "model": "MediaPipe Hands",
                "gesture": gesture,
                "gesture_name": gesture_name,
                "static_gesture": static_gesture,
                "static_gesture_name": static_name,
                "confidence": confidence,
                "handedness": (
                    result.multi_handedness[0].classification[0].label
                    if result.multi_handedness else ""
                ),
                "landmarks": _oc_to_landmark_dicts(hand_landmarks, width, height),
                "finger_features": features,
                "hand_center": hand_center,
                "action": action,
                "description": description,
                "triggered": triggered,
                "stable_count": stable_count,
                "required_stable_frames": required_stable_frames,
                "dynamic_gesture": dynamic_gesture or "",
                "dynamic_phase": resolved["dynamic_phase"],
                "event_id": resolved["event_id"],
                "display_committed": resolved["display_committed"],
                "camera_mirror_corrected": resolved[
                    "camera_mirror_corrected"
                ],
                "motion_state": resolved["motion_state"],
                "motion_debug": resolved["motion_debug"],
                "index_tip": resolved["index_tip"],
                "cooldown_remaining_ms": cooldown_remaining_ms,
                "gesture_mapping_version": _OC_GESTURE_MAPPING_VERSION,
                "supported_gestures": _OC_GESTURE_MAPPING,
                "vehicle_state": vehicle_state,
                "camera_mode": True,
                "fast_mode": True,
                "dynamic_policy": "V4 快速模式：内部采集不刷新结果面板；确认后输出一次并等待下一个动作",
            },
        }

    except _OCHTTPException:
        raise
    except Exception as exc:
        raise _OCHTTPException(status_code=500, detail=f"摄像头快速手势识别失败：{exc}")


# TRAFFIC_GESTURE_CAMERA_FAST_ENDPOINT_V1
# 交警手势：电脑摄像头实时帧接口
@app.post("/api/gesture/traffic/camera-fast-frame")
def recognize_traffic_gesture_camera_fast_frame(file: _OCUploadFile = _OCFile(...)):
    """
    交警手势电脑摄像头快速识别接口。

    接收前端摄像头抽帧图片，调用 MediaPipe Pose 进行全身姿态检测，
    通过规则引擎识别 8 类交警手势（停止/直行/左转/右转/变道/左转待转/减速/靠边停车）。

    与 /api/gesture/traffic/image 的区别：
    - 不保存上传图片
    - 不保存标注图片（前端自行绘制骨架）
    - 只返回姿态关键点、手势结果和延迟
    - 适合前端高频实时识别（~400ms 间隔）
    """
    start_time = _oc_time.perf_counter()

    try:
        content = file.file.read()

        if not content:
            raise _OCHTTPException(status_code=400, detail="上传图片为空。")

        image_array = _oc_np.frombuffer(content, dtype=_oc_np.uint8)
        image_bgr = _oc_cv2.imdecode(image_array, _oc_cv2.IMREAD_COLOR)

        if image_bgr is None:
            raise _OCHTTPException(status_code=400, detail="无法解析摄像头帧图片。")

        # 调用交警手势识别（不保存标注图片，前端自己画骨架）
        # static_image_mode=False 启用 MediaPipe 帧间平滑
        result_data = recognize_traffic_gesture_frame(
            image_bgr,
            output_path=None,
            static_image_mode=False,
            draw_annotation=False,
        )

        latency_ms = round((_oc_time.perf_counter() - start_time) * 1000, 2)

        return {
            "status": "success",
            "record_id": None,
            "input_type": "camera_fast_frame",
            "output_image_url": "",
            "latency_ms": latency_ms,
            "result": {
                **result_data,
                "camera_mode": True,
                "fast_mode": True,
            },
        }

    except _OCHTTPException:
        raise
    except Exception as exc:
        raise _OCHTTPException(status_code=500, detail=f"交警手势摄像头识别失败：{exc}")


# REALTIME_FUSION_MONITOR_PATCH_V1
# 实时融合决策监控接口：接收前端当前轮次三路识别证据，立即生成风险分析
from fastapi import Body as _RFMBody, Query as _RFMQuery, HTTPException as _RFMHTTPException
from pathlib import Path as _RFMPath
from datetime import datetime as _RFMDateTime
import sqlite3 as _rfm_sqlite3
import json as _rfm_json
import uuid as _rfm_uuid


def _rfm_now_text() -> str:
    return _RFMDateTime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rfm_db_path() -> _RFMPath:
    return _RFMPath(__file__).resolve().parent / "data" / "app.db"


def _rfm_connect():
    path = _rfm_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _rfm_sqlite3.connect(str(path))
    conn.row_factory = _rfm_sqlite3.Row
    return conn


def _rfm_ensure_table():
    with _rfm_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_monitor_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT,
                scenario TEXT,
                risk_level TEXT,
                risk_score INTEGER,
                suggestion TEXT,
                reason TEXT,
                control_advice TEXT,
                evidence_json TEXT,
                decision_json TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fusion_monitor_anomaly_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key INTEGER NOT NULL,
                user_id INTEGER,
                source_id TEXT NOT NULL,
                source_name TEXT,
                anomaly_type TEXT NOT NULL,
                consecutive_count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 0,
                last_alert_id INTEGER,
                last_reason TEXT,
                last_seen_at TEXT,
                last_alert_at TEXT,
                recovered_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(user_key, source_id, anomaly_type)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fusion_anomaly_active
            ON fusion_monitor_anomaly_state(
                user_key,
                active,
                anomaly_type
            )
            """
        )
        conn.commit()


def _rfm_to_dict(value):
    return value if isinstance(value, dict) else {}


def _rfm_result_of(channel: dict) -> dict:
    """递归拆开 channel/result/data/summary，只读取本轮接口真实返回。"""
    current = _rfm_to_dict(channel)
    visited = set()

    while isinstance(current, dict) and id(current) not in visited:
        visited.add(id(current))
        next_value = None
        for key in ("result", "data", "summary", "result_summary"):
            candidate = current.get(key)
            if isinstance(candidate, dict):
                next_value = candidate
                break
        if next_value is None:
            break
        current = next_value

    return current if isinstance(current, dict) else {}


def _rfm_channel_ok(channel: dict) -> bool:
    channel = _rfm_to_dict(channel)
    if not channel:
        return False
    if str(channel.get("status") or "success").lower() not in {"success", "ok"}:
        return False
    if channel.get("error") or channel.get("detail"):
        return False
    return True


def _rfm_extract_plate(plate_channel: dict) -> dict:
    channel = _rfm_to_dict(plate_channel)
    result = _rfm_result_of(channel)
    raw_plates = result.get("plates") or result.get("stable_plates") or []
    plates = [item for item in raw_plates if isinstance(item, dict)]

    def confidence(item):
        try:
            return float(item.get("confidence") or item.get("avg_confidence") or 0)
        except Exception:
            return 0.0

    best = max(plates, key=confidence, default={})
    best_plate = (
        best.get("plate_number")
        or best.get("plate")
        or best.get("text")
        or result.get("best_plate")
        or result.get("best_plate_text")
        or ""
    )
    valid = _rfm_channel_ok(channel) and bool(best_plate)

    return {
        "available": valid,
        "detected": valid,
        "source_id": channel.get("source_id") or result.get("source_id") or "",
        "input_type": channel.get("input_type") or result.get("input_type") or "",
        "latency_ms": channel.get("latency_ms") or result.get("latency_ms"),
        "plate_count": len(plates) if plates else int(result.get("plate_count") or 0),
        "best_plate": str(best_plate),
        "best_plate_color": str(
            best.get("plate_color")
            or best.get("color")
            or result.get("best_plate_color")
            or "未知颜色"
        ) if best_plate else "",
        "best_confidence": confidence(best) if best else result.get("best_confidence"),
        "plates": plates,
        "raw": channel,
    }


def _rfm_extract_traffic(traffic_channel: dict) -> dict:
    channel = _rfm_to_dict(traffic_channel)
    result = _rfm_result_of(channel)
    gesture = str(result.get("gesture") or "").strip()
    valid = (
        _rfm_channel_ok(channel)
        and gesture not in {"", "unknown", "no_pose", "none"}
        and bool(result.get("landmarks") or result.get("pose_detected", True))
    )

    return {
        "available": valid,
        "detected": valid,
        "source_id": channel.get("source_id") or result.get("source_id") or "camera",
        "input_type": channel.get("input_type") or result.get("input_type") or "",
        "latency_ms": channel.get("latency_ms") or result.get("latency_ms"),
        "gesture": gesture if valid else "",
        "gesture_name": str(result.get("gesture_name") or "") if valid else "",
        "traffic_command": str(result.get("traffic_command") or result.get("command") or "") if valid else "",
        "confidence": result.get("confidence") if valid else None,
        "raw": channel,
    }


def _rfm_extract_owner(owner_channel: dict) -> dict:
    channel = _rfm_to_dict(owner_channel)
    result = _rfm_result_of(channel)
    gesture = str(result.get("gesture") or "").strip()
    valid = (
        _rfm_channel_ok(channel)
        and gesture not in {"", "unknown", "no_hand", "none"}
    )

    return {
        "available": valid,
        "detected": valid,
        "source_id": channel.get("source_id") or result.get("source_id") or "camera",
        "input_type": channel.get("input_type") or result.get("input_type") or "camera_fast_frame",
        "latency_ms": channel.get("latency_ms") or result.get("latency_ms"),
        "gesture": gesture if valid else "",
        "gesture_name": str(result.get("gesture_name") or "") if valid else "",
        "action": str(result.get("action") or "") if valid else "",
        "description": str(result.get("description") or "") if valid else "",
        "confidence": result.get("confidence") if valid else None,
        "vehicle_state": (result.get("vehicle_state") or {}) if valid else {},
        "raw": channel,
    }


def _rfm_level(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _rfm_decide(evidence: dict) -> dict:
    """只依据当前轮次真实识别结果生成决策，不补写不存在的事件。"""
    plate = _rfm_extract_plate(evidence.get("plate"))
    traffic = _rfm_extract_traffic(evidence.get("traffic"))
    owner = _rfm_extract_owner(evidence.get("owner"))

    score = 0
    observed_events = []
    reasons = []

    if plate["detected"]:
        observed_events.append({
            "channel": "plate",
            "event": "plate_detected",
            "text": (
                f"检测到车牌 {plate['best_plate']}"
                f"（{plate.get('best_plate_color') or '未知颜色'}）"
            ),
            "confidence": plate["best_confidence"],
        })
        reasons.append(
            f"车牌通道实际检测到 {plate['best_plate']}"
            f"（{plate.get('best_plate_color') or '未知颜色'}）。"
        )
        score += 10

    traffic_gesture = traffic["gesture"]
    traffic_name = traffic["gesture_name"] or traffic_gesture
    if traffic["detected"]:
        observed_events.append({
            "channel": "traffic",
            "event": traffic_gesture,
            "text": f"交警手势：{traffic_name}",
            "confidence": traffic["confidence"],
        })
        reasons.append(f"交警摄像头实际识别为 {traffic_name}。")
        if traffic_gesture == "stop" or "停止" in traffic_name:
            score += 55
        elif traffic_gesture in {"lane_change", "left_turn", "right_turn", "left_turn_wait"}:
            score += 32
        elif traffic_gesture in {"slow_down", "pull_over"}:
            score += 40
        else:
            score += 18

    owner_gesture = owner["gesture"]
    owner_action = owner["action"]
    if owner["detected"]:
        owner_text = owner["gesture_name"] or owner_gesture
        observed_events.append({
            "channel": "owner",
            "event": owner_gesture,
            "text": f"车主手势：{owner_text}",
            "confidence": owner["confidence"],
        })
        reasons.append(f"车主摄像头实际识别为 {owner_text}。")
        if owner_action in {"answer_call", "hang_up_call"}:
            score += 18
        else:
            score += 8

    has_plate = plate["detected"]
    stop_signal = traffic["detected"] and (
        traffic_gesture == "stop" or "停止" in traffic_name
    )
    direction_signal = traffic["detected"] and traffic_gesture in {
        "lane_change", "left_turn", "right_turn", "left_turn_wait"
    }

    if has_plate and stop_signal:
        score += 20
        scenario = f"检测到车牌 {plate['best_plate']}，同时识别到交警停止信号"
        suggestion = "立即减速并停车，等待交警进一步指挥。"
        control_advice = "decelerate_and_stop"
    elif stop_signal:
        scenario = "识别到交警停止信号"
        suggestion = "立即减速，准备停车并服从现场交通指挥。"
        control_advice = "prepare_to_stop"
    elif has_plate and direction_signal:
        score += 10
        scenario = f"检测到车牌 {plate['best_plate']}，同时识别到交警转向或变道信号"
        suggestion = "降低车速，确认周边安全后按交警指挥通行。"
        control_advice = "slow_down_and_follow_traffic_police"
    elif traffic["detected"]:
        scenario = f"识别到交警手势：{traffic_name}"
        suggestion = traffic["traffic_command"] or "按当前交警手势指令谨慎通行。"
        control_advice = "follow_traffic_police"
    elif has_plate:
        scenario = f"当前轮次检测到车牌 {plate['best_plate']}"
        suggestion = "保持安全车距并继续观察道路环境。"
        control_advice = "maintain_safe_distance"
    elif owner["detected"]:
        scenario = f"当前轮次识别到车主手势：{owner['gesture_name'] or owner_gesture}"
        suggestion = owner["description"] or "已记录车主交互动作，请保持驾驶注意力。"
        control_advice = "keep_attention"
    else:
        scenario = "本轮未检测到有效交通事件"
        suggestion = "当前轮次没有可用于决策的有效识别结果，继续等待下一轮真实感知输入。"
        control_advice = "continue_monitoring"

    score = max(0, min(100, int(score)))
    created_at = _rfm_now_text()

    return {
        "decision_id": f"fusion_monitor_{_rfm_uuid.uuid4().hex}",
        "agent": {
            "name": "RealtimeFusionMonitorAgent",
            "mode": "current_cycle_verified_evidence",
            "llm_enabled": False,
        },
        "scenario": scenario,
        "risk_level": _rfm_level(score),
        "risk_score": score,
        "suggestion": suggestion,
        "reason": "；".join(reasons) if reasons else "本轮三个通道均未返回有效检测事件。",
        "control_advice": control_advice,
        "observed_events": observed_events,
        "evidence_integrity": {
            "current_cycle_only": True,
            "fabricated_events": False,
            "valid_channel_count": sum([
                int(plate["detected"]),
                int(traffic["detected"]),
                int(owner["detected"]),
            ]),
        },
        "evidence_summary": {
            "plate": {key: plate[key] for key in ("available", "plate_count", "best_plate", "best_confidence", "latency_ms")},
            "traffic": {key: traffic[key] for key in ("available", "gesture", "gesture_name", "traffic_command", "confidence", "latency_ms")},
            "owner": {key: owner[key] for key in ("available", "gesture", "gesture_name", "action", "confidence", "latency_ms")},
        },
        "evidence": {"plate": plate, "traffic": traffic, "owner": owner},
        "created_at": created_at,
    }


def _rfm_save_decision(decision: dict) -> int:
    _rfm_ensure_table()

    with _rfm_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO fusion_monitor_decisions (
                decision_id,
                scenario,
                risk_level,
                risk_score,
                suggestion,
                reason,
                control_advice,
                evidence_json,
                decision_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.get("decision_id"),
                decision.get("scenario"),
                decision.get("risk_level"),
                int(decision.get("risk_score") or 0),
                decision.get("suggestion"),
                decision.get("reason"),
                decision.get("control_advice"),
                _rfm_json.dumps(decision.get("evidence") or {}, ensure_ascii=False),
                _rfm_json.dumps(decision, ensure_ascii=False),
                decision.get("created_at") or _rfm_now_text(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)



# 融合监控异常邮件策略：
# - 车牌通道连续 3 轮“读取成功但无有效车牌”才告警。
# - 视频源连续 2 轮读取失败才告警。
# - 同一异常持续期间只创建一次告警，恢复后才允许再次告警。
# - 未识别到交警/车主手势不视为异常，因为现场可能本来就没有手势。
_RFM_NO_PLATE_ALERT_ROUNDS = 3
_RFM_SOURCE_FAILURE_ALERT_ROUNDS = 2


def _rfm_user_key(user_id: int | None) -> int:
    return int(user_id) if user_id is not None else 0


def _rfm_get_anomaly_state(
    *,
    user_id: int | None,
    source_id: str,
    anomaly_type: str,
) -> dict:
    _rfm_ensure_table()

    with _rfm_connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM fusion_monitor_anomaly_state
            WHERE user_key = ?
              AND source_id = ?
              AND anomaly_type = ?
            """,
            (
                _rfm_user_key(user_id),
                str(source_id),
                str(anomaly_type),
            ),
        ).fetchone()

    return dict(row) if row else {}


def _rfm_mark_anomaly(
    *,
    user_id: int | None,
    source_id: str,
    source_name: str,
    anomaly_type: str,
    reason: str,
    threshold: int,
    level: str,
    event_type: str,
    summary: str,
    suggestion: str,
) -> dict:
    """
    递增异常连续次数。

    达到阈值且当前异常尚未激活时：
    1. 创建一条 alert_events；
    2. insert_alert_event 自动安排用户和管理员邮件；
    3. 标记 active，持续异常期间不重复创建。
    """
    _rfm_ensure_table()

    user_key = _rfm_user_key(user_id)
    source_id = str(source_id or "unknown_source")
    source_name = str(source_name or source_id)
    anomaly_type = str(anomaly_type)
    now = _rfm_now_text()
    threshold = max(1, int(threshold))

    with _rfm_connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM fusion_monitor_anomaly_state
            WHERE user_key = ?
              AND source_id = ?
              AND anomaly_type = ?
            """,
            (user_key, source_id, anomaly_type),
        ).fetchone()

        previous_count = int(
            row["consecutive_count"] or 0
        ) if row else 0
        active = bool(int(row["active"] or 0)) if row else False
        count = previous_count + 1
        should_create = count >= threshold and not active

        if row:
            conn.execute(
                """
                UPDATE fusion_monitor_anomaly_state
                SET
                    user_id = ?,
                    source_name = ?,
                    consecutive_count = ?,
                    active = ?,
                    last_reason = ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    user_id,
                    source_name,
                    count,
                    1 if (active or should_create) else 0,
                    reason,
                    now,
                    now,
                    int(row["id"]),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO fusion_monitor_anomaly_state (
                    user_key,
                    user_id,
                    source_id,
                    source_name,
                    anomaly_type,
                    consecutive_count,
                    active,
                    last_reason,
                    last_seen_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_key,
                    user_id,
                    source_id,
                    source_name,
                    anomaly_type,
                    count,
                    1 if should_create else 0,
                    reason,
                    now,
                    now,
                ),
            )

        conn.commit()

    alert_id = None

    if should_create:
        alert_id = insert_alert_event(
            level=level,
            event_type=event_type,
            summary=summary,
            reason=reason,
            suggestion=suggestion,
            related_record_id=None,
            user_id=user_id,
        )

        with _rfm_connect() as conn:
            conn.execute(
                """
                UPDATE fusion_monitor_anomaly_state
                SET
                    last_alert_id = ?,
                    last_alert_at = ?,
                    updated_at = ?
                WHERE user_key = ?
                  AND source_id = ?
                  AND anomaly_type = ?
                """,
                (
                    int(alert_id),
                    now,
                    now,
                    user_key,
                    source_id,
                    anomaly_type,
                ),
            )
            conn.commit()

    state = _rfm_get_anomaly_state(
        user_id=user_id,
        source_id=source_id,
        anomaly_type=anomaly_type,
    )

    return {
        "source_id": source_id,
        "source_name": source_name,
        "anomaly_type": anomaly_type,
        "consecutive_count": count,
        "threshold": threshold,
        "active": bool(
            int(state.get("active") or 0)
        ),
        "created": alert_id is not None,
        "alert_id": alert_id,
        "reason": reason,
    }


def _rfm_recover_anomaly(
    *,
    user_id: int | None,
    source_id: str,
    anomaly_type: str,
) -> dict:
    """
    感知恢复后清除异常锁。

    恢复只写状态和操作日志，不再额外发送恢复邮件，避免邮件过多。
    """
    state = _rfm_get_anomaly_state(
        user_id=user_id,
        source_id=source_id,
        anomaly_type=anomaly_type,
    )

    if not state:
        return {
            "recovered": False,
            "was_active": False,
        }

    was_active = bool(int(state.get("active") or 0))
    previous_count = int(
        state.get("consecutive_count") or 0
    )
    now = _rfm_now_text()

    with _rfm_connect() as conn:
        conn.execute(
            """
            UPDATE fusion_monitor_anomaly_state
            SET
                consecutive_count = 0,
                active = 0,
                recovered_at = ?,
                updated_at = ?
            WHERE user_key = ?
              AND source_id = ?
              AND anomaly_type = ?
            """,
            (
                now,
                now,
                _rfm_user_key(user_id),
                str(source_id),
                str(anomaly_type),
            ),
        )
        conn.commit()

    recovered = was_active or previous_count > 0

    if recovered:
        try:
            insert_operation_log(
                action="fusion_monitor_anomaly_recovered",
                detail={
                    "source_id": source_id,
                    "anomaly_type": anomaly_type,
                    "was_active": was_active,
                    "previous_count": previous_count,
                },
                user_id=user_id,
            )
        except Exception:
            pass

    return {
        "recovered": recovered,
        "was_active": was_active,
        "previous_count": previous_count,
    }


def _rfm_evaluate_monitor_anomalies(
    *,
    evidence: dict,
    monitor_context: dict,
    cycle: int,
) -> dict:
    """
    根据当前轮次结果判断是否需要创建告警和邮件通知。

    不把单轮空结果直接发邮件，避免 3~5 秒一封邮件。
    """
    user_id = current_request_user_id()
    created: list[dict] = []
    states: list[dict] = []
    recovered: list[dict] = []

    context = _rfm_to_dict(monitor_context)
    evidence = _rfm_to_dict(evidence)

    # ---------------------- 车牌通道 ----------------------
    plate_expected = bool(context.get("plate_expected"))
    plate_source_id = str(
        context.get("plate_source_id")
        or _rfm_to_dict(evidence.get("plate")).get("source_id")
        or "plate_source"
    )
    plate_source_name = str(
        context.get("plate_source_name")
        or plate_source_id
    )
    plate_error = str(
        context.get("plate_error") or ""
    ).strip()

    if plate_expected:
        if plate_error:
            # 源读取失败和“读取成功但没有车牌”分开统计。
            state = _rfm_mark_anomaly(
                user_id=user_id,
                source_id=plate_source_id,
                source_name=plate_source_name,
                anomaly_type="plate_source_failed",
                reason=(
                    f"融合监控车牌视频源“{plate_source_name}”"
                    f"连续读取失败，本轮错误：{plate_error}"
                ),
                threshold=_RFM_SOURCE_FAILURE_ALERT_ROUNDS,
                level="error",
                event_type="fusion_plate_source_failed",
                summary="融合监控车牌视频源连续读取失败",
                suggestion=(
                    "建议检查老师 RTSP 是否在线、MediaMTX 与 ffmpeg "
                    "是否运行、视频源地址和网络连通性。"
                ),
            )
            states.append(state)
            if state["created"]:
                created.append(state)
        else:
            recovery = _rfm_recover_anomaly(
                user_id=user_id,
                source_id=plate_source_id,
                anomaly_type="plate_source_failed",
            )
            if recovery["recovered"]:
                recovered.append({
                    "source_id": plate_source_id,
                    "anomaly_type": "plate_source_failed",
                    **recovery,
                })

            plate = _rfm_extract_plate(
                evidence.get("plate")
            )

            if plate.get("detected"):
                recovery = _rfm_recover_anomaly(
                    user_id=user_id,
                    source_id=plate_source_id,
                    anomaly_type="plate_not_detected",
                )
                if recovery["recovered"]:
                    recovered.append({
                        "source_id": plate_source_id,
                        "anomaly_type": "plate_not_detected",
                        **recovery,
                    })
            else:
                plate_channel = _rfm_to_dict(
                    evidence.get("plate")
                )
                sampled_frames = (
                    plate_channel.get("sampled_frames")
                    or _rfm_result_of(
                        plate_channel
                    ).get("sampled_frames")
                    or 0
                )

                state = _rfm_mark_anomaly(
                    user_id=user_id,
                    source_id=plate_source_id,
                    source_name=plate_source_name,
                    anomaly_type="plate_not_detected",
                    reason=(
                        f"融合监控车牌通道“{plate_source_name}”"
                        f"已连续多轮未检测到有效车牌。"
                        f"当前为第 {int(cycle or 0)} 轮，"
                        f"本轮抽样 {int(sampled_frames or 0)} 帧。"
                        "视频源可以读取，但没有形成稳定车牌结果。"
                    ),
                    threshold=_RFM_NO_PLATE_ALERT_ROUNDS,
                    level="warning",
                    event_type="fusion_plate_not_detected",
                    summary="融合监控连续未检测到有效车牌",
                    suggestion=(
                        "建议检查场景内是否存在车辆、车牌目标尺寸和清晰度；"
                        "对于沙盘画面，应优先使用小目标车牌检测模型先定位、"
                        "裁剪和放大，再交给 HyperLPR 识别。"
                    ),
                )
                states.append(state)
                if state["created"]:
                    created.append(state)

    # ---------------------- 交警备用视频源 ----------------------
    # “没有识别到交警手势”可能是正常现场状态，不发送邮件；
    # 仅对配置了备用源且连续读取失败进行告警。
    traffic_expected = bool(
        context.get("traffic_source_expected")
    )
    traffic_source_id = str(
        context.get("traffic_source_id")
        or "traffic_source"
    )
    traffic_source_name = str(
        context.get("traffic_source_name")
        or traffic_source_id
    )
    traffic_error = str(
        context.get("traffic_error") or ""
    ).strip()

    if traffic_expected:
        if traffic_error:
            state = _rfm_mark_anomaly(
                user_id=user_id,
                source_id=traffic_source_id,
                source_name=traffic_source_name,
                anomaly_type="traffic_source_failed",
                reason=(
                    f"融合监控交警备用视频源“{traffic_source_name}”"
                    f"连续读取失败，本轮错误：{traffic_error}"
                ),
                threshold=_RFM_SOURCE_FAILURE_ALERT_ROUNDS,
                level="error",
                event_type="fusion_traffic_source_failed",
                summary="融合监控交警备用视频源连续读取失败",
                suggestion=(
                    "建议检查备用流地址、RTSP 发布状态、网络连接，"
                    "或直接启用交警电脑摄像头作为当前轮次证据。"
                ),
            )
            states.append(state)
            if state["created"]:
                created.append(state)
        else:
            recovery = _rfm_recover_anomaly(
                user_id=user_id,
                source_id=traffic_source_id,
                anomaly_type="traffic_source_failed",
            )
            if recovery["recovered"]:
                recovered.append({
                    "source_id": traffic_source_id,
                    "anomaly_type": "traffic_source_failed",
                    **recovery,
                })

    return {
        "policy": {
            "no_plate_rounds": _RFM_NO_PLATE_ALERT_ROUNDS,
            "source_failure_rounds": _RFM_SOURCE_FAILURE_ALERT_ROUNDS,
            "deduplicate_until_recovery": True,
            "email_recipients": "owning_user_and_all_valid_admins",
            "gesture_absence_is_alert": False,
        },
        "created": created,
        "states": states,
        "recovered": recovered,
    }


@app.post("/api/fusion/monitor/decision")
def realtime_fusion_monitor_decision(payload: dict | None = _RFMBody(default=None)):
    payload = payload or {}

    evidence = payload.get("evidence") or {
        "plate": payload.get("plate"),
        "traffic": payload.get("traffic"),
        "owner": payload.get("owner"),
    }

    save = bool(payload.get("save", True))
    cycle = int(payload.get("cycle") or 0)
    monitor_context = _rfm_to_dict(
        payload.get("monitor_context")
    )

    try:
        decision = _rfm_decide(_rfm_to_dict(evidence))

        try:
            monitor_alerts = _rfm_evaluate_monitor_anomalies(
                evidence=_rfm_to_dict(evidence),
                monitor_context=monitor_context,
                cycle=cycle,
            )
        except Exception as alert_error:
            monitor_alerts = {
                "policy": {},
                "created": [],
                "states": [],
                "recovered": [],
                "error": (
                    f"{type(alert_error).__name__}: "
                    f"{alert_error}"
                ),
            }

        decision["monitor_alerts"] = monitor_alerts
        saved_id = _rfm_save_decision(decision) if save else None

        return {
            "status": "success",
            "saved_id": saved_id,
            "decision": decision,
            "monitor_alerts": monitor_alerts,
        }

    except Exception as exc:
        raise _RFMHTTPException(status_code=500, detail=f"实时融合决策失败：{exc}")


@app.get("/api/fusion/monitor/latest")
def realtime_fusion_monitor_latest():
    _rfm_ensure_table()

    with _rfm_connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM fusion_monitor_decisions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        return {
            "status": "success",
            "latest": None,
        }

    item = dict(row)
    try:
        item["decision"] = _rfm_json.loads(item.get("decision_json") or "{}")
    except Exception:
        item["decision"] = {}

    return {
        "status": "success",
        "latest": item,
    }


@app.get("/api/fusion/monitor/history")
def realtime_fusion_monitor_history(limit: int = _RFMQuery(default=20, ge=1, le=100)):
    _rfm_ensure_table()

    with _rfm_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM fusion_monitor_decisions
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    items = []

    for row in rows:
        item = dict(row)
        try:
            item["decision"] = _rfm_json.loads(item.get("decision_json") or "{}")
        except Exception:
            item["decision"] = {}
        items.append(item)

    return {
        "status": "success",
        "total": len(items),
        "items": items,
    }


# FUSION_MONITOR_CHANNEL_RECOGNIZE_PATCH_V3
# 融合监控通道低延迟读取：
# 1. 沙盘 live1~live12 优先读取本地 MediaMTX 转发流；
# 2. OpenCV 设置打开/读取硬超时；
# 3. 每轮限制采样数量和总读取时间；
# 4. 前端并行调用车牌和交警备用通道。
from fastapi import Body as _RMCBody, HTTPException as _RMCHTTPException
import re as _rmc_re
import time as _rmc_time


_RMC_OPEN_TIMEOUT_MS = 4000
_RMC_READ_TIMEOUT_MS = 2500
_RMC_CAPTURE_DEADLINE_SECONDS = 6.5
_RMC_PLATE_MAX_SAMPLES = 6
_RMC_TRAFFIC_MAX_SAMPLES = 8


def _rmc_is_sandbox_source(
    source_id: str,
    source_url: str,
) -> bool:
    source_id = str(source_id or "").strip()
    source_url = str(source_url or "").strip()

    if _rmc_re.fullmatch(r"live(?:[1-9]|1[0-2])", source_id):
        return True

    return source_url.startswith(
        f"{SANDBOX_RTSP_BASE}/"
    )


def _rmc_raw_source_url(
    source_id: str,
    source_url: str,
) -> str:
    if source_url:
        return source_url

    if source_id:
        try:
            source = get_stream_source(source_id)
            return str(source.get("url") or "")
        except Exception:
            pass

    return ""


def _rmc_source_candidates(
    source_id: str,
    source_url: str,
) -> list[dict]:
    """
    沙盘流优先使用本地 MediaMTX 长连接转发，避免每轮重新连接老师 RTSP。

    本地转发不可用时，才短超时回退到原始地址。
    """
    source_id = str(source_id or "").strip()
    source_url = str(source_url or "").strip()
    candidates: list[dict] = []

    if _rmc_is_sandbox_source(source_id, source_url):
        match = _rmc_re.search(
            r"(live(?:[1-9]|1[0-2]))",
            source_id or source_url,
        )
        camera_id = match.group(1) if match else source_id

        if camera_id:
            try:
                status = get_stream_status(camera_id)
                if not status.get("running"):
                    start_stream(
                        camera_id,
                        mode="copy",
                        fps=15,
                        force_restart=False,
                    )
                    # 给本地 RTSP 发布留出极短启动时间。
                    _rmc_time.sleep(0.25)
            except Exception:
                pass

            candidates.append({
                "url": (
                    "rtsp://127.0.0.1:8554/"
                    f"sandbox_{camera_id}"
                ),
                "kind": "local_mediamtx",
                "camera_id": camera_id,
            })

    raw_url = _rmc_raw_source_url(
        source_id,
        source_url,
    )

    if raw_url and all(
        item["url"] != raw_url
        for item in candidates
    ):
        candidates.append({
            "url": raw_url,
            "kind": "original_source",
            "camera_id": "",
        })

    return candidates


def _rmc_open_capture(
    url: str,
    *,
    open_timeout_ms: int = _RMC_OPEN_TIMEOUT_MS,
    read_timeout_ms: int = _RMC_READ_TIMEOUT_MS,
):
    """
    使用 OpenCV FFmpeg 后端的打开/读取超时。

    新版 OpenCV 支持在 open() 时传入参数；旧版不支持时回退到 set()。
    """
    cap = cv2.VideoCapture()
    params: list[int] = []

    open_prop = getattr(
        cv2,
        "CAP_PROP_OPEN_TIMEOUT_MSEC",
        None,
    )
    read_prop = getattr(
        cv2,
        "CAP_PROP_READ_TIMEOUT_MSEC",
        None,
    )

    if open_prop is not None:
        params.extend([
            int(open_prop),
            int(open_timeout_ms),
        ])
    if read_prop is not None:
        params.extend([
            int(read_prop),
            int(read_timeout_ms),
        ])

    opened = False

    try:
        if params:
            opened = bool(
                cap.open(
                    url,
                    cv2.CAP_FFMPEG,
                    params,
                )
            )
        else:
            opened = bool(
                cap.open(url, cv2.CAP_FFMPEG)
            )
    except Exception:
        cap.release()
        cap = cv2.VideoCapture()

        if open_prop is not None:
            cap.set(
                open_prop,
                int(open_timeout_ms),
            )
        if read_prop is not None:
            cap.set(
                read_prop,
                int(read_timeout_ms),
            )

        opened = bool(
            cap.open(url, cv2.CAP_FFMPEG)
        )

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap, opened


def _rmc_open_first_available_source(
    source_id: str,
    source_url: str,
):
    candidates = _rmc_source_candidates(
        source_id,
        source_url,
    )

    if not candidates:
        raise RuntimeError("未提供可读取的视频源地址")

    errors: list[str] = []

    for candidate in candidates:
        url = str(candidate["url"])
        cap = None

        try:
            cap, opened = _rmc_open_capture(url)

            if opened and cap.isOpened():
                return cap, candidate, errors

            errors.append(
                f"{candidate['kind']} 无法打开：{url}"
            )
        except Exception as error:
            errors.append(
                f"{candidate['kind']} 打开异常："
                f"{type(error).__name__}: {error}"
            )
        finally:
            if (
                cap is not None
                and not cap.isOpened()
            ):
                cap.release()

    raise RuntimeError(
        "；".join(errors)
        or "所有视频源候选均无法打开"
    )


def _rmc_best_plate(plates: list[dict]) -> dict | None:
    valid = [item for item in plates if isinstance(item, dict) and item.get("plate_number")]
    if not valid:
        return None
    return max(valid, key=lambda item: float(item.get("confidence") or 0))


def _rmc_recognize_channel_current_cycle(
    *,
    task_type: str,
    source_id: str,
    source_url: str,
    frame_count: int,
    sample_interval: int,
    warmup_frames: int,
) -> dict:
    started = _rmc_time.perf_counter()
    cap, source_meta, source_errors = (
        _rmc_open_first_available_source(
            source_id,
            source_url,
        )
    )
    url = str(source_meta["url"])
    requested_frame_count = max(1, int(frame_count))
    requested_interval = max(1, int(sample_interval))

    if task_type == "plate":
        max_samples = min(
            _RMC_PLATE_MAX_SAMPLES,
            requested_frame_count,
        )
    else:
        max_samples = min(
            _RMC_TRAFFIC_MAX_SAMPLES,
            requested_frame_count,
        )

    # 融合监控强调当前轮次低延迟，不再按“帧数 × 间隔”读取数百帧。
    effective_interval = max(
        1,
        min(
            requested_interval,
            3 if task_type == "plate" else 2,
        ),
    )
    max_reads = max(
        int(warmup_frames) + max_samples,
        max_samples * effective_interval + int(warmup_frames),
    )
    capture_deadline = (
        _rmc_time.perf_counter()
        + _RMC_CAPTURE_DEADLINE_SECONDS
    )

    frame_results = []
    frames_read = 0
    sampled = 0

    try:
        for _ in range(max(0, int(warmup_frames))):
            if _rmc_time.perf_counter() >= capture_deadline:
                break
            cap.read()

        while (
            frames_read < max_reads
            and sampled < max_samples
            and _rmc_time.perf_counter() < capture_deadline
        ):
            ok, frame = cap.read()
            frames_read += 1
            if not ok or frame is None:
                continue
            if frames_read % effective_interval != 0:
                continue

            sampled += 1
            frame_started = _rmc_time.perf_counter()

            if task_type == "plate":
                result = recognize_plate_frame(
                    frame,
                    output_path=None,
                    draw_annotation=False,
                )
                confidence = max(
                    [float(item.get("confidence") or 0) for item in result.get("plates", [])],
                    default=0.0,
                )
            elif task_type == "traffic_gesture":
                result = recognize_traffic_gesture_frame(
                    frame,
                    output_path=None,
                    static_image_mode=False,
                    draw_annotation=False,
                )
                confidence = float(result.get("confidence") or 0)
            else:
                raise RuntimeError("task_type 只支持 plate 或 traffic_gesture")

            frame_results.append({
                "frame_index": frames_read,
                "captured_at": now_text(),
                "saved_filename": "",
                "image_url": "",
                "output_filename": "",
                "output_image_url": "",
                "confidence": confidence,
                "latency_ms": round((_rmc_time.perf_counter() - frame_started) * 1000, 2),
                "result": result,
            })
    finally:
        cap.release()

    if not frame_results:
        raise RuntimeError("视频源未返回可识别的有效帧")

    if task_type == "plate":
        aggregated = aggregate_plate_frame_results(frame_results)
        final_result = aggregated.get("final_result") or aggregated
        plates = final_result.get("stable_plates") or final_result.get("plates") or []
        best = _rmc_best_plate(plates)
        if best is None:
            best_text = final_result.get("best_plate_text") or ""
            best_confidence = float(final_result.get("best_confidence") or 0)
        else:
            best_text = best.get("plate_number") or ""
            best_confidence = float(best.get("confidence") or best.get("avg_confidence") or 0)
        result = {
            **final_result,
            "plates": plates,
            "plate_count": len(plates),
            "best_plate": best_text,
            "best_confidence": round(best_confidence, 4),
        }
    else:
        aggregated = aggregate_gesture_frame_results(task_type, frame_results)
        result = aggregated.get("final_result") or {}

    return {
        "status": "success",
        "task_type": task_type,
        "source_id": source_id,
        "source_url": url,
        "requested_source_url": source_url,
        "source_resolution": source_meta.get("kind"),
        "source_fallback_errors": source_errors,
        "open_timeout_ms": _RMC_OPEN_TIMEOUT_MS,
        "read_timeout_ms": _RMC_READ_TIMEOUT_MS,
        "capture_deadline_seconds": _RMC_CAPTURE_DEADLINE_SECONDS,
        "input_type": "current_cycle_stream",
        "latency_ms": round((_rmc_time.perf_counter() - started) * 1000, 2),
        "frames_read": frames_read,
        "sampled_frames": len(frame_results),
        "sample_interval_effective": effective_interval,
        "result": result,
    }


@app.post("/api/fusion/monitor/channel/recognize")
def fusion_monitor_channel_recognize(payload: dict | None = _RMCBody(default=None)):
    payload = payload or {}
    task_type = str(payload.get("task_type") or "").strip()
    source_id = str(payload.get("source_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()

    if task_type not in {"plate", "traffic_gesture"}:
        raise _RMCHTTPException(status_code=400, detail="task_type 只支持 plate 或 traffic_gesture")

    try:
        return _rmc_recognize_channel_current_cycle(
            task_type=task_type,
            source_id=source_id,
            source_url=source_url,
            frame_count=int(payload.get("frame_count") or 20),
            sample_interval=int(payload.get("sample_interval") or 5),
            warmup_frames=int(payload.get("warmup_frames") or 3),
        )
    except Exception as exc:
        raise _RMCHTTPException(
            status_code=500,
            detail=(
                "融合监控通道识别失败："
                f"{exc}；打开超时 {_RMC_OPEN_TIMEOUT_MS}ms，"
                f"读取超时 {_RMC_READ_TIMEOUT_MS}ms"
            ),
        )


# VIDEO_SOURCE_MANAGEMENT_PATCH_V2
# 用户视频源：普通用户管理自己的源；管理员管理全部；系统共享源所有用户可见。
from fastapi import (
    Body as _VSMBody,
    Query as _VSMQuery,
    HTTPException as _VSMHTTPException,
    Depends as _VSMDepends,
)
from pathlib import Path as _VSMPath
from datetime import datetime as _VSMDateTime
import re as _vsm_re
import sqlite3 as _vsm_sqlite3
import cv2 as _vsm_cv2


def _vsm_now_text() -> str:
    return _VSMDateTime.now().strftime("%Y-%m-%d %H:%M:%S")


def _vsm_db_path() -> _VSMPath:
    return _VSMPath(__file__).resolve().parent / "data" / "app.db"


def _vsm_project_root() -> _VSMPath:
    return _VSMPath(__file__).resolve().parent.parent


def _vsm_connect():
    path = _vsm_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _vsm_sqlite3.connect(str(path), timeout=20)
    conn.row_factory = _vsm_sqlite3.Row
    return conn


def _vsm_ensure_table():
    with _vsm_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS video_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                source_key TEXT UNIQUE,
                name TEXT,
                source_type TEXT,
                source_id TEXT,
                source_url TEXT,
                protocol TEXT,
                use_mock_frame INTEGER DEFAULT 0,
                demo_file TEXT,
                frame_count INTEGER DEFAULT 20,
                sample_interval INTEGER DEFAULT 5,
                warmup_frames INTEGER DEFAULT 3,
                enabled INTEGER DEFAULT 1,
                description TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(video_sources)"
            ).fetchall()
        }

        if "user_id" not in columns:
            conn.execute(
                "ALTER TABLE video_sources ADD COLUMN user_id INTEGER"
            )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_video_sources_user
            ON video_sources(user_id, enabled, id)
            """
        )
        conn.commit()


def _vsm_row_to_dict(
    row,
    current_user: dict | None = None,
) -> dict:
    item = dict(row)
    item["use_mock_frame"] = bool(item.get("use_mock_frame"))
    item["enabled"] = bool(item.get("enabled"))

    owner_id = item.get("user_id")
    item["is_global"] = owner_id is None

    if current_user:
        is_admin = current_user.get("role") == "admin"
        is_owner = (
            owner_id is not None
            and int(owner_id) == int(current_user["id"])
        )
        item["can_manage"] = bool(is_admin or is_owner)
        item["is_mine"] = bool(is_owner)
    else:
        item["can_manage"] = False
        item["is_mine"] = False

    return item


def _vsm_insert_default_sources():
    _vsm_ensure_table()
    now = _vsm_now_text()

    defaults = [
        {
            "source_key": "plate_mock_live12",
            "name": "沙盘 / Mock 测试源 live12",
            "source_type": "plate",
            "source_id": "live12",
            "source_url": "",
            "protocol": "mock",
            "use_mock_frame": 1,
            "demo_file": "",
            "frame_count": 20,
            "sample_interval": 5,
            "warmup_frames": 3,
            "enabled": 1,
            "description": "系统共享 Mock 视频源。",
        },
        {
            "source_key": "plate_rtsp_live12",
            "name": "沙盘 RTSP 源 live12",
            "source_type": "plate",
            "source_id": "live12",
            "source_url": "",
            "protocol": "rtsp",
            "use_mock_frame": 0,
            "demo_file": "",
            "frame_count": 20,
            "sample_interval": 5,
            "warmup_frames": 3,
            "enabled": 1,
            "description": "系统共享沙盘 RTSP 源。",
        },
        {
            "source_key": "plate_mediamtx",
            "name": "MediaMTX 车牌流 plate",
            "source_type": "plate",
            "source_id": "plate_rtsp",
            "source_url": "rtsp://127.0.0.1:8554/plate",
            "protocol": "rtsp",
            "use_mock_frame": 0,
            "demo_file": "",
            "frame_count": 20,
            "sample_interval": 5,
            "warmup_frames": 3,
            "enabled": 1,
            "description": "系统共享 MediaMTX 车牌流。",
        },
        {
            "source_key": "traffic_demo_file",
            "name": "Demo 测试图 traffic.png",
            "source_type": "traffic_gesture",
            "source_id": "traffic_demo",
            "source_url": "",
            "protocol": "demo",
            "use_mock_frame": 1,
            "demo_file": "traffic.png",
            "frame_count": 20,
            "sample_interval": 5,
            "warmup_frames": 3,
            "enabled": 1,
            "description": "系统共享交警手势 Demo。",
        },
        {
            "source_key": "traffic_mediamtx",
            "name": "MediaMTX 交警手势流 traffic",
            "source_type": "traffic_gesture",
            "source_id": "traffic_rtsp",
            "source_url": "rtsp://127.0.0.1:8554/traffic",
            "protocol": "rtsp",
            "use_mock_frame": 0,
            "demo_file": "traffic.png",
            "frame_count": 20,
            "sample_interval": 5,
            "warmup_frames": 3,
            "enabled": 1,
            "description": "系统共享交警手势流。",
        },
    ]

    with _vsm_connect() as conn:
        for item in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO video_sources (
                    user_id,
                    source_key,
                    name,
                    source_type,
                    source_id,
                    source_url,
                    protocol,
                    use_mock_frame,
                    demo_file,
                    frame_count,
                    sample_interval,
                    warmup_frames,
                    enabled,
                    description,
                    created_at,
                    updated_at
                )
                VALUES (
                    NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    item["source_key"],
                    item["name"],
                    item["source_type"],
                    item["source_id"],
                    item["source_url"],
                    item["protocol"],
                    item["use_mock_frame"],
                    item["demo_file"],
                    item["frame_count"],
                    item["sample_interval"],
                    item["warmup_frames"],
                    item["enabled"],
                    item["description"],
                    now,
                    now,
                ),
            )
        conn.commit()


def _vsm_safe_source_key(
    value: str,
    *,
    owner_user_id: int | None,
) -> str:
    clean = _vsm_re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        str(value or "").strip(),
    ).strip("_")

    if not clean:
        clean = f"source_{uuid4().hex[:10]}"

    if owner_user_id is not None:
        prefix = f"u{int(owner_user_id)}_"
        if not clean.startswith(prefix):
            clean = prefix + clean

    return clean[:120]


def _vsm_normalize_payload(
    payload: dict,
    *,
    current_user: dict,
    existing: dict | None = None,
) -> dict:
    payload = payload or {}
    existing = existing or {}

    name = str(
        payload.get("name", existing.get("name", ""))
    ).strip()
    source_type = str(
        payload.get(
            "source_type",
            existing.get("source_type", "plate"),
        )
    ).strip()
    source_id = str(
        payload.get(
            "source_id",
            existing.get("source_id", ""),
        )
    ).strip()
    source_url = str(
        payload.get(
            "source_url",
            existing.get("source_url", ""),
        )
    ).strip()
    protocol = str(
        payload.get(
            "protocol",
            existing.get("protocol", "rtsp"),
        )
    ).strip().lower()
    demo_file = str(
        payload.get(
            "demo_file",
            existing.get("demo_file", ""),
        )
    ).strip()
    description = str(
        payload.get(
            "description",
            existing.get("description", ""),
        )
    ).strip()

    if not name:
        raise _VSMHTTPException(
            status_code=400,
            detail="视频源名称不能为空。",
        )

    if source_type not in {
        "plate",
        "traffic_gesture",
        "owner_gesture",
        "general",
    }:
        raise _VSMHTTPException(
            status_code=400,
            detail=(
                "source_type 只支持 plate、traffic_gesture、"
                "owner_gesture、general。"
            ),
        )

    if protocol not in {
        "rtsp",
        "mediamtx",
        "hls",
        "webrtc",
        "mjpeg",
        "mock",
        "demo",
    }:
        raise _VSMHTTPException(
            status_code=400,
            detail="不支持的视频源协议。",
        )

    if not source_id and not source_url and not demo_file:
        raise _VSMHTTPException(
            status_code=400,
            detail=(
                "源 ID、视频流地址、Demo 文件至少填写一个。"
            ),
        )

    if existing:
        owner_user_id = existing.get("user_id")
    else:
        # 管理员新增的视频源默认为系统共享；
        # 普通用户新增的视频源只归属于自己。
        owner_user_id = (
            None
            if current_user.get("role") == "admin"
            else int(current_user["id"])
        )

    source_key = _vsm_safe_source_key(
        payload.get(
            "source_key",
            existing.get("source_key", ""),
        ),
        owner_user_id=owner_user_id,
    )

    frame_count = max(
        5,
        min(
            300,
            int(
                payload.get(
                    "frame_count",
                    existing.get("frame_count", 20),
                )
                or 20
            ),
        ),
    )
    sample_interval = max(
        1,
        min(
            60,
            int(
                payload.get(
                    "sample_interval",
                    existing.get("sample_interval", 5),
                )
                or 5
            ),
        ),
    )
    warmup_frames = max(
        0,
        min(
            30,
            int(
                payload.get(
                    "warmup_frames",
                    existing.get("warmup_frames", 3),
                )
                or 3
            ),
        ),
    )

    return {
        "user_id": owner_user_id,
        "source_key": source_key,
        "name": name,
        "source_type": source_type,
        "source_id": source_id,
        "source_url": source_url,
        "protocol": protocol,
        "use_mock_frame": (
            1
            if bool(
                payload.get(
                    "use_mock_frame",
                    existing.get("use_mock_frame", False),
                )
            )
            else 0
        ),
        "demo_file": demo_file,
        "frame_count": frame_count,
        "sample_interval": sample_interval,
        "warmup_frames": warmup_frames,
        "enabled": (
            1
            if bool(
                payload.get(
                    "enabled",
                    existing.get("enabled", True),
                )
            )
            else 0
        ),
        "description": description,
    }


def _vsm_select_source_row(source_id: int):
    with _vsm_connect() as conn:
        return conn.execute(
            """
            SELECT
                v.*,
                u.username AS owner_username,
                u.email AS owner_email
            FROM video_sources v
            LEFT JOIN users u ON u.id = v.user_id
            WHERE v.id = ?
            """,
            (int(source_id),),
        ).fetchone()


def _vsm_get_source_or_404(
    source_id: int,
    *,
    current_user: dict,
    require_manage: bool = False,
) -> dict:
    _vsm_ensure_table()
    row = _vsm_select_source_row(source_id)

    if not row:
        raise _VSMHTTPException(
            status_code=404,
            detail="视频源不存在。",
        )

    item = _vsm_row_to_dict(row, current_user)
    is_admin = current_user.get("role") == "admin"
    owner_id = item.get("user_id")
    is_owner = (
        owner_id is not None
        and int(owner_id) == int(current_user["id"])
    )
    is_visible = is_admin or owner_id is None or is_owner

    if not is_visible:
        raise _VSMHTTPException(
            status_code=403,
            detail="无权访问其他用户的视频源。",
        )

    if require_manage and not (is_admin or is_owner):
        raise _VSMHTTPException(
            status_code=403,
            detail="系统共享视频源只能由管理员管理。",
        )

    return item


@app.get("/api/video-sources")
def list_video_sources(
    source_type: str | None = _VSMQuery(default=None),
    enabled_only: bool = _VSMQuery(default=False),
    current_user: dict = _VSMDepends(get_current_user),
):
    _vsm_ensure_table()
    _vsm_insert_default_sources()

    sql = """
        SELECT
            v.*,
            u.username AS owner_username,
            u.email AS owner_email
        FROM video_sources v
        LEFT JOIN users u ON u.id = v.user_id
        WHERE 1 = 1
    """
    params: list = []

    if current_user.get("role") != "admin":
        sql += " AND (v.user_id IS NULL OR v.user_id = ?)"
        params.append(int(current_user["id"]))

    if source_type:
        sql += " AND v.source_type = ?"
        params.append(source_type)

    if enabled_only:
        sql += " AND v.enabled = 1"

    sql += """
        ORDER BY
            CASE WHEN v.user_id IS NULL THEN 0 ELSE 1 END,
            v.source_type,
            v.id
    """

    with _vsm_connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    items = [
        _vsm_row_to_dict(row, current_user)
        for row in rows
    ]

    return {
        "status": "success",
        "total": len(items),
        "items": items,
    }


@app.post("/api/video-sources")
def create_video_source(
    payload: dict | None = _VSMBody(default=None),
    current_user: dict = _VSMDepends(get_current_user),
):
    _vsm_ensure_table()
    item = _vsm_normalize_payload(
        payload or {},
        current_user=current_user,
    )
    now = _vsm_now_text()

    try:
        with _vsm_connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO video_sources (
                    user_id,
                    source_key,
                    name,
                    source_type,
                    source_id,
                    source_url,
                    protocol,
                    use_mock_frame,
                    demo_file,
                    frame_count,
                    sample_interval,
                    warmup_frames,
                    enabled,
                    description,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["user_id"],
                    item["source_key"],
                    item["name"],
                    item["source_type"],
                    item["source_id"],
                    item["source_url"],
                    item["protocol"],
                    item["use_mock_frame"],
                    item["demo_file"],
                    item["frame_count"],
                    item["sample_interval"],
                    item["warmup_frames"],
                    item["enabled"],
                    item["description"],
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = int(cursor.lastrowid)
    except _vsm_sqlite3.IntegrityError:
        raise _VSMHTTPException(
            status_code=400,
            detail="视频源 key 已存在，请修改名称或源 ID。",
        )

    insert_operation_log(
        action="create_video_source",
        detail={
            "source_id": new_id,
            "source_name": item["name"],
            "owner_user_id": item["user_id"],
        },
        user_id=int(current_user["id"]),
    )

    return {
        "status": "success",
        "message": "视频源已创建",
        "id": new_id,
        "item": _vsm_get_source_or_404(
            new_id,
            current_user=current_user,
        ),
    }


@app.put("/api/video-sources/{source_id}")
def update_video_source(
    source_id: int,
    payload: dict | None = _VSMBody(default=None),
    current_user: dict = _VSMDepends(get_current_user),
):
    old = _vsm_get_source_or_404(
        source_id,
        current_user=current_user,
        require_manage=True,
    )
    item = _vsm_normalize_payload(
        payload or {},
        current_user=current_user,
        existing=old,
    )
    now = _vsm_now_text()

    try:
        with _vsm_connect() as conn:
            conn.execute(
                """
                UPDATE video_sources
                SET
                    user_id = ?,
                    source_key = ?,
                    name = ?,
                    source_type = ?,
                    source_id = ?,
                    source_url = ?,
                    protocol = ?,
                    use_mock_frame = ?,
                    demo_file = ?,
                    frame_count = ?,
                    sample_interval = ?,
                    warmup_frames = ?,
                    enabled = ?,
                    description = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    item["user_id"],
                    item["source_key"],
                    item["name"],
                    item["source_type"],
                    item["source_id"],
                    item["source_url"],
                    item["protocol"],
                    item["use_mock_frame"],
                    item["demo_file"],
                    item["frame_count"],
                    item["sample_interval"],
                    item["warmup_frames"],
                    item["enabled"],
                    item["description"],
                    now,
                    int(source_id),
                ),
            )
            conn.commit()
    except _vsm_sqlite3.IntegrityError:
        raise _VSMHTTPException(
            status_code=400,
            detail="视频源 key 已存在，请修改名称或源 ID。",
        )

    insert_operation_log(
        action="update_video_source",
        detail={
            "source_id": int(source_id),
            "source_name": item["name"],
        },
        user_id=int(current_user["id"]),
    )

    return {
        "status": "success",
        "message": "视频源已更新",
        "item": _vsm_get_source_or_404(
            source_id,
            current_user=current_user,
        ),
    }


@app.delete("/api/video-sources/{source_id}")
def delete_video_source(
    source_id: int,
    current_user: dict = _VSMDepends(get_current_user),
):
    item = _vsm_get_source_or_404(
        source_id,
        current_user=current_user,
        require_manage=True,
    )

    with _vsm_connect() as conn:
        conn.execute(
            "DELETE FROM video_sources WHERE id = ?",
            (int(source_id),),
        )
        conn.commit()

    insert_operation_log(
        action="delete_video_source",
        detail={
            "source_id": int(source_id),
            "source_name": item.get("name"),
        },
        user_id=int(current_user["id"]),
    )

    return {
        "status": "success",
        "message": "视频源已删除",
        "id": int(source_id),
    }


@app.post("/api/video-sources/{source_id}/check")
def check_video_source(
    source_id: int,
    current_user: dict = _VSMDepends(get_current_user),
):
    item = _vsm_get_source_or_404(
        source_id,
        current_user=current_user,
    )

    if item.get("use_mock_frame"):
        demo_file = item.get("demo_file") or ""

        if demo_file:
            demo_path = _vsm_project_root() / "demo" / demo_file
            ok = demo_path.exists()
            return {
                "status": "success",
                "online": ok,
                "mode": "demo_file",
                "message": (
                    "Demo 文件存在"
                    if ok
                    else f"Demo 文件不存在：{demo_path}"
                ),
                "item": item,
            }

        return {
            "status": "success",
            "online": True,
            "mode": "mock_stream",
            "message": "Mock 视频源可用",
            "item": item,
        }

    source_url = item.get("source_url") or ""

    if not source_url:
        return {
            "status": "success",
            "online": True,
            "mode": "backend_source_id",
            "message": (
                "未填写 source_url，将使用后端 source_id 配置读取。"
            ),
            "item": item,
        }

    cap = None

    try:
        cap = _vsm_cv2.VideoCapture(
            source_url,
            _vsm_cv2.CAP_FFMPEG,
        )
        cap.set(_vsm_cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            return {
                "status": "success",
                "online": False,
                "mode": item.get("protocol") or "video",
                "message": f"无法打开视频源：{source_url}",
                "item": item,
            }

        warmup_frames = int(item.get("warmup_frames") or 3)
        frame = None

        for _ in range(max(1, warmup_frames + 1)):
            ok, current = cap.read()
            if ok and current is not None:
                frame = current

        online = frame is not None

        return {
            "status": "success",
            "online": online,
            "mode": item.get("protocol") or "video",
            "message": (
                "视频源连接正常"
                if online
                else "视频源已打开，但未读取到有效帧"
            ),
            "item": item,
        }
    finally:
        if cap is not None:
            cap.release()


# STREAMING_SANDBOX_V1
# 沙盘 HLS 流列表 + ffmpeg 推流管理
@app.get("/api/streaming/sandbox/list")
def list_sandbox_streams():
    """返回沙盘 12 路摄像头的 HLS + WebRTC 播放地址。"""
    streams = []
    for cam in SANDBOX_CAMERAS:
        cam_id = cam["id"]
        streams.append({
            "id": cam_id,
            "name": cam["name"],
            "source_url": f"rtsp://10.126.59.120:8554/live/{cam_id}",
            "hls_url": f"http://127.0.0.1:8888/sandbox_{cam_id}/index.m3u8",
            "webrtc_url": f"http://127.0.0.1:8889/sandbox_{cam_id}",
        })
    return {"status": "success", "streams": streams}


@app.post("/api/streaming/ffmpeg/start")
def ffmpeg_start(payload: dict):
    """启动 ffmpeg 拉流推流到 MediaMTX。"""
    camera_id = payload.get("camera_id", "")
    mode = payload.get("mode", "copy")
    fps = int(payload.get("fps") or 15)
    force_restart = bool(payload.get("force_restart", False))
    valid_ids = {c["id"] for c in SANDBOX_CAMERAS}
    if camera_id not in valid_ids:
        raise HTTPException(status_code=400, detail=f"无效摄像头 ID: {camera_id}")
    try:
        ok = start_stream(
            camera_id,
            mode,
            fps=fps,
            force_restart=force_restart,
        )
        return {
            "status": "success" if ok else "error",
            "camera_id": camera_id,
            "running": ok,
            "data": get_stream_status(camera_id),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动 ffmpeg 失败: {exc}")


@app.post("/api/streaming/ffmpeg/stop")
def ffmpeg_stop(payload: dict):
    """停止指定摄像头的 ffmpeg 推流进程。"""
    camera_id = payload.get("camera_id", "")
    try:
        ok = stop_stream(camera_id)
        return {"status": "success" if ok else "error", "camera_id": camera_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"停止 ffmpeg 失败: {exc}")


@app.get("/api/streaming/ffmpeg/status")
def ffmpeg_status(camera_id: str = Query("", description="摄像头 ID，留空返回全部")):
    """查询 ffmpeg 推流进程状态。"""
    if camera_id:
        try:
            return {"status": "success", "data": get_stream_status(camera_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "streams": get_all_streams_status()}


@app.post("/api/fusion/monitor/sandbox/plate/recognize")
def sandbox_plate_recognize(payload: dict):
    """沙盘车牌识别：复用融合当前轮次快速抽帧管线。"""
    camera_id = str(payload.get("camera_id") or "").strip()
    valid_ids = {item["id"] for item in SANDBOX_CAMERAS}
    if camera_id not in valid_ids:
        raise HTTPException(status_code=400, detail=f"无效摄像头 ID: {camera_id}")

    try:
        channel = _rmc_recognize_channel_current_cycle(
            task_type="plate",
            source_id=camera_id,
            source_url=f"{SANDBOX_RTSP_BASE}/{camera_id}",
            frame_count=int(payload.get("frame_count") or 60),
            sample_interval=int(payload.get("sample_interval") or 5),
            warmup_frames=int(payload.get("warmup_frames") or 3),
        )
        result = channel.get("result") or {}
        plates = result.get("plates") or []

        return {
            "status": "success",
            "data": {
                "camera_id": camera_id,
                "plate_count": int(result.get("plate_count") or len(plates)),
                "plates": plates,
                "best_plate": result.get("best_plate") or result.get("best_plate_text") or "",
                "best_confidence": result.get("best_confidence") or 0,
                "latency_ms": channel.get("latency_ms"),
                "frames_read": channel.get("frames_read"),
                "sampled_frames": channel.get("sampled_frames"),
                "sample_interval_effective": channel.get("sample_interval_effective"),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"沙盘车牌识别失败：{exc}")


@app.get("/api/video/stream")
def video_mjpeg_stream(
    source_id: str = Query("live1", description="RTSP 源 ID"),
    source_url: str = Query("", description="自定义 RTSP 地址（优先级高于 source_id）"),
    fps: int = Query(15, ge=1, le=30, description="最大帧率"),
):
    """
    实时 MJPEG 视频流。

    前端 <img> 标签直接加载此端点即可看到实时画面：
        <img src="http://127.0.0.1:8000/api/video/stream?source_id=live1" />

    支持两种模式：
    - source_id：从内置 RTSP_SOURCES 查找
    - source_url：直接使用自定义 RTSP 地址
    """
    if source_url and source_url.strip():
        url = source_url.strip()
    else:
        source = next((s for s in RTSP_SOURCES if s["id"] == source_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail=f"未找到视频源：{source_id}")
        url = source["url"]

    # 为每个请求创建独立的生成器实例
    return StreamingResponse(
        _mjpeg_stream_generator(url, max_fps=fps),
        media_type="multipart/x-mixed-replace; boundary=mjpeg-frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close",
            "Access-Control-Allow-Origin": "*",
        },
    )
