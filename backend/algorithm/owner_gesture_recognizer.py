from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


try:
    import mediapipe as mp
except ImportError:
    mp = None


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


def draw_chinese_label(image_bgr: np.ndarray, text: str, x: int, y: int):
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil_image)
    font = get_chinese_font(28)

    x = max(0, int(x))
    y = max(0, int(y))

    text_bbox = draw.textbbox((x, y), text, font=font)
    bg_x1, bg_y1, bg_x2, bg_y2 = text_bbox

    draw.rectangle(
        [bg_x1 - 6, bg_y1 - 6, bg_x2 + 6, bg_y2 + 6],
        fill=(0, 120, 220),
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)


def landmark_to_dict(landmark: Any, index: int, image_width: int, image_height: int):
    return {
        "index": index,
        "x": round(float(landmark.x), 4),
        "y": round(float(landmark.y), 4),
        "z": round(float(landmark.z), 4),
        "pixel_x": int(landmark.x * image_width),
        "pixel_y": int(landmark.y * image_height),
    }


def is_finger_extended(points: list[dict], tip_id: int, pip_id: int) -> bool:
    """
    图像坐标中 y 越小越靠上。
    对食指/中指/无名指/小指，指尖在 PIP 关节上方，可近似认为伸直。
    """
    return points[tip_id]["y"] < points[pip_id]["y"] - 0.025


def classify_static_hand_gesture(points: list[dict], handedness_score: float) -> dict:
    """
    基于 MediaPipe 21 个手部关键点做简单静态手势分类。

    当前支持：
    - open_palm：手掌张开
    - fist：握拳
    - thumb_up：拇指向上
    - thumb_down：拇指向下
    - unknown：未识别

    左右滑动、挥手、画圈属于时序动作，下一步用连续帧实现。
    """
    wrist = points[0]
    thumb_tip = points[4]

    index_extended = is_finger_extended(points, 8, 6)
    middle_extended = is_finger_extended(points, 12, 10)
    ring_extended = is_finger_extended(points, 16, 14)
    pinky_extended = is_finger_extended(points, 20, 18)

    non_thumb_extended_count = sum([
        index_extended,
        middle_extended,
        ring_extended,
        pinky_extended,
    ])

    thumb_up_pose = (
        thumb_tip["y"] < wrist["y"] - 0.12
        and non_thumb_extended_count <= 1
    )

    thumb_down_pose = (
        thumb_tip["y"] > wrist["y"] + 0.12
        and non_thumb_extended_count <= 1
    )

    if non_thumb_extended_count >= 3:
        gesture = "open_palm"
        gesture_name = "手掌张开"
        confidence = 0.88

    elif thumb_up_pose:
        gesture = "thumb_up"
        gesture_name = "拇指向上"
        confidence = 0.86

    elif thumb_down_pose:
        gesture = "thumb_down"
        gesture_name = "拇指向下"
        confidence = 0.86

    elif non_thumb_extended_count <= 1:
        gesture = "fist"
        gesture_name = "握拳"
        confidence = 0.84

    else:
        gesture = "unknown"
        gesture_name = "未知手势"
        confidence = 0.45

    confidence = round(min(confidence, float(handedness_score)), 4)

    return {
        "gesture": gesture,
        "gesture_name": gesture_name,
        "confidence": confidence,
        "finger_features": {
            "index_extended": index_extended,
            "middle_extended": middle_extended,
            "ring_extended": ring_extended,
            "pinky_extended": pinky_extended,
            "non_thumb_extended_count": non_thumb_extended_count,
        },
    }


def recognize_owner_gesture_image(input_path: Path, output_path: Path) -> dict:
    """
    车主手势真实 AI 识别入口：
    1. OpenCV 读取图片
    2. MediaPipe Hands 检测手部 21 个关键点
    3. 基于关键点规则分类静态手势
    4. 绘制手部骨架和识别标签
    5. 返回结构化识别结果
    """
    if mp is None:
        raise RuntimeError("未安装 mediapipe。请先执行：pip install mediapipe")

    image_bgr = cv2.imread(str(input_path))

    if image_bgr is None:
        raise RuntimeError("图片读取失败，请检查图片格式是否正确")

    image_height, image_width = image_bgr.shape[:2]
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    mp_styles = mp.solutions.drawing_styles

    with mp_hands.Hands(
        static_image_mode=True,
        max_num_hands=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as hands:
        results = hands.process(image_rgb)

    if not results.multi_hand_landmarks:
        output_image = draw_chinese_label(
            image_bgr.copy(),
            "未检测到手部",
            30,
            30,
        )

        cv2.imwrite(str(output_path), output_image)

        return {
            "model": "MediaPipe Hands",
            "gesture": "unknown",
            "gesture_name": "未检测到手部",
            "confidence": 0.0,
            "handedness": "",
            "landmarks": [],
            "finger_features": {},
        }

    hand_landmarks = results.multi_hand_landmarks[0]

    if results.multi_handedness:
        handedness_item = results.multi_handedness[0].classification[0]
        handedness_label = handedness_item.label
        handedness_score = handedness_item.score
    else:
        handedness_label = ""
        handedness_score = 0.8

    landmarks = [
        landmark_to_dict(landmark, index, image_width, image_height)
        for index, landmark in enumerate(hand_landmarks.landmark)
    ]

    classify_result = classify_static_hand_gesture(
        points=landmarks,
        handedness_score=handedness_score,
    )

    output_image = image_bgr.copy()

    mp_drawing.draw_landmarks(
        output_image,
        hand_landmarks,
        mp_hands.HAND_CONNECTIONS,
        mp_styles.get_default_hand_landmarks_style(),
        mp_styles.get_default_hand_connections_style(),
    )

    label = f"{classify_result['gesture_name']} {classify_result['confidence']:.2f}"
    output_image = draw_chinese_label(output_image, label, 30, 30)

    success = cv2.imwrite(str(output_path), output_image)

    if not success:
        raise RuntimeError("手势识别标注图保存失败")

    return {
        "model": "MediaPipe Hands",
        "gesture": classify_result["gesture"],
        "gesture_name": classify_result["gesture_name"],
        "confidence": classify_result["confidence"],
        "handedness": handedness_label,
        "landmarks": landmarks,
        "finger_features": classify_result["finger_features"],
    }