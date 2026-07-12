"""
ffmpeg 进程管理模块：从沙盘 RTSP 拉流 → 推送到 MediaMTX → HLS 供前端播放。

两种模式：
- copy：passthrough（-c copy），零 CPU 开销
- transcode：H.264 重编码（libx264 ultrafast），可控制码率和帧率
"""

import subprocess
import threading
import time
from typing import Optional

FFMPEG_PATH = "ffmpeg"  # 从 PATH 环境变量中查找
MEDIAMTX_RTSP_BASE = "rtsp://127.0.0.1:8554"
SANDBOX_RTSP_BASE = "rtsp://10.126.59.120:8554/live"

# Managed ffmpeg processes: keyed by camera_id (e.g. "live1")
_ffmpeg_processes: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()

SANDBOX_CAMERAS = [
    {"id": "live1", "name": "桥面"},
    {"id": "live2", "name": "停车场出口"},
    {"id": "live3", "name": "行人检测"},
    {"id": "live4", "name": "消防车识别"},
    {"id": "live5", "name": "桥出口"},
    {"id": "live6", "name": "桥入口"},
    {"id": "live7", "name": "道路2"},
    {"id": "live8", "name": "隧道事故识别"},
    {"id": "live9", "name": "隧道车辆数量"},
    {"id": "live10", "name": "道路3"},
    {"id": "live11", "name": "停车场入口"},
    {"id": "live12", "name": "道路1"},
]


def build_ffmpeg_cmd(camera_id: str, mode: str = "copy", fps: int = 15) -> list[str]:
    """构造 ffmpeg 拉流推流命令行。"""
    source_url = f"{SANDBOX_RTSP_BASE}/{camera_id}"
    target_url = f"{MEDIAMTX_RTSP_BASE}/sandbox_{camera_id}"

    if mode == "copy":
        return [
            FFMPEG_PATH,
            "-rtsp_transport", "tcp",
            "-i", source_url,
            "-c", "copy",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            target_url,
        ]
    else:
        return [
            FFMPEG_PATH,
            "-rtsp_transport", "tcp",
            "-i", source_url,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-b:v", "2M",
            "-maxrate", "2M",
            "-bufsize", "4M",
            "-g", str(fps * 2),
            "-r", str(fps),
            "-an",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            target_url,
        ]


def start_stream(camera_id: str, mode: str = "copy") -> bool:
    """启动 ffmpeg 拉流进程。幂等：已运行则跳过。"""
    valid_ids = {c["id"] for c in SANDBOX_CAMERAS}
    if camera_id not in valid_ids:
        raise ValueError(f"无效摄像头 ID: {camera_id}")

    with _lock:
        existing = _ffmpeg_processes.get(camera_id)
        if existing is not None and existing.poll() is None:
            return True  # already running

        cmd = build_ffmpeg_cmd(camera_id, mode)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        _ffmpeg_processes[camera_id] = proc
        return True


def stop_stream(camera_id: str) -> bool:
    """停止 ffmpeg 进程。"""
    with _lock:
        proc = _ffmpeg_processes.pop(camera_id, None)
        if proc is None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        return True


def stop_all_streams() -> None:
    """停止所有 ffmpeg 进程。shutdown 时调用。"""
    with _lock:
        ids = list(_ffmpeg_processes.keys())
    for cid in ids:
        stop_stream(cid)


def get_stream_status(camera_id: str) -> dict:
    """查询单路推流状态。"""
    valid_ids = {c["id"] for c in SANDBOX_CAMERAS}
    if camera_id not in valid_ids:
        raise ValueError(f"无效摄像头 ID: {camera_id}")

    with _lock:
        proc = _ffmpeg_processes.get(camera_id)
        if proc is None:
            return {"camera_id": camera_id, "running": False, "pid": None, "exit_code": None}
        poll = proc.poll()
        return {
            "camera_id": camera_id,
            "running": poll is None,
            "pid": proc.pid,
            "exit_code": poll if poll is not None else None,
        }


def get_all_streams_status() -> list[dict]:
    """查询全部推流状态。"""
    return [get_stream_status(c["id"]) for c in SANDBOX_CAMERAS]
