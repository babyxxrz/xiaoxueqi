from __future__ import annotations

from pathlib import Path
from typing import Any
import re
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


_lpr3_module = None
_lpr_catcher = None
_lpr_lock = threading.RLock()


def get_lpr_catcher():
    """
    延迟加载 HyperLPR3 模型。

    采用延迟加载与线程锁，避免 uvicorn --reload 或多路并发时重复初始化模型，
    同时降低 Windows 下模型文件被多个进程/线程同时访问造成的异常概率。
    """
    global _lpr3_module
    global _lpr_catcher

    if _lpr_catcher is not None:
        return _lpr_catcher

    with _lpr_lock:
        if _lpr_catcher is not None:
            return _lpr_catcher

        try:
            import hyperlpr3 as lpr3
        except ImportError as error:
            raise RuntimeError(
                "未安装 hyperlpr3。请先执行：pip install hyperlpr3"
            ) from error

        _lpr3_module = lpr3
        _lpr_catcher = lpr3.LicensePlateCatcher()

    return _lpr_catcher


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_plate_number(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"\s+", "", text)
    return text


def normalize_bbox(raw_box: Any, image_width: int, image_height: int) -> list[int]:
    """
    兼容常见 bbox 格式：
    1. [x1, y1, x2, y2]
    2. [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    3. [x1, y1, x2, y2, x3, y3, x4, y4]
    """
    fallback = [0, 0, max(0, image_width - 1), max(0, image_height - 1)]

    if raw_box is None:
        return fallback

    try:
        arr = np.asarray(raw_box, dtype=float).reshape(-1)

        if arr.size == 4:
            x1, y1, x2, y2 = arr.tolist()
        elif arr.size >= 8:
            xs = arr[0::2]
            ys = arr[1::2]
            x1, y1, x2, y2 = xs.min(), ys.min(), xs.max(), ys.max()
        else:
            return fallback

        x1 = max(0, min(int(round(x1)), image_width - 1))
        y1 = max(0, min(int(round(y1)), image_height - 1))
        x2 = max(0, min(int(round(x2)), image_width - 1))
        y2 = max(0, min(int(round(y2)), image_height - 1))

        if x2 <= x1 or y2 <= y1:
            return fallback

        return [x1, y1, x2, y2]
    except Exception:
        return fallback


def infer_plate_color(plate_number: str, plate_type: Any = None) -> str:
    text = str(plate_type or "")

    if "绿" in text or "新能源" in text:
        return "绿牌"
    if "黄" in text:
        return "黄牌"
    if "蓝" in text:
        return "蓝牌"
    if "白" in text:
        return "白牌"
    if "黑" in text:
        return "黑牌"

    # 新能源车牌一般为 8 位字符，普通车牌一般为 7 位字符。
    if len(plate_number) >= 8:
        return "绿牌"

    return "蓝牌"


def _looks_like_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple, np.ndarray)):
        return False

    try:
        size = np.asarray(value).reshape(-1).size
        return size in {4, 8} or size > 8
    except Exception:
        return False


def normalize_hyperlpr_result(item: Any, image_width: int, image_height: int) -> dict:
    """
    将不同版本 HyperLPR3 返回结果统一为：
    plate_number / plate_color / confidence / bbox / raw_type。
    """
    plate_number = ""
    confidence = 0.0
    plate_type = None
    raw_box = None

    if isinstance(item, dict):
        plate_number = (
            item.get("code")
            or item.get("plate")
            or item.get("plate_number")
            or item.get("text")
            or item.get("license")
            or ""
        )
        confidence = safe_float(
            item.get("confidence")
            or item.get("score")
            or item.get("text_confidence")
            or item.get("rec_confidence")
            or 0.0
        )
        plate_type = (
            item.get("type")
            or item.get("plate_type")
            or item.get("color")
            or item.get("plate_color")
        )
        raw_box = (
            item.get("box")
            or item.get("bbox")
            or item.get("points")
            or item.get("vertex")
        )

    elif isinstance(item, (list, tuple)):
        if len(item) >= 1:
            plate_number = item[0]
        if len(item) >= 2:
            confidence = safe_float(item[1])

        for part in item[2:]:
            if raw_box is None and _looks_like_bbox(part):
                raw_box = part
            elif plate_type is None and isinstance(part, (str, int, float)):
                plate_type = part

    plate_number = normalize_plate_number(plate_number)
    bbox = normalize_bbox(raw_box, image_width, image_height)
    plate_color = infer_plate_color(plate_number, plate_type)

    return {
        "plate_number": plate_number,
        "plate_color": plate_color,
        "confidence": round(max(0.0, min(confidence, 1.0)), 4),
        "bbox": bbox,
        "raw_type": str(plate_type) if plate_type is not None else "",
    }


