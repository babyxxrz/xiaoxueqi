"""
沙盘车牌识别模块（YOLOv8 + HyperLPR3）

针对沙盘场景优化的车牌识别流水线：
1. YOLOv8 检测车牌区域（针对沙盘小目标优化）
2. HyperLPR3 OCR 识别车牌号码
3. 多帧聚合去重，提升识别准确率

支持 ROI 区域配置，可按摄像头自定义检测区域。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import json
import math
import re
import threading
import time

import cv2
import numpy as np

from algorithm.plate_recognizer import (
    draw_plate_annotations,
    recognize_plate_frame,
)


_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "sandbox_plate_rois.json"
)
_CONFIG_LOCK = threading.RLock()
_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_MTIME_NS: int | None = None

_SANDBOX_SOURCE_PATTERN = re.compile(r"(?:^|[/_])live(?:[1-9]|1[0-2])(?:$|[/?#])", re.IGNORECASE)
_LOCAL_SANDBOX_PATTERN = re.compile(r"sandbox_live(?:[1-9]|1[0-2])", re.IGNORECASE)

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "default": {
        "whole_frame_first": True,
        "rois": [
            {
                "id": "road_area",
                "x1": 0.0,
                "y1": 0.12,
                "x2": 1.0,
                "y2": 1.0,
            }
        ],
        "roi_scale": 1.25,
        "tile_scale": 2.0,
        "max_enhanced_width": 1600,
        "max_enhanced_height": 1200,
        "auto_slice": True,
        "tile_width": 640,
        "tile_height": 480,
        "tile_overlap": 0.22,
        "max_tiles": 8,
        "max_inference_calls": 10,
        "frame_time_budget_seconds": 3.6,
        "multi_plate_scan": True,
        "target_plate_count": 0,
        "color_proposal_enabled": True,
        "color_proposal_max_candidates": 6,
        "color_proposal_scale": 3.0,
        "color_proposal_expand_x": 0.18,
        "color_proposal_expand_y": 0.30,
        "color_proposal_min_area": 180,
        "color_proposal_min_fill_ratio": 0.18,
        "detail_refine_enabled": True,
        "detail_refine_max_parents": 3,
        "detail_refine_max_windows": 1,
        "detail_refine_scale": 4.0,
        "detail_refine_strong_retry": True,
        "detail_refine_parent_aspect_max": 1.95,
        "detail_refine_min_parent_area": 3000,
        "focus_roi_enabled": False,
        "focus_roi_scale": 3.0,
        "focus_roi_candidate_min_confidence": 0.42,
        "focus_roi_strong_retry": True,
        "focus_roi_max_regions": 4,
        "target_count_min_confidence": 0.70,
        "frame_output_require_valid_format": True,
        "frame_output_min_confidence": 0.35,
        "focus_strong_retry_skip_confidence": 0.92,
        "detail_strong_retry_skip_confidence": 0.82,
        "skip_recognized_color_proposals": True,
        "color_proposal_unresolved_max_candidates": 4,
        "skip_general_fallback_when_focus_enabled": False,
        "candidate_min_confidence": 0.42,
        "vote_min_confidence": 0.54,
        "vote_min_appearances": 2,
        "single_frame_high_confidence": 0.86,
        "cluster_similarity": 0.70,
        "spatial_cluster_similarity": 0.35,
        "max_vote_frames": 4,
    },
    "sources": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_sandbox_plate_config() -> dict:
    global _CONFIG_CACHE
    global _CONFIG_MTIME_NS

    with _CONFIG_LOCK:
        try:
            mtime_ns = _CONFIG_PATH.stat().st_mtime_ns
        except OSError:
            mtime_ns = None

        if (
            _CONFIG_CACHE is not None
            and mtime_ns == _CONFIG_MTIME_NS
        ):
            return _CONFIG_CACHE

        config = dict(_DEFAULT_CONFIG)

        if _CONFIG_PATH.exists():
            try:
                loaded = json.loads(
                    _CONFIG_PATH.read_text(encoding="utf-8-sig")
                )
                if isinstance(loaded, dict):
                    config = _deep_merge(
                        _DEFAULT_CONFIG,
                        loaded,
                    )
            except Exception:
                # 配置文件损坏时使用内置安全默认值，不影响其他识别功能。
                config = dict(_DEFAULT_CONFIG)

        _CONFIG_CACHE = config
        _CONFIG_MTIME_NS = mtime_ns
        return config


def _source_fields(
    source: dict | None = None,
    *,
    source_id: str = "",
    source_url: str = "",
) -> tuple[str, str]:
    source = source or {}
    resolved_id = str(
        source_id
        or source.get("id")
        or source.get("source_id")
        or ""
    ).strip()
    resolved_url = str(
        source_url
        or source.get("url")
        or source.get("source_url")
        or ""
    ).strip()
    return resolved_id, resolved_url


def extract_sandbox_camera_id(
    source: dict | None = None,
    *,
    source_id: str = "",
    source_url: str = "",
) -> str:
    resolved_id, resolved_url = _source_fields(
        source,
        source_id=source_id,
        source_url=source_url,
    )

    direct = re.fullmatch(
        r"live(?:[1-9]|1[0-2])",
        resolved_id,
        flags=re.IGNORECASE,
    )
    if direct:
        return direct.group(0).lower()

    for text in (resolved_url, resolved_id):
        match = re.search(
            r"(?:sandbox_)?(live(?:[1-9]|1[0-2]))",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).lower()

    return ""


def is_sandbox_plate_source(
    source: dict | None = None,
    *,
    source_id: str = "",
    source_url: str = "",
) -> bool:
    """
    只识别老师沙盘 live1~live12 或本地 sandbox_live1~12。

    普通图片、本地上传视频、自定义摄像头、其他 RTSP 地址全部返回 False，
    因而继续使用原有车牌识别流水线。
    """
    config = load_sandbox_plate_config()
    if not bool(config.get("enabled", True)):
        return False

    resolved_id, resolved_url = _source_fields(
        source,
        source_id=source_id,
        source_url=source_url,
    )

    if extract_sandbox_camera_id(
        source_id=resolved_id,
        source_url=resolved_url,
    ):
        return True

    return bool(
        _SANDBOX_SOURCE_PATTERN.search(resolved_url)
        or _LOCAL_SANDBOX_PATTERN.search(resolved_url)
    )


def get_source_sandbox_settings(
    source_id: str,
    source_url: str = "",
) -> dict:
    config = load_sandbox_plate_config()
    camera_id = extract_sandbox_camera_id(
        source_id=source_id,
        source_url=source_url,
    )
    default_settings = config.get("default") or {}
    source_settings = (
        (config.get("sources") or {}).get(camera_id)
        or {}
    )
    settings = _deep_merge(default_settings, source_settings)
    settings["camera_id"] = camera_id
    return settings


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(float(value), maximum))


def _normalized_roi_to_bbox(
    roi: dict,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int] | None:
    try:
        x1 = _clamp(roi.get("x1", 0.0), 0.0, 1.0)
        y1 = _clamp(roi.get("y1", 0.0), 0.0, 1.0)
        x2 = _clamp(roi.get("x2", 1.0), 0.0, 1.0)
        y2 = _clamp(roi.get("y2", 1.0), 0.0, 1.0)
    except Exception:
        return None

    left = int(round(x1 * image_width))
    top = int(round(y1 * image_height))
    right = int(round(x2 * image_width))
    bottom = int(round(y2 * image_height))

    left = max(0, min(left, image_width - 1))
    top = max(0, min(top, image_height - 1))
    right = max(left + 1, min(right, image_width))
    bottom = max(top + 1, min(bottom, image_height))

    if right - left < 32 or bottom - top < 24:
        return None

    return left, top, right, bottom


def resolve_focus_rois(
    image_width: int,
    image_height: int,
    settings: dict,
) -> list[dict]:
    resolved: list[dict] = []

    for index, roi in enumerate(
        settings.get("focus_rois") or []
    ):
        if not isinstance(roi, dict):
            continue
        if roi.get("enabled") is False:
            continue

        bbox = _normalized_roi_to_bbox(
            roi,
            image_width,
            image_height,
        )
        if bbox is None:
            continue

        resolved.append({
            "id": str(
                roi.get("id")
                or f"focus_{index + 1}"
            ),
            "bbox": list(bbox),
            "scale": float(
                roi.get(
                    "scale",
                    settings.get(
                        "focus_roi_scale",
                        3.0,
                    ),
                )
            ),
            "strong_retry": bool(
                roi.get(
                    "strong_retry",
                    settings.get(
                        "focus_roi_strong_retry",
                        True,
                    ),
                )
            ),
            "candidate_min_confidence": float(
                roi.get(
                    "candidate_min_confidence",
                    settings.get(
                        "focus_roi_candidate_min_confidence",
                        0.42,
                    ),
                )
            ),
        })

    max_regions = max(
        1,
        int(
            settings.get(
                "focus_roi_max_regions",
                4,
            )
        ),
    )
    return resolved[:max_regions]


def resolve_source_rois(
    image_width: int,
    image_height: int,
    settings: dict,
) -> list[dict]:
    resolved: list[dict] = []

    for index, roi in enumerate(settings.get("rois") or []):
        if not isinstance(roi, dict):
            continue
        if roi.get("enabled") is False:
            continue

        bbox = _normalized_roi_to_bbox(
            roi,
            image_width,
            image_height,
        )
        if bbox is None:
            continue

        resolved.append({
            "id": str(roi.get("id") or f"roi_{index + 1}"),
            "bbox": list(bbox),
        })

    if not resolved:
        resolved.append({
            "id": "full_frame_fallback",
            "bbox": [0, 0, image_width, image_height],
        })

    return resolved


def _positions(total: int, window: int, overlap: float) -> list[int]:
    if total <= window:
        return [0]

    step = max(1, int(round(window * (1.0 - overlap))))
    values = list(range(0, max(1, total - window + 1), step))
    last = max(0, total - window)
    if not values or values[-1] != last:
        values.append(last)
    return sorted(set(values))


def generate_priority_tiles(
    roi_bbox: list[int] | tuple[int, int, int, int],
    *,
    tile_width: int,
    tile_height: int,
    overlap: float,
    max_tiles: int,
) -> list[dict]:
    x1, y1, x2, y2 = [int(value) for value in roi_bbox]
    roi_width = max(1, x2 - x1)
    roi_height = max(1, y2 - y1)
    tile_width = max(160, min(int(tile_width), roi_width))
    tile_height = max(120, min(int(tile_height), roi_height))
    overlap = _clamp(overlap, 0.0, 0.75)

    x_positions = _positions(roi_width, tile_width, overlap)
    y_positions = _positions(roi_height, tile_height, overlap)
    roi_center_x = x1 + roi_width / 2.0

    candidates: list[dict] = []
    for local_y in y_positions:
        for local_x in x_positions:
            left = x1 + local_x
            top = y1 + local_y
            right = min(x2, left + tile_width)
            bottom = min(y2, top + tile_height)
            center_x = (left + right) / 2.0
            center_distance = abs(center_x - roi_center_x) / max(1.0, roi_width)
            vertical_priority = bottom / max(1.0, y2)
            priority = vertical_priority * 2.0 - center_distance
            candidates.append({
                "bbox": [left, top, right, bottom],
                "priority": priority,
            })

    limit = max(1, int(max_tiles))

    if len(candidates) <= limit:
        limited = list(candidates)
    else:
        # 先选道路近端中心区域，再使用最远点采样覆盖上/中/下、左/中/右。
        # 旧逻辑只取 priority 最高的若干切片，容易全部集中在画面下方，
        # 导致远处和中部车辆车牌完全没有被扫描。
        remaining = list(candidates)
        first = max(
            remaining,
            key=lambda item: float(item["priority"]),
        )
        limited = [first]
        remaining.remove(first)

        def normalized_center(item: dict) -> tuple[float, float]:
            left, top, right, bottom = item["bbox"]
            center_x = ((left + right) / 2.0 - x1) / max(1.0, roi_width)
            center_y = ((top + bottom) / 2.0 - y1) / max(1.0, roi_height)
            return center_x, center_y

        while remaining and len(limited) < limit:
            selected_centers = [
                normalized_center(item)
                for item in limited
            ]

            def coverage_score(item: dict) -> float:
                center_x, center_y = normalized_center(item)
                min_distance = min(
                    math.sqrt(
                        (center_x - selected_x) ** 2
                        + (center_y - selected_y) ** 2
                    )
                    for selected_x, selected_y in selected_centers
                )
                # 空间覆盖为主，近端道路优先级只作轻量加分。
                return min_distance * 3.0 + float(item["priority"]) * 0.12

            selected = max(
                remaining,
                key=coverage_score,
            )
            limited.append(selected)
            remaining.remove(selected)

    for index, item in enumerate(limited, start=1):
        item["id"] = f"tile_{index}"

    return limited


def _enhance_crop(
    crop_bgr: np.ndarray,
    scale: float,
    *,
    max_width: int,
    max_height: int,
) -> tuple[np.ndarray, float]:
    requested_scale = _clamp(scale, 1.0, 4.0)
    crop_height, crop_width = crop_bgr.shape[:2]
    width_scale = max(1.0, int(max_width)) / max(1, crop_width)
    height_scale = max(1.0, int(max_height)) / max(1, crop_height)
    effective_scale = max(
        1.0,
        min(requested_scale, width_scale, height_scale),
    )
    enlarged = cv2.resize(
        crop_bgr,
        None,
        fx=effective_scale,
        fy=effective_scale,
        interpolation=cv2.INTER_CUBIC,
    )

    # 仅增强亮度通道，尽量保留蓝/绿/黄牌颜色信息。
    lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=1.8,
        tileGridSize=(8, 8),
    )
    l_channel = clahe.apply(l_channel)
    enhanced = cv2.cvtColor(
        cv2.merge((l_channel, a_channel, b_channel)),
        cv2.COLOR_LAB2BGR,
    )

    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(
        enhanced,
        1.22,
        blurred,
        -0.22,
        0,
    )
    return sharpened, effective_scale


def _enhance_crop_strong(
    crop_bgr: np.ndarray,
    scale: float,
    *,
    max_width: int,
    max_height: int,
) -> tuple[np.ndarray, float]:
    """
    仅用于沙盘小车牌精细候选的强增强版本。

    与普通车牌流程完全隔离。
    """
    requested_scale = _clamp(scale, 1.0, 4.0)
    crop_height, crop_width = crop_bgr.shape[:2]
    width_scale = max(1.0, int(max_width)) / max(1, crop_width)
    height_scale = max(1.0, int(max_height)) / max(1, crop_height)
    effective_scale = max(
        1.0,
        min(requested_scale, width_scale, height_scale),
    )

    enlarged = cv2.resize(
        crop_bgr,
        None,
        fx=effective_scale,
        fy=effective_scale,
        interpolation=cv2.INTER_LANCZOS4,
    )

    lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=2.8,
        tileGridSize=(6, 6),
    )
    l_channel = clahe.apply(l_channel)
    enhanced = cv2.cvtColor(
        cv2.merge((l_channel, a_channel, b_channel)),
        cv2.COLOR_LAB2BGR,
    )

    denoised = cv2.bilateralFilter(
        enhanced,
        5,
        28,
        28,
    )
    blurred = cv2.GaussianBlur(
        denoised,
        (0, 0),
        0.75,
    )
    sharpened = cv2.addWeighted(
        denoised,
        1.55,
        blurred,
        -0.55,
        0,
    )
    return sharpened, effective_scale


def _map_bbox_to_original(
    bbox: list[int],
    origin_bbox: list[int] | tuple[int, int, int, int],
    scale: float,
    image_width: int,
    image_height: int,
) -> list[int]:
    origin_x1, origin_y1, _, _ = [int(value) for value in origin_bbox]
    scale = max(1e-6, float(scale))

    x1 = int(round(origin_x1 + float(bbox[0]) / scale))
    y1 = int(round(origin_y1 + float(bbox[1]) / scale))
    x2 = int(round(origin_x1 + float(bbox[2]) / scale))
    y2 = int(round(origin_y1 + float(bbox[3]) / scale))

    x1 = max(0, min(x1, image_width - 1))
    y1 = max(0, min(y1, image_height - 1))
    x2 = max(x1 + 1, min(x2, image_width))
    y2 = max(y1 + 1, min(y2, image_height))
    return [x1, y1, x2, y2]


def _bbox_iou(box_a: list[int], box_b: list[int]) -> float:
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
    return intersection / union if union > 0 else 0.0


def _normalize_plate_text(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").upper())
    return "".join(
        char
        for char in text
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


_PLATE_PROVINCE_CHARS = set(
    "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领"
)


def is_valid_plate_number(value: Any) -> bool:
    """
    校验普通7位车牌和新能源8位车牌的基本字符结构。

    目标不是替代公安号牌规则，而是过滤类似：
    京D1桂A11、蒙1桂A1A21
    这类明显不可能的 OCR 垃圾结果。
    """
    text = _normalize_plate_text(value)

    if len(text) not in {7, 8}:
        return False
    if text[0] not in _PLATE_PROVINCE_CHARS:
        return False
    if not ("A" <= text[1] <= "Z"):
        return False

    suffix = text[2:]
    if not suffix:
        return False

    for char in suffix:
        if not (
            "A" <= char <= "Z"
            or "0" <= char <= "9"
        ):
            return False

    # 8位号牌按新能源常见结构约束：
    # 省份 + 地市字母 + D/F开头的6位序列，
    # 或最后一位为D/F的大型新能源号牌。
    if len(text) == 8:
        if suffix[0] not in {"D", "F"} and suffix[-1] not in {"D", "F"}:
            return False

    return True


def is_reliable_plate_candidate(
    candidate: dict,
    *,
    min_confidence: float,
) -> bool:
    confidence = float(
        candidate.get("confidence", 0) or 0
    )
    return bool(
        confidence >= float(min_confidence)
        and is_valid_plate_number(
            candidate.get("plate_number")
        )
    )


def _bbox_intersection_over_min_area(
    box_a: list[int],
    box_b: list[int],
) -> float:
    ax1, ay1, ax2, ay2 = [
        int(value)
        for value in box_a
    ]
    bx1, by1, bx2, by2 = [
        int(value)
        for value in box_b
    ]

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    intersection = max(0, ix2 - ix1) * max(
        0,
        iy2 - iy1,
    )
    area_a = max(0, ax2 - ax1) * max(
        0,
        ay2 - ay1,
    )
    area_b = max(0, bx2 - bx1) * max(
        0,
        by2 - by1,
    )
    denominator = min(area_a, area_b)

    return (
        intersection / denominator
        if denominator > 0
        else 0.0
    )


def _proposal_overlaps_reliable_candidates(
    proposal: dict,
    candidates: list[dict],
    *,
    min_confidence: float,
) -> bool:
    proposal_box = (
        proposal.get("bbox")
        or [0, 0, 0, 0]
    )

    for candidate in candidates:
        if not is_reliable_plate_candidate(
            candidate,
            min_confidence=min_confidence,
        ):
            continue

        candidate_box = (
            candidate.get("bbox")
            or [0, 0, 0, 0]
        )

        if (
            _bbox_intersection_over_min_area(
                proposal_box,
                candidate_box,
            )
            >= 0.62
        ):
            return True

    return False


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1]
                + (0 if left_char == right_char else 1),
            ))
        previous = current
    return previous[-1]


def plate_text_similarity(left: str, right: str) -> float:
    left = _normalize_plate_text(left)
    right = _normalize_plate_text(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    full = 1.0 - _edit_distance(left, right) / max(len(left), len(right))
    left_suffix = left[1:] if "\u4e00" <= left[0] <= "\u9fff" else left
    right_suffix = right[1:] if "\u4e00" <= right[0] <= "\u9fff" else right
    suffix = 1.0 - _edit_distance(left_suffix, right_suffix) / max(
        1,
        len(left_suffix),
        len(right_suffix),
    )
    return max(full, suffix)


def _deduplicate_frame_candidates(
    candidates: list[dict],
) -> list[dict]:
    kept: list[dict] = []

    for candidate in sorted(
        candidates,
        key=lambda item: float(item.get("confidence", 0) or 0),
        reverse=True,
    ):
        number = _normalize_plate_text(candidate.get("plate_number"))
        if not number:
            continue
        candidate["plate_number"] = number
        box = candidate.get("bbox") or [0, 0, 0, 0]

        duplicate = False
        for existing in kept:
            same_number = number == existing.get("plate_number")
            overlap = _bbox_iou(
                box,
                existing.get("bbox") or [0, 0, 0, 0],
            )
            similar_number = plate_text_similarity(
                number,
                existing.get("plate_number", ""),
            ) >= 0.82

            if same_number or overlap >= 0.58 or (overlap >= 0.28 and similar_number):
                duplicate = True
                break

        if not duplicate:
            kept.append(candidate)

    return kept



def _expand_bbox(
    bbox: list[int] | tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    *,
    expand_x: float,
    expand_y: float,
) -> list[int]:
    x1, y1, x2, y2 = [int(value) for value in bbox]
    width = max(1, x2 - x1)
    height = max(1, y2 - y1)

    dx = int(round(width * max(0.0, float(expand_x))))
    dy = int(round(height * max(0.0, float(expand_y))))

    return [
        max(0, x1 - dx),
        max(0, y1 - dy),
        min(image_width, x2 + dx),
        min(image_height, y2 + dy),
    ]


def _proposal_iou(
    left: dict,
    right: dict,
) -> float:
    return _bbox_iou(
        left.get("bbox") or [0, 0, 0, 0],
        right.get("bbox") or [0, 0, 0, 0],
    )


def detect_colored_plate_proposals(
    image_bgr: np.ndarray,
    *,
    max_candidates: int = 6,
    min_area: int = 180,
    min_fill_ratio: float = 0.18,
    expand_x: float = 0.18,
    expand_y: float = 0.30,
) -> list[dict]:
    """
    沙盘专用颜色候选定位。

    沙盘中的蓝牌/绿牌/黄牌区域颜色明显，先用 HSV 找出横向彩色矩形，
    再把候选小区域交给 HyperLPR。该逻辑只在沙盘识别器中调用，
    不影响普通图片、本地视频和其他 RTSP。
    """
    if image_bgr is None or image_bgr.size == 0:
        return []

    image_height, image_width = image_bgr.shape[:2]
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

    color_ranges = [
        (
            "blue",
            np.array([88, 65, 35], dtype=np.uint8),
            np.array([145, 255, 255], dtype=np.uint8),
        ),
        (
            "green",
            np.array([34, 55, 35], dtype=np.uint8),
            np.array([92, 255, 255], dtype=np.uint8),
        ),
        (
            "yellow",
            np.array([12, 70, 55], dtype=np.uint8),
            np.array([42, 255, 255], dtype=np.uint8),
        ),
    ]

    proposals: list[dict] = []

    for color_name, lower, upper in color_ranges:
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (5, 3),
            ),
            iterations=2,
        )
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (3, 3),
            ),
            iterations=1,
        )

        contours, _ = cv2.findContours(
            mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            contour_area = float(cv2.contourArea(contour))
            rectangle_area = float(max(1, width * height))
            fill_ratio = contour_area / rectangle_area
            aspect_ratio = width / max(1.0, float(height))

            if rectangle_area < max(80, int(min_area)):
                continue
            if width < 22 or height < 7:
                continue
            if not 1.20 <= aspect_ratio <= 8.5:
                continue
            if fill_ratio < float(min_fill_ratio):
                continue

            # 排除画面边缘过细的灯带、道路指示灯等长条区域。
            if (
                aspect_ratio > 6.0
                and height < 14
            ):
                continue

            raw_bbox = [x, y, x + width, y + height]
            expanded_bbox = _expand_bbox(
                raw_bbox,
                image_width,
                image_height,
                expand_x=expand_x,
                expand_y=expand_y,
            )

            crop = image_bgr[
                expanded_bbox[1]:expanded_bbox[3],
                expanded_bbox[0]:expanded_bbox[2],
            ]
            if crop.size == 0:
                continue

            crop_hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            white_ratio = float(
                np.mean(
                    (
                        crop_hsv[:, :, 1] < 95
                    )
                    & (
                        crop_hsv[:, :, 2] > 135
                    )
                )
            )
            edge_density = float(
                np.mean(
                    cv2.Canny(
                        cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                        55,
                        150,
                    )
                    > 0
                )
            )

            size_score = min(
                1.0,
                rectangle_area
                / max(1.0, image_width * image_height * 0.008),
            )
            aspect_score = max(
                0.0,
                1.0 - abs(aspect_ratio - 2.7) / 4.0,
            )
            color_priority = {
                "blue": 1.0,
                "green": 0.55,
                "yellow": 0.30,
            }.get(color_name, 0.0)

            score = (
                fill_ratio * 1.8
                + white_ratio * 1.2
                + edge_density * 1.0
                + size_score * 0.8
                + aspect_score * 0.5
                + color_priority
            )

            proposals.append({
                "bbox": expanded_bbox,
                "raw_bbox": raw_bbox,
                "color_hint": color_name,
                "score": round(float(score), 6),
                "fill_ratio": round(fill_ratio, 4),
                "white_ratio": round(white_ratio, 4),
                "edge_density": round(edge_density, 4),
                "aspect_ratio": round(aspect_ratio, 4),
            })

    kept: list[dict] = []
    for proposal in sorted(
        proposals,
        key=lambda item: float(item["score"]),
        reverse=True,
    ):
        if any(
            _proposal_iou(proposal, existing) >= 0.45
            for existing in kept
        ):
            continue
        kept.append(proposal)
        if len(kept) >= max(1, int(max_candidates)):
            break

    return kept



def _integral_sum(
    integral: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
) -> float:
    x2 = x + width
    y2 = y + height
    return float(
        integral[y2, x2]
        - integral[y, x2]
        - integral[y2, x]
        + integral[y, x]
    )


def detect_inner_plate_subproposals(
    image_bgr: np.ndarray,
    parent_bbox: list[int],
    *,
    max_candidates: int = 1,
) -> list[dict]:
    """
    在“大块蓝色车身候选”内部寻找车牌字符带。

    核心特征：
    - 蓝色背景占比；
    - 白色字符占比；
    - 字符边缘密度；
    - 横向车牌宽高比。

    该函数仅由沙盘颜色候选阶段调用。
    """
    image_height, image_width = image_bgr.shape[:2]
    px1, py1, px2, py2 = [
        int(value)
        for value in parent_bbox
    ]
    parent = image_bgr[py1:py2, px1:px2]

    if parent.size == 0:
        return []

    height, width = parent.shape[:2]
    if width < 36 or height < 24:
        return []

    hsv = cv2.cvtColor(parent, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(parent, cv2.COLOR_BGR2GRAY)

    blue_mask = (
        (hsv[:, :, 0] >= 88)
        & (hsv[:, :, 0] <= 145)
        & (hsv[:, :, 1] >= 55)
        & (hsv[:, :, 2] >= 30)
    ).astype(np.uint8)

    white_mask = (
        (hsv[:, :, 1] <= 120)
        & (hsv[:, :, 2] >= 110)
    ).astype(np.uint8)

    edge_mask = (
        cv2.Canny(gray, 45, 145) > 0
    ).astype(np.uint8)

    blue_integral = cv2.integral(
        blue_mask,
        sdepth=cv2.CV_64F,
    )
    white_integral = cv2.integral(
        white_mask,
        sdepth=cv2.CV_64F,
    )
    edge_integral = cv2.integral(
        edge_mask,
        sdepth=cv2.CV_64F,
    )

    raw_windows: list[dict] = []

    for width_ratio in (
        0.30,
        0.38,
        0.46,
        0.55,
        0.66,
    ):
        window_width = max(
            24,
            int(round(width * width_ratio)),
        )

        for aspect_ratio in (
            2.4,
            2.8,
            3.2,
            3.8,
            4.5,
        ):
            window_height = max(
                8,
                int(round(window_width / aspect_ratio)),
            )

            if window_height >= height:
                continue

            step_x = max(
                2,
                int(round(window_width * 0.08)),
            )
            step_y = max(
                2,
                int(round(window_height * 0.18)),
            )

            for local_y in range(
                0,
                height - window_height + 1,
                step_y,
            ):
                for local_x in range(
                    0,
                    width - window_width + 1,
                    step_x,
                ):
                    area = float(
                        window_width * window_height
                    )
                    blue_ratio = _integral_sum(
                        blue_integral,
                        local_x,
                        local_y,
                        window_width,
                        window_height,
                    ) / area
                    white_ratio = _integral_sum(
                        white_integral,
                        local_x,
                        local_y,
                        window_width,
                        window_height,
                    ) / area
                    edge_density = _integral_sum(
                        edge_integral,
                        local_x,
                        local_y,
                        window_width,
                        window_height,
                    ) / area

                    if blue_ratio < 0.16:
                        continue
                    if edge_density < 0.04:
                        continue

                    center_prior = (
                        1.0
                        - abs(
                            (
                                local_x
                                + window_width / 2.0
                            )
                            / max(1.0, width)
                            - 0.5
                        )
                    )
                    vertical_prior = (
                        local_y
                        + window_height / 2.0
                    ) / max(1.0, height)

                    score = (
                        blue_ratio * 2.50
                        + white_ratio * 1.10
                        + edge_density * 1.50
                        + center_prior * 0.15
                        + vertical_prior * 0.12
                    )

                    raw_windows.append({
                        "x": local_x,
                        "y": local_y,
                        "width": window_width,
                        "height": window_height,
                        "score": round(
                            float(score),
                            6,
                        ),
                        "blue_ratio": round(
                            float(blue_ratio),
                            4,
                        ),
                        "white_ratio": round(
                            float(white_ratio),
                            4,
                        ),
                        "edge_density": round(
                            float(edge_density),
                            4,
                        ),
                    })

    selected: list[dict] = []

    for window in sorted(
        raw_windows,
        key=lambda item: float(item["score"]),
        reverse=True,
    ):
        local_x = int(window["x"])
        local_y = int(window["y"])
        window_width = int(window["width"])
        window_height = int(window["height"])

        # 原始窗口通常落在字符最密集的中部。
        # 横向和纵向适度扩展后，覆盖完整车牌边框。
        left = max(
            0,
            int(round(
                local_x - window_width * 0.65
            )),
        )
        top = max(
            0,
            int(round(
                local_y - window_height * 1.00
            )),
        )
        right = min(
            width,
            int(round(
                local_x + window_width * 1.65
            )),
        )
        bottom = min(
            height,
            int(round(
                local_y + window_height * 2.00
            )),
        )

        global_bbox = [
            max(0, min(image_width - 1, px1 + left)),
            max(0, min(image_height - 1, py1 + top)),
            max(1, min(image_width, px1 + right)),
            max(1, min(image_height, py1 + bottom)),
        ]

        if (
            global_bbox[2] - global_bbox[0] < 36
            or global_bbox[3] - global_bbox[1] < 12
        ):
            continue

        if any(
            _bbox_iou(
                global_bbox,
                existing["bbox"],
            ) >= 0.55
            for existing in selected
        ):
            continue

        selected.append({
            "bbox": global_bbox,
            "score": window["score"],
            "blue_ratio": window["blue_ratio"],
            "white_ratio": window["white_ratio"],
            "edge_density": window["edge_density"],
            "character_band_bbox": [
                px1 + local_x,
                py1 + local_y,
                px1 + local_x + window_width,
                py1 + local_y + window_height,
            ],
        })

        if len(selected) >= max(
            1,
            int(max_candidates),
        ):
            break

    return selected


def _consolidate_parent_ocr_variants(
    candidates: list[dict],
) -> list[dict]:
    """
    同一个颜色候选可能产生：
    - 大候选框 OCR；
    - 精细子框标准增强 OCR；
    - 精细子框强增强 OCR。

    先在单帧内按父候选合并，避免同一辆车重复计数。
    """
    grouped: dict[str, list[dict]] = {}
    passthrough: list[dict] = []

    for item in candidates:
        parent_id = str(
            item.get("sandbox_parent_proposal_id")
            or ""
        ).strip()

        if not parent_id:
            passthrough.append(item)
            continue

        grouped.setdefault(
            parent_id,
            [],
        ).append(item)

    consolidated: list[dict] = []

    for parent_id, members in grouped.items():
        if not members:
            continue

        weighted_members: list[dict] = []
        variant_rows: list[dict] = []

        for member in members:
            stage = str(
                member.get("sandbox_stage") or ""
            )
            variant = str(
                member.get("sandbox_ocr_variant")
                or "standard"
            )

            stage_bonus = 1.0
            if stage == "color_detail":
                stage_bonus = 1.08
            if variant == "strong":
                stage_bonus += 0.04

            weighted = dict(member)
            weighted["confidence"] = min(
                1.0,
                float(
                    member.get("confidence", 0)
                    or 0
                ) * stage_bonus,
            )
            weighted_members.append(weighted)

            variant_rows.append({
                "plate_number": member.get(
                    "plate_number"
                ),
                "confidence": round(
                    float(
                        member.get("confidence", 0)
                        or 0
                    ),
                    4,
                ),
                "stage": stage,
                "variant": variant,
            })

        voted_number = _character_vote(
            weighted_members
        )

        best_member = max(
            weighted_members,
            key=lambda item: float(
                item.get("confidence", 0)
                or 0
            ),
        )
        output = dict(best_member)
        output["plate_number"] = voted_number
        output["confidence"] = round(
            max(
                float(
                    member.get("confidence", 0)
                    or 0
                )
                for member in members
            ),
            4,
        )
        output["sandbox_parent_proposal_id"] = (
            parent_id
        )
        output["sandbox_ocr_variants"] = (
            variant_rows
        )
        output["sandbox_ocr_variant_count"] = len(
            variant_rows
        )
        consolidated.append(output)

    consolidated.extend(passthrough)
    return consolidated


def _run_region_recognition(
    image_bgr: np.ndarray,
    region_bbox: list[int],
    *,
    stage: str,
    region_id: str,
    scale: float,
    candidate_min_confidence: float,
    max_enhanced_width: int,
    max_enhanced_height: int,
    enhancement_mode: str = "standard",
) -> tuple[list[dict], float]:
    x1, y1, x2, y2 = region_bbox
    crop = image_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return [], 0.0

    if enhancement_mode == "strong":
        enhanced, effective_scale = (
            _enhance_crop_strong(
                crop,
                scale,
                max_width=max_enhanced_width,
                max_height=max_enhanced_height,
            )
        )
    else:
        enhanced, effective_scale = _enhance_crop(
            crop,
            scale,
            max_width=max_enhanced_width,
            max_height=max_enhanced_height,
        )
    started = time.perf_counter()
    result = recognize_plate_frame(
        enhanced,
        output_path=None,
        min_confidence=0.0,
        draw_annotation=False,
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    image_height, image_width = image_bgr.shape[:2]
    mapped: list[dict] = []

    for plate in result.get("plates", []) or []:
        confidence = float(plate.get("confidence", 0) or 0)
        if confidence < candidate_min_confidence:
            continue

        item = dict(plate)
        item["plate_number"] = _normalize_plate_text(
            plate.get("plate_number")
        )
        if not item["plate_number"]:
            continue

        item["bbox"] = _map_bbox_to_original(
            plate.get("bbox") or [0, 0, enhanced.shape[1], enhanced.shape[0]],
            region_bbox,
            effective_scale,
            image_width,
            image_height,
        )
        item["sandbox_stage"] = stage
        item["sandbox_region_id"] = region_id
        item["sandbox_scale"] = round(float(effective_scale), 3)
        item["sandbox_source_bbox"] = list(region_bbox)
        item["sandbox_ocr_variant"] = enhancement_mode
        mapped.append(item)

    return mapped, latency_ms


def recognize_sandbox_plate_frame(
    image_bgr: np.ndarray,
    *,
    source_id: str,
    source_url: str = "",
    output_path: Path | None = None,
    draw_annotation: bool = False,
    enable_auto_slicing: bool = True,
) -> dict:
    """
    沙盘视频专用免训练增强识别。

    此函数只能由 is_sandbox_plate_source() 为 True 的视频流路径调用。
    普通图片、本地视频和非沙盘 RTSP 继续调用原 recognize_plate_frame()。
    """
    if image_bgr is None or not isinstance(image_bgr, np.ndarray):
        raise RuntimeError("沙盘车牌识别输入帧为空")

    started = time.perf_counter()
    image_height, image_width = image_bgr.shape[:2]
    settings = get_source_sandbox_settings(source_id, source_url)
    camera_id = str(settings.get("camera_id") or source_id or "sandbox")
    candidate_min_confidence = float(
        settings.get("candidate_min_confidence", 0.45)
    )
    max_calls = max(1, int(settings.get("max_inference_calls", 8)))
    time_budget = max(
        0.5,
        float(settings.get("frame_time_budget_seconds", 2.8)),
    )
    multi_plate_scan = bool(
        settings.get("multi_plate_scan", True)
    )
    color_proposal_enabled = bool(
        settings.get("color_proposal_enabled", True)
    )
    color_proposal_max_candidates = max(
        1,
        int(settings.get("color_proposal_max_candidates", 6)),
    )
    color_proposal_scale = float(
        settings.get("color_proposal_scale", 3.0)
    )
    detail_refine_enabled = bool(
        settings.get("detail_refine_enabled", True)
    )
    detail_refine_max_parents = max(
        1,
        int(
            settings.get(
                "detail_refine_max_parents",
                3,
            )
        ),
    )
    detail_refine_max_windows = max(
        1,
        int(
            settings.get(
                "detail_refine_max_windows",
                1,
            )
        ),
    )
    detail_refine_scale = float(
        settings.get("detail_refine_scale", 4.0)
    )
    detail_refine_strong_retry = bool(
        settings.get(
            "detail_refine_strong_retry",
            True,
        )
    )
    detail_refine_parent_aspect_max = float(
        settings.get(
            "detail_refine_parent_aspect_max",
            1.95,
        )
    )
    detail_refine_min_parent_area = max(
        1,
        int(
            settings.get(
                "detail_refine_min_parent_area",
                3000,
            )
        ),
    )
    focus_roi_enabled = bool(
        settings.get("focus_roi_enabled", False)
    )
    focus_roi_scale = float(
        settings.get("focus_roi_scale", 3.0)
    )
    focus_roi_candidate_min_confidence = float(
        settings.get(
            "focus_roi_candidate_min_confidence",
            candidate_min_confidence,
        )
    )
    focus_roi_strong_retry = bool(
        settings.get("focus_roi_strong_retry", True)
    )
    target_count_min_confidence = float(
        settings.get(
            "target_count_min_confidence",
            0.70,
        )
    )
    frame_output_require_valid_format = bool(
        settings.get(
            "frame_output_require_valid_format",
            True,
        )
    )
    frame_output_min_confidence = float(
        settings.get(
            "frame_output_min_confidence",
            0.35,
        )
    )
    focus_strong_retry_skip_confidence = float(
        settings.get(
            "focus_strong_retry_skip_confidence",
            0.92,
        )
    )
    detail_strong_retry_skip_confidence = float(
        settings.get(
            "detail_strong_retry_skip_confidence",
            0.82,
        )
    )
    skip_recognized_color_proposals = bool(
        settings.get(
            "skip_recognized_color_proposals",
            True,
        )
    )
    color_proposal_unresolved_max_candidates = max(
        1,
        int(
            settings.get(
                "color_proposal_unresolved_max_candidates",
                4,
            )
        ),
    )
    skip_general_fallback_when_focus_enabled = bool(
        settings.get(
            "skip_general_fallback_when_focus_enabled",
            False,
        )
    )
    target_plate_count = max(
        0,
        int(settings.get("target_plate_count", 0) or 0),
    )
    max_enhanced_width = max(640, int(settings.get("max_enhanced_width", 1600)))
    max_enhanced_height = max(480, int(settings.get("max_enhanced_height", 1200)))
    deadline = started + time_budget

    candidates: list[dict] = []
    stage_stats: list[dict] = []
    inference_calls = 0
    resolved_rois = resolve_source_rois(
        image_width,
        image_height,
        settings,
    )
    resolved_focus_rois = (
        resolve_focus_rois(
            image_width,
            image_height,
            settings,
        )
        if focus_roi_enabled
        else []
    )

    def can_continue() -> bool:
        return (
            inference_calls < max_calls
            and time.perf_counter() < deadline
        )

    def current_unique_plate_count() -> int:
        reliable_candidates = [
            dict(item)
            for item in candidates
            if is_reliable_plate_candidate(
                item,
                min_confidence=(
                    target_count_min_confidence
                ),
            )
        ]
        return len(
            _deduplicate_frame_candidates(
                reliable_candidates
            )
        )

    def target_reached() -> bool:
        return bool(
            target_plate_count > 0
            and current_unique_plate_count() >= target_plate_count
        )

    focus_candidates: list[dict] = []
    focus_latency_ms = 0.0
    focus_regions_processed = 0
    focus_strong_retries = 0
    focus_strong_skipped = 0

    if (
        focus_roi_enabled
        and resolved_focus_rois
        and can_continue()
    ):
        for focus_roi in resolved_focus_rois:
            if not can_continue() or target_reached():
                break

            focus_id = str(focus_roi["id"])
            parent_id = f"focus:{focus_id}"
            focus_min_confidence = float(
                focus_roi.get(
                    "candidate_min_confidence",
                    focus_roi_candidate_min_confidence,
                )
            )
            focus_scale = float(
                focus_roi.get(
                    "scale",
                    focus_roi_scale,
                )
            )

            standard_result, standard_latency = (
                _run_region_recognition(
                    image_bgr,
                    focus_roi["bbox"],
                    stage="focus_roi",
                    region_id=focus_id,
                    scale=focus_scale,
                    candidate_min_confidence=(
                        focus_min_confidence
                    ),
                    max_enhanced_width=(
                        max_enhanced_width
                    ),
                    max_enhanced_height=(
                        max_enhanced_height
                    ),
                    enhancement_mode="standard",
                )
            )
            inference_calls += 1
            focus_regions_processed += 1
            focus_latency_ms += standard_latency

            for item in standard_result:
                item[
                    "sandbox_parent_proposal_id"
                ] = parent_id
                item["sandbox_focus_roi_id"] = (
                    focus_id
                )

            focus_candidates.extend(standard_result)
            candidates.extend(standard_result)

            standard_has_reliable_result = any(
                is_reliable_plate_candidate(
                    item,
                    min_confidence=(
                        focus_strong_retry_skip_confidence
                    ),
                )
                for item in standard_result
            )

            should_run_strong = bool(
                focus_roi.get(
                    "strong_retry",
                    focus_roi_strong_retry,
                )
                and not standard_has_reliable_result
                and can_continue()
                and not target_reached()
            )

            if (
                focus_roi.get(
                    "strong_retry",
                    focus_roi_strong_retry,
                )
                and standard_has_reliable_result
            ):
                focus_strong_skipped += 1

            if should_run_strong:
                focus_strong_retries += 1
                strong_result, strong_latency = (
                    _run_region_recognition(
                        image_bgr,
                        focus_roi["bbox"],
                        stage="focus_roi",
                        region_id=focus_id,
                        scale=focus_scale,
                        candidate_min_confidence=(
                            focus_min_confidence
                        ),
                        max_enhanced_width=(
                            max_enhanced_width
                        ),
                        max_enhanced_height=(
                            max_enhanced_height
                        ),
                        enhancement_mode="strong",
                    )
                )
                inference_calls += 1
                focus_regions_processed += 1
                focus_latency_ms += strong_latency

                for item in strong_result:
                    item[
                        "sandbox_parent_proposal_id"
                    ] = parent_id
                    item["sandbox_focus_roi_id"] = (
                        focus_id
                    )

                focus_candidates.extend(strong_result)
                candidates.extend(strong_result)

        stage_stats.append({
            "stage": "focus_roi",
            "regions": focus_regions_processed,
            "configured_regions": len(
                resolved_focus_rois
            ),
            "candidates": len(focus_candidates),
            "strong_retries": focus_strong_retries,
            "strong_skipped": focus_strong_skipped,
            "reliable_candidates": len([
                item
                for item in focus_candidates
                if is_reliable_plate_candidate(
                    item,
                    min_confidence=(
                        target_count_min_confidence
                    ),
                )
            ]),
            "latency_ms": round(
                focus_latency_ms,
                2,
            ),
            "focus_boxes": [
                {
                    "id": item["id"],
                    "bbox": item["bbox"],
                    "scale": item["scale"],
                }
                for item in resolved_focus_rois
            ],
        })

    color_proposals: list[dict] = []
    detected_color_proposals: list[dict] = []
    skipped_recognized_proposals = 0

    if (
        color_proposal_enabled
        and can_continue()
        and not target_reached()
    ):
        detected_color_proposals = detect_colored_plate_proposals(
            image_bgr,
            max_candidates=color_proposal_max_candidates,
            min_area=int(
                settings.get("color_proposal_min_area", 180)
            ),
            min_fill_ratio=float(
                settings.get(
                    "color_proposal_min_fill_ratio",
                    0.18,
                )
            ),
            expand_x=float(
                settings.get(
                    "color_proposal_expand_x",
                    0.18,
                )
            ),
            expand_y=float(
                settings.get(
                    "color_proposal_expand_y",
                    0.30,
                )
            ),
        )

        for proposal in detected_color_proposals:
            if (
                skip_recognized_color_proposals
                and _proposal_overlaps_reliable_candidates(
                    proposal,
                    focus_candidates,
                    min_confidence=(
                        target_count_min_confidence
                    ),
                )
            ):
                skipped_recognized_proposals += 1
                continue

            color_proposals.append(proposal)

            if (
                len(color_proposals)
                >= color_proposal_unresolved_max_candidates
            ):
                break

        proposal_candidates: list[dict] = []
        detail_candidates: list[dict] = []
        detail_windows_debug: list[dict] = []
        proposal_latency_ms = 0.0
        detail_latency_ms = 0.0
        proposal_regions = 0
        detail_regions = 0
        refined_parent_count = 0

        for proposal_index, proposal in enumerate(
            color_proposals,
            start=1,
        ):
            if not can_continue() or target_reached():
                break

            parent_id = f"color_{proposal_index}"

            region_candidates, latency_ms = _run_region_recognition(
                image_bgr,
                proposal["bbox"],
                stage="color_proposal",
                region_id=parent_id,
                scale=color_proposal_scale,
                candidate_min_confidence=candidate_min_confidence,
                max_enhanced_width=max_enhanced_width,
                max_enhanced_height=max_enhanced_height,
            )
            inference_calls += 1
            proposal_regions += 1
            proposal_latency_ms += latency_ms

            for item in region_candidates:
                item["sandbox_color_hint"] = proposal["color_hint"]
                item["sandbox_proposal_score"] = proposal["score"]
                item["sandbox_parent_proposal_id"] = parent_id

            proposal_candidates.extend(region_candidates)
            candidates.extend(region_candidates)

            proposal_bbox = proposal["bbox"]
            proposal_width = max(
                1,
                int(proposal_bbox[2] - proposal_bbox[0]),
            )
            proposal_height = max(
                1,
                int(proposal_bbox[3] - proposal_bbox[1]),
            )
            proposal_area = proposal_width * proposal_height
            proposal_aspect = (
                proposal_width / max(1.0, proposal_height)
            )

            should_refine = bool(
                detail_refine_enabled
                and proposal.get("color_hint") == "blue"
                and refined_parent_count
                    < detail_refine_max_parents
                and proposal_area
                    >= detail_refine_min_parent_area
                and proposal_aspect
                    <= detail_refine_parent_aspect_max
                and can_continue()
                and not target_reached()
            )

            if not should_refine:
                continue

            detail_windows = (
                detect_inner_plate_subproposals(
                    image_bgr,
                    proposal_bbox,
                    max_candidates=(
                        detail_refine_max_windows
                    ),
                )
            )

            if not detail_windows:
                continue

            refined_parent_count += 1

            for detail_index, detail in enumerate(
                detail_windows,
                start=1,
            ):
                if not can_continue() or target_reached():
                    break

                detail_id = (
                    f"{parent_id}:detail_{detail_index}"
                )

                detail_result, detail_latency = (
                    _run_region_recognition(
                        image_bgr,
                        detail["bbox"],
                        stage="color_detail",
                        region_id=detail_id,
                        scale=detail_refine_scale,
                        candidate_min_confidence=(
                            candidate_min_confidence
                        ),
                        max_enhanced_width=(
                            max_enhanced_width
                        ),
                        max_enhanced_height=(
                            max_enhanced_height
                        ),
                        enhancement_mode="standard",
                    )
                )
                inference_calls += 1
                detail_regions += 1
                detail_latency_ms += detail_latency

                for item in detail_result:
                    item["sandbox_color_hint"] = (
                        proposal["color_hint"]
                    )
                    item["sandbox_proposal_score"] = (
                        proposal["score"]
                    )
                    item["sandbox_parent_proposal_id"] = (
                        parent_id
                    )
                    item["sandbox_detail_score"] = (
                        detail["score"]
                    )

                detail_candidates.extend(detail_result)
                candidates.extend(detail_result)

                detail_standard_reliable = any(
                    is_reliable_plate_candidate(
                        item,
                        min_confidence=(
                            detail_strong_retry_skip_confidence
                        ),
                    )
                    for item in detail_result
                )

                if (
                    detail_refine_strong_retry
                    and not detail_standard_reliable
                    and can_continue()
                    and not target_reached()
                ):
                    strong_result, strong_latency = (
                        _run_region_recognition(
                            image_bgr,
                            detail["bbox"],
                            stage="color_detail",
                            region_id=detail_id,
                            scale=detail_refine_scale,
                            candidate_min_confidence=(
                                candidate_min_confidence
                            ),
                            max_enhanced_width=(
                                max_enhanced_width
                            ),
                            max_enhanced_height=(
                                max_enhanced_height
                            ),
                            enhancement_mode="strong",
                        )
                    )
                    inference_calls += 1
                    detail_regions += 1
                    detail_latency_ms += strong_latency

                    for item in strong_result:
                        item["sandbox_color_hint"] = (
                            proposal["color_hint"]
                        )
                        item["sandbox_proposal_score"] = (
                            proposal["score"]
                        )
                        item[
                            "sandbox_parent_proposal_id"
                        ] = parent_id
                        item["sandbox_detail_score"] = (
                            detail["score"]
                        )

                    detail_candidates.extend(
                        strong_result
                    )
                    candidates.extend(strong_result)

                detail_windows_debug.append({
                    "parent_id": parent_id,
                    "bbox": detail["bbox"],
                    "character_band_bbox": detail[
                        "character_band_bbox"
                    ],
                    "score": detail["score"],
                })

        stage_stats.append({
            "stage": "color_proposal",
            "regions": proposal_regions,
            "proposals_found": len(
                detected_color_proposals
            ),
            "proposals_processed": len(color_proposals),
            "proposals_skipped_recognized": (
                skipped_recognized_proposals
            ),
            "candidates": len(proposal_candidates),
            "latency_ms": round(proposal_latency_ms, 2),
            "proposal_boxes": [
                item["bbox"]
                for item in detected_color_proposals
            ],
            "processed_proposal_boxes": [
                item["bbox"]
                for item in color_proposals
            ],
        })
        stage_stats.append({
            "stage": "color_detail",
            "regions": detail_regions,
            "refined_parents": refined_parent_count,
            "detail_windows": len(
                detail_windows_debug
            ),
            "candidates": len(detail_candidates),
            "latency_ms": round(detail_latency_ms, 2),
            "windows": detail_windows_debug,
        })

    allow_general_fallback = not (
        focus_roi_enabled
        and skip_general_fallback_when_focus_enabled
    )

    if (
        allow_general_fallback
        and bool(settings.get("whole_frame_first", True))
        and can_continue()
        and not target_reached()
    ):
        frame_started = time.perf_counter()
        whole_result = recognize_plate_frame(
            image_bgr,
            output_path=None,
            min_confidence=0.0,
            draw_annotation=False,
        )
        inference_calls += 1
        whole_candidates = []
        for plate in whole_result.get("plates", []) or []:
            confidence = float(plate.get("confidence", 0) or 0)
            number = _normalize_plate_text(plate.get("plate_number"))
            if number and confidence >= candidate_min_confidence:
                item = dict(plate)
                item["plate_number"] = number
                item["sandbox_stage"] = "whole_frame"
                item["sandbox_region_id"] = "whole_frame"
                item["sandbox_scale"] = 1.0
                item["sandbox_source_bbox"] = [
                    0,
                    0,
                    image_width,
                    image_height,
                ]
                whole_candidates.append(item)
        candidates.extend(whole_candidates)
        stage_stats.append({
            "stage": "whole_frame",
            "regions": 1,
            "candidates": len(whole_candidates),
            "latency_ms": round(
                (time.perf_counter() - frame_started) * 1000.0,
                2,
            ),
        })

    roi_scale = float(settings.get("roi_scale", 1.8))

    if allow_general_fallback:
        for roi in resolved_rois:
            if not can_continue() or target_reached():
                break
            region_candidates, latency_ms = _run_region_recognition(
                image_bgr,
                roi["bbox"],
                stage="roi",
                region_id=roi["id"],
                scale=roi_scale,
                candidate_min_confidence=candidate_min_confidence,
                max_enhanced_width=max_enhanced_width,
                max_enhanced_height=max_enhanced_height,
            )
            inference_calls += 1
            candidates.extend(region_candidates)
            stage_stats.append({
                "stage": "roi",
                "region_id": roi["id"],
                "candidates": len(region_candidates),
                "latency_ms": round(latency_ms, 2),
            })
    else:
        stage_stats.append({
            "stage": "general_fallback",
            "skipped": True,
            "reason": (
                "focus_roi_enabled_and_fixed_camera"
            ),
            "regions": 0,
            "candidates": 0,
            "latency_ms": 0.0,
        })

    should_slice = bool(
        allow_general_fallback
        and enable_auto_slicing
        and settings.get("auto_slice", True)
        and can_continue()
        and (
            multi_plate_scan
            or not candidates
        )
        and not target_reached()
    )

    if should_slice:
        tile_scale = float(settings.get("tile_scale", 2.2))
        remaining_tiles = max(0, int(settings.get("max_tiles", 6)))
        tile_candidates: list[dict] = []
        tile_latency_ms = 0.0
        tile_count = 0

        for roi in resolved_rois:
            if remaining_tiles <= 0 or not can_continue():
                break

            tiles = generate_priority_tiles(
                roi["bbox"],
                tile_width=int(settings.get("tile_width", 720)),
                tile_height=int(settings.get("tile_height", 480)),
                overlap=float(settings.get("tile_overlap", 0.22)),
                max_tiles=remaining_tiles,
            )

            # ROI 本身已经识别过；只有真正比 ROI 小的窗口才有切片价值。
            filtered_tiles = [
                tile
                for tile in tiles
                if tile["bbox"] != roi["bbox"]
            ]

            for tile in filtered_tiles:
                if remaining_tiles <= 0 or not can_continue():
                    break
                region_candidates, latency_ms = _run_region_recognition(
                    image_bgr,
                    tile["bbox"],
                    stage="tile",
                    region_id=f"{roi['id']}:{tile['id']}",
                    scale=tile_scale,
                    candidate_min_confidence=candidate_min_confidence,
                    max_enhanced_width=max_enhanced_width,
                    max_enhanced_height=max_enhanced_height,
                )
                inference_calls += 1
                remaining_tiles -= 1
                tile_count += 1
                tile_latency_ms += latency_ms
                tile_candidates.extend(region_candidates)
                candidates.extend(region_candidates)

                if target_reached():
                    break

            if target_reached():
                break

        stage_stats.append({
            "stage": "auto_slice",
            "regions": tile_count,
            "candidates": len(tile_candidates),
            "latency_ms": round(tile_latency_ms, 2),
        })

    consolidated_candidates = (
        _consolidate_parent_ocr_variants(
            candidates
        )
    )
    deduplicated_plates = (
        _deduplicate_frame_candidates(
            consolidated_candidates
        )
    )

    if frame_output_require_valid_format:
        plates = [
            item
            for item in deduplicated_plates
            if (
                is_valid_plate_number(
                    item.get("plate_number")
                )
                and float(
                    item.get("confidence", 0)
                    or 0
                )
                >= frame_output_min_confidence
            )
        ]
    else:
        plates = deduplicated_plates

    rejected_frame_candidates = (
        len(deduplicated_plates)
        - len(plates)
    )

    if draw_annotation or output_path is not None:
        draw_plate_annotations(
            image_bgr=image_bgr,
            plates=plates,
            output_path=output_path,
        )

    latency_ms = round(
        (time.perf_counter() - started) * 1000.0,
        2,
    )

    return {
        "model": "HyperLPR3 + Sandbox ROI/Tiled Enhancement",
        "plate_count": len(plates),
        "plates": plates,
        "latency_ms": latency_ms,
        "image_width": image_width,
        "image_height": image_height,
        "multi_plate_supported": True,
        "sandbox_plate_mode": True,
        "sandbox_camera_id": camera_id,
        "sandbox_strategy": (
            "整帧快速尝试 + 固定ROI放大增强 + 自动重叠切片 + 坐标回映"
        ),
        "sandbox_auto_slicing_used": should_slice,
        "sandbox_inference_calls": inference_calls,
        "sandbox_raw_candidate_count": len(candidates),
        "sandbox_deduplicated_candidate_count": len(
            deduplicated_plates
        ),
        "sandbox_rejected_invalid_candidate_count": (
            rejected_frame_candidates
        ),
        "sandbox_reliable_target_candidate_count": (
            current_unique_plate_count()
        ),
        "sandbox_target_count_min_confidence": (
            target_count_min_confidence
        ),
        "sandbox_general_fallback_skipped": (
            not allow_general_fallback
        ),
        "sandbox_consolidated_candidate_count": len(
            consolidated_candidates
        ),
        "sandbox_detail_refine_enabled": (
            detail_refine_enabled
        ),
        "sandbox_focus_roi_enabled": (
            focus_roi_enabled
        ),
        "sandbox_focus_roi_count": len(
            resolved_focus_rois
        ),
        "sandbox_focus_roi_boxes": [
            {
                "id": item["id"],
                "bbox": item["bbox"],
                "scale": item["scale"],
            }
            for item in resolved_focus_rois
        ],
        "sandbox_stage_stats": stage_stats,
        "sandbox_roi_count": len(resolved_rois),
        "sandbox_time_budget_seconds": time_budget,
        "sandbox_multi_plate_scan": multi_plate_scan,
        "sandbox_color_proposal_enabled": color_proposal_enabled,
        "sandbox_color_proposals_found": len(
            detected_color_proposals
        ),
        "sandbox_color_proposals_processed": len(
            color_proposals
        ),
        "sandbox_color_proposals_skipped_recognized": (
            skipped_recognized_proposals
        ),
        "sandbox_color_proposal_boxes": [
            item["bbox"]
            for item in detected_color_proposals
        ],
        "sandbox_target_plate_count": target_plate_count,
        "sandbox_target_reached": target_reached(),
    }


def select_sandbox_vote_frames(
    sampled_frames: list[dict],
    max_frames: int,
) -> list[dict]:
    if len(sampled_frames) <= max_frames:
        return list(sampled_frames)
    max_frames = max(2, int(max_frames))
    indices = np.linspace(
        0,
        len(sampled_frames) - 1,
        num=max_frames,
        dtype=int,
    )
    return [sampled_frames[int(index)] for index in sorted(set(indices.tolist()))]


def _character_vote(members: list[dict]) -> str:
    plausible = [
        member
        for member in members
        if len(member["plate_number"]) in {7, 8}
    ]
    if not plausible:
        return max(
            members,
            key=lambda item: float(item.get("confidence", 0) or 0),
        )["plate_number"]

    length_scores: dict[int, float] = defaultdict(float)
    for member in plausible:
        length_scores[len(member["plate_number"])] += float(
            member.get("confidence", 0) or 0
        )
    target_length = max(length_scores, key=length_scores.get)
    same_length = [
        member
        for member in plausible
        if len(member["plate_number"]) == target_length
    ]

    voted_chars: list[str] = []
    for position in range(target_length):
        char_scores: dict[str, float] = defaultdict(float)
        for member in same_length:
            char_scores[member["plate_number"][position]] += max(
                0.05,
                float(member.get("confidence", 0) or 0),
            )
        voted_chars.append(max(char_scores, key=char_scores.get))
    return "".join(voted_chars)


def _bbox_center_distance_ratio(
    box_a: list[int] | tuple[int, int, int, int],
    box_b: list[int] | tuple[int, int, int, int],
) -> float:
    ax1, ay1, ax2, ay2 = [float(value) for value in box_a]
    bx1, by1, bx2, by2 = [float(value) for value in box_b]

    center_a_x = (ax1 + ax2) / 2.0
    center_a_y = (ay1 + ay2) / 2.0
    center_b_x = (bx1 + bx2) / 2.0
    center_b_y = (by1 + by2) / 2.0

    distance = math.sqrt(
        (center_a_x - center_b_x) ** 2
        + (center_a_y - center_b_y) ** 2
    )
    reference = max(
        24.0,
        (ax2 - ax1),
        (ay2 - ay1) * 2.5,
        (bx2 - bx1),
        (by2 - by1) * 2.5,
    )
    return distance / reference


def _same_physical_plate_candidate(
    left: dict,
    right: dict,
    *,
    text_similarity: float,
    spatial_similarity_threshold: float,
) -> bool:
    left_box = left.get("bbox") or [0, 0, 0, 0]
    right_box = right.get("bbox") or [0, 0, 0, 0]

    overlap = _bbox_iou(left_box, right_box)
    distance_ratio = _bbox_center_distance_ratio(
        left_box,
        right_box,
    )

    # 同一车辆在短时间抽样帧中的车牌框通常会重叠或保持邻近。
    # 只有文字仍具备一定相似度时才按空间合并，避免把三辆不同车辆合成一组。
    return bool(
        text_similarity >= spatial_similarity_threshold
        and (
            overlap >= 0.10
            or distance_ratio <= 1.15
        )
    )


def _cluster_candidates(
    candidates: list[dict],
    similarity_threshold: float,
    spatial_similarity_threshold: float = 0.35,
) -> list[list[dict]]:
    clusters: list[list[dict]] = []

    for candidate in sorted(
        candidates,
        key=lambda item: float(item.get("confidence", 0) or 0),
        reverse=True,
    ):
        best_cluster: list[dict] | None = None
        best_similarity = 0.0

        for cluster in clusters:
            cluster_similarity = 0.0
            spatial_match = False

            for member in cluster:
                similarity = plate_text_similarity(
                    candidate["plate_number"],
                    member["plate_number"],
                )
                cluster_similarity = max(
                    cluster_similarity,
                    similarity,
                )
                if _same_physical_plate_candidate(
                    candidate,
                    member,
                    text_similarity=similarity,
                    spatial_similarity_threshold=spatial_similarity_threshold,
                ):
                    spatial_match = True

            if (
                cluster_similarity > best_similarity
                or (
                    spatial_match
                    and cluster_similarity >= spatial_similarity_threshold
                )
            ):
                best_similarity = cluster_similarity
                best_cluster = cluster

        should_merge = bool(
            best_cluster is not None
            and (
                best_similarity >= similarity_threshold
                or any(
                    _same_physical_plate_candidate(
                        candidate,
                        member,
                        text_similarity=plate_text_similarity(
                            candidate["plate_number"],
                            member["plate_number"],
                        ),
                        spatial_similarity_threshold=spatial_similarity_threshold,
                    )
                    for member in best_cluster
                )
            )
        )

        if should_merge:
            best_cluster.append(candidate)
        else:
            clusters.append([candidate])

    return clusters


def aggregate_sandbox_plate_frame_results(
    frame_results: list[dict],
    *,
    source_id: str,
    source_url: str = "",
) -> dict:
    """沙盘视频专用多帧字符投票，不修改普通视频聚合阈值。"""
    if not frame_results:
        raise RuntimeError("沙盘车牌多帧投票没有输入结果")

    settings = get_source_sandbox_settings(source_id, source_url)
    min_confidence = float(settings.get("vote_min_confidence", 0.58))
    min_appearances = max(2, int(settings.get("vote_min_appearances", 2)))
    high_confidence = float(
        settings.get("single_frame_high_confidence", 0.96)
    )
    similarity_threshold = float(
        settings.get("cluster_similarity", 0.70)
    )
    spatial_similarity_threshold = float(
        settings.get("spatial_cluster_similarity", 0.35)
    )

    candidates: list[dict] = []
    raw_candidate_count = 0
    aggregate_stage_stats: list[dict] = []
    max_color_proposals_found = 0
    detail_refine_executed = False
    focus_roi_executed = False
    total_inference_calls = 0
    total_consolidated_candidates = 0

    for item in frame_results:
        frame_index = item.get("frame_index")
        frame_payload = item.get("result") or {}

        max_color_proposals_found = max(
            max_color_proposals_found,
            int(
                frame_payload.get(
                    "sandbox_color_proposals_found",
                    0,
                )
                or 0
            ),
        )
        detail_refine_executed = bool(
            detail_refine_executed
            or frame_payload.get(
                "sandbox_detail_refine_enabled",
                False,
            )
        )
        focus_roi_executed = bool(
            focus_roi_executed
            or frame_payload.get(
                "sandbox_focus_roi_enabled",
                False,
            )
        )
        total_inference_calls += int(
            frame_payload.get(
                "sandbox_inference_calls",
                0,
            )
            or 0
        )
        total_consolidated_candidates += int(
            frame_payload.get(
                "sandbox_consolidated_candidate_count",
                0,
            )
            or 0
        )

        for stage in (
            frame_payload.get(
                "sandbox_stage_stats"
            )
            or []
        ):
            row = dict(stage)
            row["frame_index"] = frame_index
            aggregate_stage_stats.append(row)

        for plate in frame_payload.get("plates", []) or []:
            raw_candidate_count += 1
            number = _normalize_plate_text(plate.get("plate_number"))
            confidence = float(plate.get("confidence", 0) or 0)
            if not number or confidence < min_confidence:
                continue
            if not is_valid_plate_number(number):
                continue
            candidate = dict(plate)
            candidate["plate_number"] = number
            candidate["confidence"] = confidence
            candidate["frame_index"] = frame_index
            candidate["image_url"] = item.get("image_url", "")
            candidate["output_image_url"] = item.get("output_image_url", "")
            candidates.append(candidate)

    clusters = _cluster_candidates(
        candidates,
        similarity_threshold,
        spatial_similarity_threshold,
    )
    stable_plates: list[dict] = []

    for cluster in clusters:
        unique_frames = sorted({
            int(member["frame_index"])
            for member in cluster
            if member.get("frame_index") is not None
        })
        best_member = max(
            cluster,
            key=lambda item: float(item.get("confidence", 0) or 0),
        )
        max_confidence = float(best_member.get("confidence", 0) or 0)

        if (
            len(unique_frames) < min_appearances
            and max_confidence < high_confidence
        ):
            continue

        voted_number = _character_vote(cluster)
        if not is_valid_plate_number(voted_number):
            continue

        color_scores: dict[str, float] = defaultdict(float)
        variant_scores: dict[str, dict] = {}

        for member in cluster:
            confidence = float(member.get("confidence", 0) or 0)
            color = str(member.get("plate_color") or "未知颜色")
            color_scores[color] += max(0.05, confidence)
            variant = variant_scores.setdefault(
                member["plate_number"],
                {
                    "plate_number": member["plate_number"],
                    "appear_frames": set(),
                    "score": 0.0,
                    "max_confidence": 0.0,
                },
            )
            if member.get("frame_index") is not None:
                variant["appear_frames"].add(int(member["frame_index"]))
            variant["score"] += confidence
            variant["max_confidence"] = max(
                variant["max_confidence"],
                confidence,
            )

        plate_color = (
            max(color_scores, key=color_scores.get)
            if color_scores
            else str(best_member.get("plate_color") or "未知颜色")
        )
        weighted_average = sum(
            float(member.get("confidence", 0) or 0)
            for member in cluster
        ) / max(1, len(cluster))

        stable = dict(best_member)
        stable["raw_plate_number"] = best_member.get("plate_number")
        stable["plate_number"] = voted_number
        stable["plate_color"] = plate_color
        stable["confidence"] = round(max_confidence, 4)
        stable["average_confidence"] = round(weighted_average, 4)
        stable["appear_count"] = len(unique_frames)
        stable["stability_status"] = (
            "multi_frame_confirmed"
            if len(unique_frames) >= min_appearances
            else "single_frame_high_confidence"
        )
        stable["frame_indices"] = unique_frames
        stable["best_frame_index"] = best_member.get("frame_index")
        stable["best_image_url"] = best_member.get("image_url", "")
        stable["best_output_image_url"] = best_member.get(
            "output_image_url",
            "",
        )
        stable["vote_method"] = "similarity_cluster + character_weighted_vote"
        stable["candidate_variants"] = sorted(
            [
                {
                    "plate_number": value["plate_number"],
                    "appear_count": len(value["appear_frames"]),
                    "confidence": round(value["max_confidence"], 4),
                    "vote_score": round(value["score"], 4),
                }
                for value in variant_scores.values()
            ],
            key=lambda item: (
                item["appear_count"],
                item["vote_score"],
                item["confidence"],
            ),
            reverse=True,
        )
        stable.pop("frame_index", None)
        stable.pop("image_url", None)
        stable.pop("output_image_url", None)
        stable_plates.append(stable)

    stable_plates.sort(
        key=lambda item: (
            int(item.get("appear_count", 0)),
            float(item.get("average_confidence", 0) or 0),
            float(item.get("confidence", 0) or 0),
        ),
        reverse=True,
    )

    def frame_score(item: dict) -> tuple[int, float]:
        frame_plates = (item.get("result") or {}).get("plates", []) or []
        matched = 0
        confidence_sum = 0.0
        for plate in frame_plates:
            number = _normalize_plate_text(plate.get("plate_number"))
            confidence = float(plate.get("confidence", 0) or 0)
            for stable in stable_plates:
                if plate_text_similarity(
                    number,
                    stable.get("plate_number", ""),
                ) >= similarity_threshold:
                    matched += 1
                    confidence_sum += confidence
                    break
        return matched, confidence_sum

    best = (
        max(frame_results, key=frame_score)
        if stable_plates
        else max(
            frame_results,
            key=lambda item: float(item.get("confidence", 0) or 0),
        )
    )

    best_frame_plates: list[dict] = []
    for plate in (best.get("result") or {}).get("plates", []) or []:
        number = _normalize_plate_text(plate.get("plate_number"))
        for stable in stable_plates:
            similarity = plate_text_similarity(
                number,
                stable.get("plate_number", ""),
            )
            if similarity >= similarity_threshold:
                evidence = dict(plate)
                evidence["raw_plate_number"] = number
                evidence["plate_number"] = stable["plate_number"]
                evidence["plate_color"] = stable.get(
                    "plate_color",
                    evidence.get("plate_color", "未知颜色"),
                )
                evidence["appear_count"] = stable.get("appear_count", 1)
                evidence["aggregation_similarity"] = round(similarity, 4)
                evidence["evidence_scope"] = "sandbox_best_frame"
                best_frame_plates.append(evidence)
                break

    compact_results = []
    for item in frame_results:
        compact_results.append({
            "frame_index": item.get("frame_index"),
            "captured_at": item.get("captured_at", ""),
            "image_url": item.get("image_url", ""),
            "output_image_url": item.get("output_image_url", ""),
            "confidence": item.get("confidence", 0),
            "latency_ms": item.get("latency_ms", 0),
            "plates": [
                {
                    "plate_number": plate.get("plate_number", ""),
                    "plate_color": plate.get("plate_color", ""),
                    "confidence": plate.get("confidence", 0),
                    "bbox": plate.get("bbox"),
                    "sandbox_stage": plate.get("sandbox_stage", ""),
                    "sandbox_region_id": plate.get(
                        "sandbox_region_id",
                        "",
                    ),
                    "sandbox_ocr_variant": plate.get(
                        "sandbox_ocr_variant",
                        "",
                    ),
                }
                for plate in (item.get("result") or {}).get("plates", []) or []
            ],
        })

    best_confidence = max(
        [float(item.get("confidence", 0) or 0) for item in stable_plates],
        default=0.0,
    )
    final_result = {
        "model": "HyperLPR3 + Sandbox ROI/Tiled + Multi-frame Vote",
        "stream_strategy": (
            "沙盘专用：固定ROI + 自动重叠切片 + 多尺度增强 + 多帧字符加权投票"
        ),
        "sandbox_plate_mode": True,
        "sandbox_camera_id": extract_sandbox_camera_id(
            source_id=source_id,
            source_url=source_url,
        ),
        "plates": stable_plates,
        "stable_plates": stable_plates,
        "aggregated_plates": stable_plates,
        "video_plates": stable_plates,
        "best_frame_plates": best_frame_plates,
        "plate_count": len(stable_plates),
        "best_plate_text": (
            stable_plates[0].get("plate_number", "")
            if stable_plates
            else ""
        ),
        "best_confidence": round(best_confidence, 4),
        "best_frame_index": best.get("frame_index"),
        "sampled_frames": len(frame_results),
        "raw_candidate_count": raw_candidate_count,
        "accepted_candidate_count": len(candidates),
        "stable_plate_count": len(stable_plates),
        "vote_min_appearances": min_appearances,
        "vote_min_confidence": min_confidence,
        "single_frame_high_confidence": high_confidence,
        "cluster_similarity": similarity_threshold,
        "spatial_cluster_similarity": spatial_similarity_threshold,
        "cluster_count": len(clusters),
        "sandbox_color_proposals_found": (
            max_color_proposals_found
        ),
        "sandbox_detail_refine_enabled": (
            detail_refine_executed
        ),
        "sandbox_focus_roi_enabled": (
            focus_roi_executed
        ),
        "sandbox_inference_calls": (
            total_inference_calls
        ),
        "sandbox_consolidated_candidate_count": (
            total_consolidated_candidates
        ),
        "sandbox_stage_stats": (
            aggregate_stage_stats
        ),
        "frame_results": compact_results,
        "ordinary_plate_pipeline_unchanged": True,
    }

    return {
        "best": best,
        "final_result": final_result,
    }
