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


PLATE_TYPE_COLOR_MAP = {
    -1: "未知",
    0: "蓝牌",
    1: "黄牌",
    2: "白牌",
    3: "绿牌",
    4: "黑牌",
    5: "黑牌",
    6: "黑牌",
    7: "黑牌",
    8: "黑牌",
    9: "黄牌",
}


def _plate_type_index(plate_type: Any) -> int | None:
    if plate_type is None or isinstance(plate_type, bool):
        return None

    if isinstance(plate_type, (int, np.integer)):
        return int(plate_type)

    text = str(plate_type).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)

    return None


def infer_plate_color(plate_number: str, plate_type: Any = None) -> str:
    """
    将 HyperLPR3 的 plate_type 转换为中文车牌颜色。

    HyperLPR3 0.1.3 返回的类型索引为：
    0 蓝牌、1 单层黄牌、2 白牌、3 新能源绿牌、
    4~8 黑色港澳类车牌、9 双层黄牌。
    """
    type_index = _plate_type_index(plate_type)
    if type_index in PLATE_TYPE_COLOR_MAP:
        return PLATE_TYPE_COLOR_MAP[type_index]

    text = str(plate_type or "").strip()

    if "绿" in text or "新能源" in text:
        return "绿牌"
    if "黄" in text:
        return "黄牌"
    if "蓝" in text:
        return "蓝牌"
    if "白" in text:
        return "白牌"
    if "黑" in text or "港" in text or "澳" in text:
        return "黑牌"

    if len(plate_number) >= 8:
        return "绿牌"

    return "未知"


def estimate_plate_color_from_crop(
    image_bgr: np.ndarray | None,
    bbox: list[int],
) -> dict:
    """
    使用车牌框内 HSV 像素比例对颜色做轻量复核。

    该步骤只用于纠正明显的黄/蓝/绿分类冲突，不替代 HyperLPR3
    自带的车牌类型分类器。
    """
    empty = {
        "dominant_color": "未知",
        "yellow_ratio": 0.0,
        "blue_ratio": 0.0,
        "green_ratio": 0.0,
        "confidence": 0.0,
    }

    if image_bgr is None or not isinstance(image_bgr, np.ndarray):
        return empty

    height, width = image_bgr.shape[:2]
    if width <= 0 or height <= 0:
        return empty

    x1, y1, x2, y2 = normalize_bbox(bbox, width, height)
    if x2 <= x1 or y2 <= y1:
        return empty

    crop = image_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return empty

    crop_height, crop_width = crop.shape[:2]
    pad_x = max(1, int(crop_width * 0.05))
    pad_y = max(1, int(crop_height * 0.08))
    if crop_width > pad_x * 2 and crop_height > pad_y * 2:
        crop = crop[pad_y:crop_height - pad_y, pad_x:crop_width - pad_x]

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    total_pixels = max(1, hsv.shape[0] * hsv.shape[1])

    yellow_mask = cv2.inRange(
        hsv,
        np.array([14, 70, 55], dtype=np.uint8),
        np.array([42, 255, 255], dtype=np.uint8),
    )
    blue_mask = cv2.inRange(
        hsv,
        np.array([88, 65, 45], dtype=np.uint8),
        np.array([138, 255, 255], dtype=np.uint8),
    )
    green_mask = cv2.inRange(
        hsv,
        np.array([38, 55, 45], dtype=np.uint8),
        np.array([88, 255, 255], dtype=np.uint8),
    )

    ratios = {
        "黄牌": float(cv2.countNonZero(yellow_mask)) / total_pixels,
        "蓝牌": float(cv2.countNonZero(blue_mask)) / total_pixels,
        "绿牌": float(cv2.countNonZero(green_mask)) / total_pixels,
    }
    dominant_color, confidence = max(
        ratios.items(),
        key=lambda item: item[1],
    )

    if confidence < 0.10:
        dominant_color = "未知"

    return {
        "dominant_color": dominant_color,
        "yellow_ratio": round(ratios["黄牌"], 4),
        "blue_ratio": round(ratios["蓝牌"], 4),
        "green_ratio": round(ratios["绿牌"], 4),
        "confidence": round(confidence, 4),
    }


def resolve_plate_color(
    plate_number: str,
    plate_type: Any,
    image_bgr: np.ndarray | None,
    bbox: list[int],
) -> tuple[str, str, dict]:
    model_color = infer_plate_color(plate_number, plate_type)
    visual = estimate_plate_color_from_crop(image_bgr, bbox)
    visual_color = visual["dominant_color"]
    visual_confidence = float(visual["confidence"])

    if (
        visual_color == "黄牌"
        and visual_confidence >= 0.12
        and float(visual["yellow_ratio"])
        >= max(0.12, float(visual["blue_ratio"]) * 1.25)
        and model_color in {"未知", "蓝牌"}
    ):
        return "黄牌", "hsv_override", visual

    if (
        visual_color == "蓝牌"
        and visual_confidence >= 0.14
        and model_color == "未知"
    ):
        return "蓝牌", "hsv_fallback", visual

    if (
        visual_color == "绿牌"
        and visual_confidence >= 0.16
        and model_color == "未知"
    ):
        return "绿牌", "hsv_fallback", visual

    if model_color != "未知":
        return model_color, "hyperlpr_type", visual

    return "蓝牌", "default_fallback", visual


