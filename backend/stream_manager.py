"""
沙盘视频流管理模块

将老师 RTSP 流拉取后通过 ffmpeg 推送到本地 MediaMTX，
对外提供 HLS / WebRTC 两种播放方式。

架构：
    老师 RTSP (10.126.59.120:8554) → ffmpeg (tcp) → 本地 MediaMTX (127.0.0.1:8554)
                                                          ├── HLS:  http://127.0.0.1:8888/sandbox_{id}/index.m3u8
                                                          └── WebRTC: http://127.0.0.1:8889/sandbox_{id}

稳定性策略：
1. 默认使用视频流复制（mode=copy），避免不必要的转码开销与兼容性问题。
2. start_stream() 幂等 —— 重复调用不会创建重复进程。
3. ffmpeg 意外退出后由守护线程（watchdog）自动重启，退避延迟递增。
4. stop_stream() 会取消自动重启并终止进程。
5. 支持转码模式（mode=transcode），可调整分辨率与帧率。
"""

from __future__ import annotations

from datetime import datetime
import subprocess
import threading
import time


FFMPEG_PATH = "ffmpeg"
MEDIAMTX_RTSP_BASE = "rtsp://127.0.0.1:8554"
SANDBOX_RTSP_BASE = "rtsp://10.126.59.120:8554/live"

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

_ffmpeg_processes: dict[str, subprocess.Popen] = {}
_stream_configs: dict[str, dict] = {}
_stream_runtime: dict[str, dict] = {}
_lock = threading.RLock()
_watchdog_started = False


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_ffmpeg_cmd(
    camera_id: str,
    mode: str = "copy",
    fps: int = 15,
) -> list[str]:
    source_url = f"{SANDBOX_RTSP_BASE}/{camera_id}"
    target_url = f"{MEDIAMTX_RTSP_BASE}/sandbox_{camera_id}"

    common_input = [
        FFMPEG_PATH,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-fflags",
        "+genpts+discardcorrupt",
        "-use_wallclock_as_timestamps",
        "1",
        "-i",
        source_url,
    ]

    if mode == "copy":
        return common_input + [
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            target_url,
        ]

    gop = max(15, int(fps) * 2)

    return common_input + [
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"scale=1280:-2,fps={fps}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "main",
        "-level:v",
        "4.0",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-b:v",
        "1800k",
        "-maxrate",
        "2200k",
        "-bufsize",
        "3600k",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        target_url,
    ]


def _validate_camera_id(camera_id: str) -> None:
    valid_ids = {item["id"] for item in SANDBOX_CAMERAS}
    if camera_id not in valid_ids:
        raise ValueError(f"无效摄像头 ID: {camera_id}")



def _consume_ffmpeg_stderr(
    camera_id: str,
    proc: subprocess.Popen,
) -> None:
    stderr = proc.stderr
    if stderr is None:
        return

    tail: list[str] = []

    try:
        for raw_line in iter(stderr.readline, ""):
            line = str(raw_line or "").strip()
            if not line:
                continue

            tail.append(line)
            tail = tail[-20:]

            with _lock:
                runtime = _stream_runtime.setdefault(
                    camera_id,
                    {},
                )
                runtime["log_tail"] = list(tail)
                runtime["last_error"] = line
    except Exception as error:
        with _lock:
            runtime = _stream_runtime.setdefault(camera_id, {})
            runtime["last_error"] = (
                f"{type(error).__name__}: {error}"
            )
    finally:
        try:
            stderr.close()
        except Exception:
            pass


def _spawn_stream(camera_id: str) -> subprocess.Popen:
    config = _stream_configs[camera_id]
    cmd = build_ffmpeg_cmd(
        camera_id,
        config.get("mode", "copy"),
        int(config.get("fps", 15)),
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        ),
    )

    runtime = _stream_runtime.setdefault(camera_id, {})
    runtime["last_started_at"] = now_text()
    runtime["started_monotonic"] = time.monotonic()
    runtime["pid"] = proc.pid
    runtime["last_error"] = ""
    runtime["log_tail"] = []
    _ffmpeg_processes[camera_id] = proc

    stderr_thread = threading.Thread(
        target=_consume_ffmpeg_stderr,
        args=(camera_id, proc),
        name=f"ffmpeg-stderr-{camera_id}",
        daemon=True,
    )
    stderr_thread.start()

    return proc


def _ensure_watchdog() -> None:
    global _watchdog_started

    with _lock:
        if _watchdog_started:
            return

        thread = threading.Thread(
            target=_watchdog_loop,
            name="sandbox-stream-watchdog",
            daemon=True,
        )
        thread.start()
        _watchdog_started = True


