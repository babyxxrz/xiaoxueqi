from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import mediapipe as mp
except ImportError:
    mp = None


POSE_LANDMARK_NAMES = {
    0: "nose",
    11: "left_shoulder",
    12: "right_shoulder",
    13: "left_elbow",
    14: "right_elbow",
    15: "left_wrist",
    16: "right_wrist",
    23: "left_hip",
    24: "right_hip",
}


TRAFFIC_COMMANDS = {
    "stop": {
        "gesture_name": "停止信号",
        "traffic_command": "车辆停止通行",
    },
    "straight": {
        "gesture_name": "直行信号",
        "traffic_command": "车辆允许直行",
    },
    "left_turn": {
        "gesture_name": "左转弯信号",
        "traffic_command": "车辆允许左转",
    },
    "left_turn_wait": {
        "gesture_name": "左转弯待转信号",
        "traffic_command": "车辆进入待转区",
    },
    "right_turn": {
        "gesture_name": "右转弯信号",
        "traffic_command": "车辆允许右转",
    },
    "lane_change": {
        "gesture_name": "变道信号",
        "traffic_command": "车辆按指令变道",
    },
    "slow_down": {
        "gesture_name": "减速慢行信号",
        "traffic_command": "车辆减速慢行",
    },
    "pull_over": {
        "gesture_name": "靠边停车信号",
        "traffic_command": "车辆靠边停车",
    },
    "unknown": {
        "gesture_name": "未知手势",
        "traffic_command": "无法确定交通指令",
    },
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
        fill=(220, 120, 0),
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)


def landmark_to_dict(landmark: Any, index: int, image_width: int, image_height: int):
    return {
        "index": index,
        "name": POSE_LANDMARK_NAMES.get(index, f"landmark_{index}"),
        "x": round(float(landmark.x), 4),
        "y": round(float(landmark.y), 4),
        "z": round(float(landmark.z), 4),
        "visibility": round(float(landmark.visibility), 4),
        "pixel_x": int(landmark.x * image_width),
        "pixel_y": int(landmark.y * image_height),
    }


def point_ok(point: dict, min_visibility: float = 0.30) -> bool:
    return point.get("visibility", 0) >= min_visibility


def classify_traffic_gesture(points: dict[int, dict]) -> dict:
    """
    基于 MediaPipe Pose 人体关键点的交警手势规则分类。

    说明：
    1. MediaPipe 的 left/right 是人体解剖学左右，不是图片左右。
    2. 人面向摄像头时，人体 left_arm 往往出现在图像右侧。
    3. 所以这里不再写死 x 方向，而是用“手腕相对肩膀的水平距离”判断手臂是否伸展。
    """
    required_ids = [11, 12, 13, 14, 15, 16, 23, 24]

    if any(index not in points for index in required_ids):
        return {
            "gesture": "unknown",
            "confidence": 0.0,
            "features": {
                "reason": "missing_required_keypoints",
            },
        }

    left_shoulder = points[11]
    right_shoulder = points[12]
    left_elbow = points[13]
    right_elbow = points[14]
    left_wrist = points[15]
    right_wrist = points[16]
    left_hip = points[23]
    right_hip = points[24]

    if any(not point_ok(points[index]) for index in required_ids):
        return {
            "gesture": "unknown",
            "confidence": 0.35,
            "features": {
                "reason": "low_visibility_keypoints",
            },
        }

    shoulder_width = abs(left_shoulder["x"] - right_shoulder["x"])
    shoulder_width = max(shoulder_width, 0.12)

    shoulder_y = (left_shoulder["y"] + right_shoulder["y"]) / 2
    hip_y = (left_hip["y"] + right_hip["y"]) / 2

    left_arm_up = left_wrist["y"] < left_shoulder["y"] - 0.10
    right_arm_up = right_wrist["y"] < right_shoulder["y"] - 0.10

    left_arm_down = left_wrist["y"] > shoulder_y + 0.16
    right_arm_down = right_wrist["y"] > shoulder_y + 0.16

    left_arm_horizontal = (
        abs(left_wrist["x"] - left_shoulder["x"]) > shoulder_width * 0.65
        and abs(left_wrist["y"] - left_shoulder["y"]) < 0.20
    )

    right_arm_horizontal = (
        abs(right_wrist["x"] - right_shoulder["x"]) > shoulder_width * 0.65
        and abs(right_wrist["y"] - right_shoulder["y"]) < 0.20
    )

    left_arm_diagonal_down = (
        abs(left_wrist["x"] - left_shoulder["x"]) > shoulder_width * 0.45
        and shoulder_y + 0.05 < left_wrist["y"] < hip_y + 0.12
    )

    right_arm_diagonal_down = (
        abs(right_wrist["x"] - right_shoulder["x"]) > shoulder_width * 0.45
        and shoulder_y + 0.05 < right_wrist["y"] < hip_y + 0.12
    )

    left_wrist_near_chest = (
        min(left_shoulder["x"], right_shoulder["x"]) - 0.05
        < left_wrist["x"]
        < max(left_shoulder["x"], right_shoulder["x"]) + 0.05
        and abs(left_wrist["y"] - shoulder_y) < 0.24
    )

    right_wrist_near_chest = (
        min(left_shoulder["x"], right_shoulder["x"]) - 0.05
        < right_wrist["x"]
        < max(left_shoulder["x"], right_shoulder["x"]) + 0.05
        and abs(right_wrist["y"] - shoulder_y) < 0.24
    )

    left_arm_to_image_right = left_wrist["x"] > left_shoulder["x"]
    left_arm_to_image_left = left_wrist["x"] < left_shoulder["x"]
    right_arm_to_image_right = right_wrist["x"] > right_shoulder["x"]
    right_arm_to_image_left = right_wrist["x"] < right_shoulder["x"]

    features = {
        "left_arm_up": left_arm_up,
        "right_arm_up": right_arm_up,
        "left_arm_down": left_arm_down,
        "right_arm_down": right_arm_down,
        "left_arm_horizontal": left_arm_horizontal,
        "right_arm_horizontal": right_arm_horizontal,
        "left_arm_diagonal_down": left_arm_diagonal_down,
        "right_arm_diagonal_down": right_arm_diagonal_down,
        "left_wrist_near_chest": left_wrist_near_chest,
        "right_wrist_near_chest": right_wrist_near_chest,
        "left_arm_to_image_right": left_arm_to_image_right,
        "left_arm_to_image_left": left_arm_to_image_left,
        "right_arm_to_image_right": right_arm_to_image_right,
        "right_arm_to_image_left": right_arm_to_image_left,
        "shoulder_width": round(shoulder_width, 4),
    }

    if left_arm_horizontal and right_arm_horizontal:
        gesture = "straight"
        confidence = 0.88

    elif (left_arm_up and right_arm_horizontal) or (right_arm_up and left_arm_horizontal):
        gesture = "pull_over"
        confidence = 0.84

    elif left_arm_up or right_arm_up:
        gesture = "stop"
        confidence = 0.86

    elif left_arm_horizontal and right_wrist_near_chest:
        gesture = "lane_change"
        confidence = 0.81

    elif right_arm_horizontal and left_wrist_near_chest:
        gesture = "lane_change"
        confidence = 0.81

    elif left_arm_horizontal:
        gesture = "left_turn"
        confidence = 0.82

    elif right_arm_horizontal:
        gesture = "right_turn"
        confidence = 0.82

    elif left_arm_diagonal_down and right_arm_down:
        gesture = "left_turn_wait"
        confidence = 0.78

    elif right_arm_diagonal_down or left_arm_diagonal_down:
        gesture = "slow_down"
        confidence = 0.76

    else:
        gesture = "unknown"
        confidence = 0.45

    return {
        "gesture": gesture,
        "confidence": round(confidence, 4),
        "features": features,
    }