def _looks_like_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple, np.ndarray)):
        return False

    try:
        size = np.asarray(value).reshape(-1).size
        return size in {4, 8} or size > 8
    except Exception:
        return False


def normalize_hyperlpr_result(
    item: Any,
    image_width: int,
    image_height: int,
    image_bgr: np.ndarray | None = None,
) -> dict:
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
            or item.get("plate_code")
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
            if item.get("type") is not None
            else item.get("plate_type")
        )
        if plate_type is None:
            plate_type = item.get("color") or item.get("plate_color")
        raw_box = (
            item.get("box")
            or item.get("bbox")
            or item.get("det_bound_box")
            or item.get("points")
            or item.get("vertex")
        )

    elif isinstance(item, (list, tuple)):
        if len(item) >= 1:
            plate_number = item[0]
        if len(item) >= 2:
            confidence = safe_float(item[1])

        if len(item) >= 3:
            plate_type = item[2]
        if len(item) >= 4 and _looks_like_bbox(item[3]):
            raw_box = item[3]

        for part in item[2:]:
            if raw_box is None and _looks_like_bbox(part):
                raw_box = part
            elif plate_type is None and isinstance(part, (str, int, float, np.integer)):
                plate_type = part

    plate_number = normalize_plate_number(plate_number)
    bbox = normalize_bbox(raw_box, image_width, image_height)
    plate_color, color_source, color_scores = resolve_plate_color(
        plate_number=plate_number,
        plate_type=plate_type,
        image_bgr=image_bgr,
        bbox=bbox,
    )

    return {
        "plate_number": plate_number,
        "plate_color": plate_color,
        "plate_color_source": color_source,
        "plate_color_scores": color_scores,
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


def draw_plate_annotations(
    image_bgr: np.ndarray,
    plates: list[dict] | None,
    output_path: Path | None = None,
) -> np.ndarray:
    """使用已有 OCR 结果绘制车牌标注图，不再次调用 HyperLPR3。"""
    if image_bgr is None or not isinstance(image_bgr, np.ndarray):
        raise RuntimeError("车牌标注输入图像为空或格式错误")

    image = image_bgr.copy()
    plate_items = [item for item in (plates or []) if isinstance(item, dict)]

    for item in plate_items:
        x1, y1, x2, y2 = normalize_bbox(
            item.get("bbox") or [0, 0, 0, 0],
            image.shape[1],
            image.shape[0],
        )
        confidence = safe_float(item.get("confidence"), 0.0)
        box_color = (0, 255, 0) if confidence >= 0.6 else (0, 165, 255)
        cv2.rectangle(image, (x1, y1), (x2, y2), box_color, 3)

        number = item.get("plate_number") or item.get("plate") or item.get("text") or "未解析车牌"
        color = item.get("plate_color") or item.get("color") or "未知颜色"
        image = draw_label_with_pil(
            image,
            f"{number} {color} {confidence:.2f}",
            x1,
            y1,
            background_rgb=(0, 150, 0) if confidence >= 0.6 else (220, 120, 0),
        )

    if not plate_items:
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
        if not cv2.imwrite(str(output_path), image):
            raise RuntimeError("车牌最佳帧标注图保存失败")

    return image


def recognize_plate_frame(
    image_bgr: np.ndarray,
    output_path: Path | None = None,
    force_confidence: float | None = None,
    min_confidence: float = 0.0,
    draw_annotation: bool = True,
) -> dict:
    """
    对一帧 BGR 图像执行 HyperLPR3 多车牌检测与 OCR。

    draw_annotation=False 时跳过 PIL 中文标注和图像写盘，适用于
    批量视频与融合监控的高频抽帧识别。
    """
    if image_bgr is None or not isinstance(image_bgr, np.ndarray):
        raise RuntimeError("输入图像为空或格式错误")

    started = time.perf_counter()
    height, width = image_bgr.shape[:2]
    should_draw = bool(draw_annotation or output_path is not None)
    image = image_bgr.copy() if should_draw else None

    catcher = get_lpr_catcher()

    with _lpr_lock:
        raw_results = catcher(image_bgr)

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
        plate = normalize_hyperlpr_result(
            item,
            width,
            height,
            image_bgr=image_bgr,
        )

        if not plate["plate_number"]:
            continue

        if force_confidence is not None:
            plate["confidence"] = round(float(force_confidence), 4)

        if float(plate["confidence"]) < float(min_confidence):
            continue

        plates.append(plate)

    plates = deduplicate_plates(plates)

    if should_draw:
        image = draw_plate_annotations(
            image_bgr=image_bgr,
            plates=plates,
            output_path=output_path,
        )

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "model": "HyperLPR3",
        "plate_count": len(plates),
        "plates": plates,
        "latency_ms": latency_ms,
        "image_width": width,
        "image_height": height,
        "multi_plate_supported": True,
        "fast_mode": not should_draw,
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
