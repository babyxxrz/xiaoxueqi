from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


_lpr3_module = None
_lpr_catcher = None


def get_lpr_catcher():
    """
    延迟加载 HyperLPR3 模型。

    不能在文件顶部直接 import hyperlpr3：
    1. HyperLPR3 首次 import 时可能会下载模型文件。
    2. uvicorn --reload 会启动多个进程。
    3. Windows 下多个进程同时访问模型 zip 容易触发 WinError 32 文件占用错误。
    """
    global _lpr3_module
    global _lpr_catcher

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


def normalize_bbox(raw_box: Any, image_width: int, image_height: int) -> list[int]:
    """
    兼容不同车牌模型返回的 bbox 格式：
    1. [x1, y1, x2, y2]
    2. [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    3. [x1, y1, x2, y2, x3, y3, x4, y4]
    """
    if raw_box is None:
        return [0, 0, image_width - 1, image_height - 1]

    try:
        arr = np.array(raw_box).astype(float).reshape(-1)

        if arr.size == 4:
            x1, y1, x2, y2 = arr.tolist()
        elif arr.size >= 8:
            xs = arr[0::2]
            ys = arr[1::2]
            x1, y1, x2, y2 = xs.min(), ys.min(), xs.max(), ys.max()
        else:
            return [0, 0, image_width - 1, image_height - 1]

        x1 = max(0, min(int(x1), image_width - 1))
        y1 = max(0, min(int(y1), image_height - 1))
        x2 = max(0, min(int(x2), image_width - 1))
        y2 = max(0, min(int(y2), image_height - 1))

        if x2 <= x1 or y2 <= y1:
            return [0, 0, image_width - 1, image_height - 1]

        return [x1, y1, x2, y2]

    except Exception:
        return [0, 0, image_width - 1, image_height - 1]


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

    if len(plate_number) >= 8:
        return "绿牌"

    return "蓝牌"


def normalize_hyperlpr_result(item: Any, image_width: int, image_height: int) -> dict:
    """
    将 HyperLPR3 返回结果统一整理成前端需要的格式。
    不同版本 HyperLPR3 返回结构可能略有差异，因此这里做兼容处理。
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
            plate_number = str(item[0])
        if len(item) >= 2:
            confidence = safe_float(item[1])

        for part in item[2:]:
            if isinstance(part, (list, tuple, np.ndarray)):
                raw_box = part
            elif isinstance(part, (str, int, float)):
                if plate_type is None:
                    plate_type = part

    plate_number = str(plate_number).strip()
    bbox = normalize_bbox(raw_box, image_width, image_height)
    plate_color = infer_plate_color(plate_number, plate_type)

    return {
        "plate_number": plate_number,
        "plate_color": plate_color,
        "confidence": round(confidence, 4),
        "bbox": bbox,
        "raw_type": str(plate_type) if plate_type is not None else "",
    }


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


def draw_label_with_pil(image_bgr: np.ndarray, text: str, x: int, y: int):
    """
    用 PIL 绘制中文，避免 OpenCV 中文乱码。
    """
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil_image)
    font = get_chinese_font(28)

    y = max(0, y - 36)

    text_bbox = draw.textbbox((x, y), text, font=font)
    bg_x1, bg_y1, bg_x2, bg_y2 = text_bbox

    draw.rectangle(
        [bg_x1 - 4, bg_y1 - 4, bg_x2 + 4, bg_y2 + 4],
        fill=(0, 150, 0),
    )

    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)


def recognize_plate_real(
    input_path: Path,
    output_path: Path,
    force_confidence: float | None = None,
) -> dict:
    """
    真实车牌识别入口：
    1. OpenCV 读取图片
    2. HyperLPR3 检测 + OCR
    3. 画车牌框和识别文字
    4. 返回结构化结果
    """
    image = cv2.imread(str(input_path))

    if image is None:
        raise RuntimeError("图片读取失败，请检查图片格式是否正确")

    height, width = image.shape[:2]

    catcher = get_lpr_catcher()
    raw_results = catcher(image)

    if raw_results is None:
        raw_results = []

    plates = []

    for item in raw_results:
        plate = normalize_hyperlpr_result(item, width, height)

        if not plate["plate_number"]:
            continue

        if force_confidence is not None:
            plate["confidence"] = round(float(force_confidence), 4)

        x1, y1, x2, y2 = plate["bbox"]

        box_color = (0, 255, 0) if plate["confidence"] >= 0.6 else (0, 165, 255)

        cv2.rectangle(
            image,
            (x1, y1),
            (x2, y2),
            box_color,
            3,
        )

        label = f"{plate['plate_number']} {plate['confidence']:.2f}"
        image = draw_label_with_pil(image, label, x1, y1)

        plates.append(plate)

    if not plates:
        image = draw_label_with_pil(
            image,
            "未检测到车牌",
            30,
            60,
        )

    success = cv2.imwrite(str(output_path), image)

    if not success:
        raise RuntimeError("识别标注图保存失败")

    return {
        "model": "HyperLPR3",
        "plates": plates,
    }