def _watchdog_loop() -> None:
    while True:
        time.sleep(2)

        with _lock:
            camera_ids = [
                camera_id
                for camera_id, config in _stream_configs.items()
                if config.get("desired")
            ]

        for camera_id in camera_ids:
            with _lock:
                proc = _ffmpeg_processes.get(camera_id)
                config = _stream_configs.get(camera_id, {})
                runtime = _stream_runtime.setdefault(camera_id, {})

                if not config.get("desired"):
                    continue

                if proc is not None and proc.poll() is None:
                    started_monotonic = float(
                        runtime.get(
                            "started_monotonic",
                            time.monotonic(),
                        )
                    )
                    if (
                        time.monotonic() - started_monotonic
                        >= 10.0
                    ):
                        runtime["restart_count"] = 0
                        runtime["last_error"] = ""
                    continue

                if proc is not None:
                    exit_code = proc.poll()
                    runtime["last_exit_code"] = exit_code

                    if not runtime.get("last_error"):
                        runtime["last_error"] = (
                            f"ffmpeg exited with code {exit_code}"
                        )

                restart_count = int(runtime.get("restart_count", 0))
                last_restart_monotonic = float(
                    runtime.get("last_restart_monotonic", 0.0)
                )
                delay = min(12.0, 1.5 + restart_count * 1.5)

                if (
                    time.monotonic() - last_restart_monotonic
                    < delay
                ):
                    continue

                runtime["last_restart_monotonic"] = time.monotonic()
                runtime["restart_count"] = restart_count + 1

                try:
                    _spawn_stream(camera_id)
                    print(
                        "[STREAM WATCHDOG] restarted "
                        f"{camera_id}, count={runtime['restart_count']}"
                    )
                except Exception as error:
                    runtime["last_error"] = (
                        f"{type(error).__name__}: {error}"
                    )


def start_stream(
    camera_id: str,
    mode: str = "copy",
    fps: int = 15,
    force_restart: bool = False,
) -> bool:
    _validate_camera_id(camera_id)
    _ensure_watchdog()

    mode = "copy" if mode == "copy" else "transcode"
    fps = max(5, min(30, int(fps or 15)))

    with _lock:
        _stream_configs[camera_id] = {
            "desired": True,
            "mode": mode,
            "fps": fps,
        }

        existing = _ffmpeg_processes.get(camera_id)

        if (
            not force_restart
            and existing is not None
            and existing.poll() is None
        ):
            return True

        if existing is not None and existing.poll() is None:
            existing.terminate()
            try:
                existing.wait(timeout=3)
            except subprocess.TimeoutExpired:
                existing.kill()

        _spawn_stream(camera_id)
        return True


def stop_stream(camera_id: str) -> bool:
    _validate_camera_id(camera_id)

    with _lock:
        config = _stream_configs.setdefault(camera_id, {})
        config["desired"] = False

        proc = _ffmpeg_processes.pop(camera_id, None)
        if proc is None:
            return False

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

        runtime = _stream_runtime.setdefault(camera_id, {})
        runtime["last_exit_code"] = proc.returncode
        runtime["pid"] = None
        return True


def stop_all_streams() -> None:
    with _lock:
        camera_ids = list(_stream_configs.keys())

    for camera_id in camera_ids:
        try:
            stop_stream(camera_id)
        except Exception:
            pass


def get_stream_status(camera_id: str) -> dict:
    _validate_camera_id(camera_id)

    with _lock:
        proc = _ffmpeg_processes.get(camera_id)
        config = _stream_configs.get(camera_id, {})
        runtime = _stream_runtime.get(camera_id, {})

        exit_code = None
        running = False
        pid = None

        if proc is not None:
            exit_code = proc.poll()
            running = exit_code is None
            pid = proc.pid if running else None

        return {
            "camera_id": camera_id,
            "running": running,
            "desired": bool(config.get("desired")),
            "pid": pid,
            "exit_code": exit_code,
            "mode": config.get("mode", "copy"),
            "fps": int(config.get("fps", 15)),
            "restart_count": int(
                runtime.get("restart_count", 0)
            ),
            "last_started_at": runtime.get("last_started_at"),
            "last_exit_code": runtime.get("last_exit_code"),
            "last_error": runtime.get("last_error", ""),
            "log_tail": runtime.get("log_tail", []),
            "hls_url": (
                f"http://127.0.0.1:8888/"
                f"sandbox_{camera_id}/index.m3u8"
            ),
            "webrtc_url": (
                f"http://127.0.0.1:8889/"
                f"sandbox_{camera_id}"
            ),
        }


def get_all_streams_status() -> list[dict]:
    return [
        get_stream_status(item["id"])
        for item in SANDBOX_CAMERAS
    ]