def recognize_traffic_gesture_image(input_path: Path, output_path: Path) -> dict:
    """
    交警手势 AI 识别入口：
    1. OpenCV 读取图片
    2. MediaPipe Pose 检测人体 33 个姿态关键点
    3. 根据肩、肘、腕、髋等关键点进行手势分类
    4. 绘制人体骨架和识别标签
    5. 返回结构化识别结果
    """
    if mp is None:
        raise RuntimeError("未安装 mediapipe。请先执行：pip install mediapipe")

    image_bgr = cv2.imread(str(input_path))

    if image_bgr is None:
        raise RuntimeError("图片读取失败，请检查图片格式是否正确")

    image_height, image_width = image_bgr.shape[:2]
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    mp_pose = mp.solutions.pose
    mp_drawing = mp.solutions.drawing_utils
    mp_styles = mp.solutions.drawing_styles

    with mp_pose.Pose(
        static_image_mode=True,
        model_complexity=1,
        enable_segmentation=False,
        min_detection_confidence=0.5,
    ) as pose:
        results = pose.process(image_rgb)

    if not results.pose_landmarks:
        output_image = draw_chinese_label(
            image_bgr.copy(),
            "未检测到人体姿态",
            30,
            30,
        )

        cv2.imwrite(str(output_path), output_image)

        return {
            "model": "MediaPipe Pose",
            "gesture": "unknown",
            "gesture_name": "未检测到人体姿态",
            "traffic_command": "无法确定交通指令",
            "confidence": 0.0,
            "landmarks": [],
            "keypoints": [],
            "features": {},
        }

    landmarks = [
        landmark_to_dict(landmark, index, image_width, image_height)
        for index, landmark in enumerate(results.pose_landmarks.landmark)
    ]

    points = {item["index"]: item for item in landmarks}
    classify_result = classify_traffic_gesture(points)

    gesture = classify_result["gesture"]
    command_info = TRAFFIC_COMMANDS.get(gesture, TRAFFIC_COMMANDS["unknown"])

    keypoints = [
        points[index]
        for index in POSE_LANDMARK_NAMES.keys()
        if index in points
    ]

    output_image = image_bgr.copy()

    mp_drawing.draw_landmarks(
        output_image,
        results.pose_landmarks,
        mp_pose.POSE_CONNECTIONS,
        landmark_drawing_spec=mp_styles.get_default_pose_landmarks_style(),
    )

    label = f"{command_info['gesture_name']} {classify_result['confidence']:.2f}"
    output_image = draw_chinese_label(output_image, label, 30, 30)

    success = cv2.imwrite(str(output_path), output_image)

    if not success:
        raise RuntimeError("交警手势识别标注图保存失败")

    return {
        "model": "MediaPipe Pose",
        "gesture": gesture,
        "gesture_name": command_info["gesture_name"],
        "traffic_command": command_info["traffic_command"],
        "confidence": classify_result["confidence"],
        "landmarks": landmarks,
        "keypoints": keypoints,
        "features": classify_result["features"],
    }