def bbox_iou(box_a: list[int], box_b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - intersection

    if union <= 0:
        return 0.0

    return intersection / union


def deduplicate_plates(plates: list[dict]) -> list[dict]:
    """
    去除同一帧中模型可能重复返回的同一车牌。
    同号码优先保留高置信度；号码不同但框高度重叠时也只保留更可信结果。
    """
    kept: list[dict] = []

    for plate in sorted(
        plates,
        key=lambda item: float(item.get("confidence", 0) or 0),
        reverse=True,
    ):
        number = str(plate.get("plate_number") or "")
        box = plate.get("bbox") or [0, 0, 0, 0]

        duplicated = False
        for existing in kept:
            same_number = number and number == existing.get("plate_number")
            heavy_overlap = bbox_iou(box, existing.get("bbox") or [0, 0, 0, 0]) >= 0.75

            if same_number or heavy_overlap:
                duplicated = True
                break

        if not duplicated:
            kept.append(plate)

    return kept


def get_chinese_font(size: int = 28):
    font_candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]

    for font_path in font_candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)

    return ImageFont.load_default()


def draw_label_with_pil(
    image_bgr: np.ndarray,
    text: str,
    x: int,
    y: int,
    background_rgb: tuple[int, int, int] = (0, 150, 0),
):
    """使用 PIL 绘制中文，避免 OpenCV 中文乱码。"""
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil_image)
    font = get_chinese_font(26)

    x = max(0, int(x))
    y = max(0, int(y) - 34)

    text_bbox = draw.textbbox((x, y), text, font=font)
    bg_x1, bg_y1, bg_x2, bg_y2 = text_bbox

    draw.rectangle(
        [bg_x1 - 4, bg_y1 - 4, bg_x2 + 4, bg_y2 + 4],
        fill=background_rgb,
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


def recognize_plate_frame(
    image_bgr: np.ndarray,
    output_path: Path | None = None,
    force_confidence: float | None = None,
    min_confidence: float = 0.0,
) -> dict:
    """
    对一帧 BGR 图像执行 HyperLPR3 多车牌检测与 OCR。

    返回字段始终包含 plates、plate_count、latency_ms，方便图片、视频流和前端统一处理。
    """
    if image_bgr is None or not isinstance(image_bgr, np.ndarray):
        raise RuntimeError("输入图像为空或格式错误")

    started = time.perf_counter()
    image = image_bgr.copy()
    height, width = image.shape[:2]

    catcher = get_lpr_catcher()

    # HyperLPR3 推理对象可能不是线程安全的，多路流并发时统一串行调用该模型实例。
    with _lpr_lock:
        raw_results = catcher(image)

    if raw_results is None:
        raw_items: list[Any] = []
    elif isinstance(raw_results, dict):
        raw_items = [raw_results]
    elif isinstance(raw_results, tuple) and raw_results and isinstance(raw_results[0], str):
        raw_items = [raw_results]
    else:
        raw_items = list(raw_results)

    plates: list[dict] = []

    for item in raw_items:
        plate = normalize_hyperlpr_result(item, width, height)

        if not plate["plate_number"]:
            continue

        if force_confidence is not None:
            plate["confidence"] = round(float(force_confidence), 4)

        if float(plate["confidence"]) < float(min_confidence):
            continue

        plates.append(plate)

    plates = deduplicate_plates(plates)

    for plate in plates:
        x1, y1, x2, y2 = plate["bbox"]
        confidence = float(plate["confidence"])
        box_color = (0, 255, 0) if confidence >= 0.6 else (0, 165, 255)

        cv2.rectangle(image, (x1, y1), (x2, y2), box_color, 3)

        label = (
            f"{plate['plate_number']} "
            f"{plate['plate_color']} "
            f"{confidence:.2f}"
        )
        image = draw_label_with_pil(
            image,
            label,
            x1,
            y1,
            background_rgb=(0, 150, 0) if confidence >= 0.6 else (220, 120, 0),
        )

    if not plates:
        image = draw_label_with_pil(
            image,
            "未检测到车牌",
            30,
            60,
            background_rgb=(180, 90, 0),
        )

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        success = cv2.imwrite(str(output_path), image)

        if not success:
            raise RuntimeError("识别标注图保存失败")

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "model": "HyperLPR3",
        "plate_count": len(plates),
        "plates": plates,
        "latency_ms": latency_ms,
        "image_width": width,
        "image_height": height,
        "multi_plate_supported": True,
    }


def recognize_plate_real(
    input_path: Path,
    output_path: Path,
    force_confidence: float | None = None,
) -> dict:
    """读取图片文件并调用统一的多车牌识别入口。"""
    image = cv2.imread(str(input_path))

    if image is None:
        raise RuntimeError("图片读取失败，请检查图片格式是否正确")

    return recognize_plate_frame(
        image_bgr=image,
        output_path=output_path,
        force_confidence=force_confidence,
    )
