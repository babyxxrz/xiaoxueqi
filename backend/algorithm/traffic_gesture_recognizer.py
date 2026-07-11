from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import math
import statistics
import threading
import time

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
        "gesture_name": "示意车辆靠边停车",
        "traffic_command": "车辆靠边停车",
    },
    "unknown": {
        "gesture_name": "未知手势",
        "traffic_command": "无法确定交通指令",
    },
}


GESTURE_PRIORITY = [
    "straight",
    "pull_over",
    "lane_change",
    "stop",
    "left_turn_wait",
    "slow_down",
    "left_turn",
    "right_turn",
]


_pose_local = threading.local()


def _get_pose(static_image_mode: bool):
    if mp is None:
        raise RuntimeError("未安装 mediapipe。请先执行：pip install mediapipe")

    key = "static_pose" if static_image_mode else "stream_pose"
    pose = getattr(_pose_local, key, None)

    if pose is None:
        pose = mp.solutions.pose.Pose(
            static_image_mode=static_image_mode,
            model_complexity=1,
            smooth_landmarks=not static_image_mode,
            enable_segmentation=False,
            min_detection_confidence=0.50,
            min_tracking_confidence=0.50,
        )
        setattr(_pose_local, key, pose)

    return pose


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


def draw_chinese_label(
    image_bgr: np.ndarray,
    text: str,
    x: int,
    y: int,
    background_rgb: tuple[int, int, int] = (220, 120, 0),
):
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil_image)
    font = get_chinese_font(26)

    x = max(0, int(x))
    y = max(0, int(y))

    text_bbox = draw.textbbox((x, y), text, font=font)
    bg_x1, bg_y1, bg_x2, bg_y2 = text_bbox

    draw.rectangle(
        [bg_x1 - 6, bg_y1 - 6, bg_x2 + 6, bg_y2 + 6],
        fill=background_rgb,
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


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
    return float(point.get("visibility", 0) or 0) >= min_visibility


def _distance(a: dict, b: dict) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _joint_angle(a: dict, b: dict, c: dict) -> float:
    """返回以 b 为顶点的夹角，范围 0~180。"""
    ba = np.array([float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"])])
    bc = np.array([float(c["x"]) - float(b["x"]), float(c["y"]) - float(b["y"])])

    denominator = float(np.linalg.norm(ba) * np.linalg.norm(bc))
    if denominator <= 1e-8:
        return 0.0

    cosine = float(np.clip(np.dot(ba, bc) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _linear_score(value: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0 if value >= high else 0.0
    return _clamp01((float(value) - low) / (high - low))


def _bool_score(value: bool) -> float:
    return 1.0 if value else 0.0


def _mean_visibility(points: dict[int, dict], indices: list[int]) -> float:
    values = [float(points[index].get("visibility", 0) or 0) for index in indices if index in points]
    return sum(values) / len(values) if values else 0.0


def _choose_gesture(scores: dict[str, float], threshold: float = 0.62) -> tuple[str, float]:
    if not scores:
        return "unknown", 0.0

    priority_index = {name: len(GESTURE_PRIORITY) - index for index, name in enumerate(GESTURE_PRIORITY)}
    selected = max(
        scores,
        key=lambda name: (float(scores[name]), priority_index.get(name, 0)),
    )
    selected_score = float(scores[selected])

    if selected_score < threshold:
        return "unknown", selected_score

    return selected, selected_score


def classify_traffic_gesture(points: dict[int, dict]) -> dict:
    """
    单帧规则分类器。

    与旧版本相比：
    - 所有高度和水平距离尽量使用肩宽、躯干高度归一化；
    - 不再只依赖一个 if/else，而是为八类手势计算规则匹配分；
    - 停止信号允许“单臂从身体侧方上举”的标准动作姿态，不再要求手腕必须高出肩部 0.22 个绝对坐标。
    """
    required_ids = [11, 12, 13, 14, 15, 16, 23, 24]

    if any(index not in points for index in required_ids):
        return {
            "gesture": "unknown",
            "confidence": 0.0,
            "features": {"reason": "missing_required_keypoints"},
            "rule_scores": {},
        }

    if any(not point_ok(points[index], 0.22) for index in required_ids):
        return {
            "gesture": "unknown",
            "confidence": 0.30,
            "features": {"reason": "low_visibility_keypoints"},
            "rule_scores": {},
        }

    nose = points.get(0)
    left_shoulder = points[11]
    right_shoulder = points[12]
    left_elbow = points[13]
    right_elbow = points[14]
    left_wrist = points[15]
    right_wrist = points[16]
    left_hip = points[23]
    right_hip = points[24]

    shoulder_width = max(_distance(left_shoulder, right_shoulder), 0.08)
    shoulder_y = (float(left_shoulder["y"]) + float(right_shoulder["y"])) / 2
    hip_y = (float(left_hip["y"]) + float(right_hip["y"])) / 2
    torso_height = max(hip_y - shoulder_y, shoulder_width * 1.25, 0.16)

    left_elbow_angle = _joint_angle(left_shoulder, left_elbow, left_wrist)
    right_elbow_angle = _joint_angle(right_shoulder, right_elbow, right_wrist)

    left_dx_ratio = abs(float(left_wrist["x"]) - float(left_shoulder["x"])) / shoulder_width
    right_dx_ratio = abs(float(right_wrist["x"]) - float(right_shoulder["x"])) / shoulder_width

    # 正数代表手腕高于肩膀，负数代表手腕低于肩膀。
    left_height_ratio = (shoulder_y - float(left_wrist["y"])) / torso_height
    right_height_ratio = (shoulder_y - float(right_wrist["y"])) / torso_height

    left_shoulder_level_ratio = abs(float(left_wrist["y"]) - float(left_shoulder["y"])) / torso_height
    right_shoulder_level_ratio = abs(float(right_wrist["y"]) - float(right_shoulder["y"])) / torso_height

    left_arm_up = left_height_ratio > 0.28
    right_arm_up = right_height_ratio > 0.28
    left_arm_high = left_height_ratio > 0.55
    right_arm_high = right_height_ratio > 0.55

    left_arm_down = left_height_ratio < -0.36
    right_arm_down = right_height_ratio < -0.36

    left_elbow_extended = left_elbow_angle >= 138
    right_elbow_extended = right_elbow_angle >= 138

    left_arm_horizontal = (
        left_dx_ratio > 0.72
        and left_shoulder_level_ratio < 0.36
        and left_elbow_angle > 132
    )
    right_arm_horizontal = (
        right_dx_ratio > 0.72
        and right_shoulder_level_ratio < 0.36
        and right_elbow_angle > 132
    )

    left_arm_vertical_up = (
        left_height_ratio > 0.30
        and left_dx_ratio < 1.15
        and left_elbow_angle > 128
    )
    right_arm_vertical_up = (
        right_height_ratio > 0.30
        and right_dx_ratio < 1.15
        and right_elbow_angle > 128
    )

    left_arm_diagonal_down = (
        left_dx_ratio > 0.42
        and -1.20 < left_height_ratio < -0.08
        and left_elbow_angle > 118
    )
    right_arm_diagonal_down = (
        right_dx_ratio > 0.42
        and -1.20 < right_height_ratio < -0.08
        and right_elbow_angle > 118
    )

    chest_x_min = min(float(left_shoulder["x"]), float(right_shoulder["x"])) - shoulder_width * 0.35
    chest_x_max = max(float(left_shoulder["x"]), float(right_shoulder["x"])) + shoulder_width * 0.35

    left_wrist_near_chest = (
        chest_x_min < float(left_wrist["x"]) < chest_x_max
        and -0.45 < left_height_ratio < 0.42
    )
    right_wrist_near_chest = (
        chest_x_min < float(right_wrist["x"]) < chest_x_max
        and -0.45 < right_height_ratio < 0.42
    )

    left_wrist_near_head = False
    right_wrist_near_head = False
    if nose is not None and point_ok(nose, 0.20):
        nose_y = float(nose["y"])
        left_wrist_near_head = float(left_wrist["y"]) <= nose_y + torso_height * 0.28
        right_wrist_near_head = float(right_wrist["y"]) <= nose_y + torso_height * 0.28

    pose_visibility = _mean_visibility(points, [0, 11, 12, 13, 14, 15, 16, 23, 24])

    # 单臂停止姿态评分。标准视频常从双手下垂开始，再单臂向上举起。
    left_stop_score = (
        0.28 * _linear_score(left_height_ratio, 0.20, 0.80)
        + 0.20 * _linear_score(left_elbow_angle, 122, 170)
        + 0.18 * _bool_score(right_arm_down or right_wrist_near_chest)
        + 0.14 * _linear_score(1.30 - left_dx_ratio, 0.15, 0.85)
        + 0.10 * _bool_score(left_wrist_near_head)
        + 0.10 * pose_visibility
    )
    right_stop_score = (
        0.28 * _linear_score(right_height_ratio, 0.20, 0.80)
        + 0.20 * _linear_score(right_elbow_angle, 122, 170)
        + 0.18 * _bool_score(left_arm_down or left_wrist_near_chest)
        + 0.14 * _linear_score(1.30 - right_dx_ratio, 0.15, 0.85)
        + 0.10 * _bool_score(right_wrist_near_head)
        + 0.10 * pose_visibility
    )

    straight_score = (
        0.36 * _bool_score(left_arm_horizontal)
        + 0.36 * _bool_score(right_arm_horizontal)
        + 0.14 * _linear_score(left_elbow_angle, 135, 175)
        + 0.14 * _linear_score(right_elbow_angle, 135, 175)
    )

    pull_over_score = max(
        0.40 * _bool_score(left_arm_high or left_arm_vertical_up)
        + 0.38 * _bool_score(right_arm_horizontal)
        + 0.12 * _linear_score(left_elbow_angle, 125, 170)
        + 0.10 * pose_visibility,
        0.40 * _bool_score(right_arm_high or right_arm_vertical_up)
        + 0.38 * _bool_score(left_arm_horizontal)
        + 0.12 * _linear_score(right_elbow_angle, 125, 170)
        + 0.10 * pose_visibility,
    )

    # 变道属于动态手势，单帧只能提供弱候选，避免把“直行信号中一臂水平、
    # 另一臂横向摆动经过胸前”的中间姿态误判为变道。
    # 真正的变道判断放在连续帧时序规则中完成。
    lane_change_score = max(
        0.28 * _bool_score(left_arm_horizontal)
        + 0.12 * _bool_score(right_arm_down)
        + 0.10 * _linear_score(left_elbow_angle, 132, 175)
        + 0.10 * pose_visibility,
        0.28 * _bool_score(right_arm_horizontal)
        + 0.12 * _bool_score(left_arm_down)
        + 0.10 * _linear_score(right_elbow_angle, 132, 175)
        + 0.10 * pose_visibility,
    )

    left_turn_score = (
        0.56 * _bool_score(left_arm_horizontal)
        + 0.20 * _bool_score(right_arm_down or right_wrist_near_chest)
        + 0.14 * _linear_score(left_elbow_angle, 132, 175)
        + 0.10 * pose_visibility
    )
    right_turn_score = (
        0.56 * _bool_score(right_arm_horizontal)
        + 0.20 * _bool_score(left_arm_down or left_wrist_near_chest)
        + 0.14 * _linear_score(right_elbow_angle, 132, 175)
        + 0.10 * pose_visibility
    )

    # 左转弯待转：以左臂低位斜向摆动为主，右臂通常保持自然下垂。
    left_turn_wait_score = (
        0.58 * _bool_score(left_arm_diagonal_down)
        + 0.22 * _bool_score(right_arm_down)
        + 0.10 * _linear_score(left_elbow_angle, 118, 170)
        + 0.10 * pose_visibility
    )

    # 减速慢行：以右臂从较高位置向下摆动为主，左臂通常自然下垂。
    slow_down_score = (
        0.52 * _bool_score(right_arm_diagonal_down)
        + 0.18 * _bool_score(left_arm_down or left_wrist_near_chest)
        + 0.20 * _linear_score(abs(right_height_ratio), 0.10, 0.85)
        + 0.10 * pose_visibility
    )

    rule_scores = {
        "stop": round(max(left_stop_score, right_stop_score), 4),
        "straight": round(straight_score, 4),
        "left_turn": round(left_turn_score, 4),
        "left_turn_wait": round(left_turn_wait_score, 4),
        "right_turn": round(right_turn_score, 4),
        "lane_change": round(lane_change_score, 4),
        "slow_down": round(slow_down_score, 4),
        "pull_over": round(pull_over_score, 4),
    }

    # 高特异性姿态优先，避免停止信号被“靠边停车”或转向规则抢占。
    if straight_score >= 0.74:
        gesture, selected_score = "straight", straight_score
    elif pull_over_score >= 0.78:
        gesture, selected_score = "pull_over", pull_over_score
    elif max(left_stop_score, right_stop_score) >= 0.62 and (
        left_arm_vertical_up or right_arm_vertical_up or left_arm_high or right_arm_high
    ):
        gesture, selected_score = "stop", max(left_stop_score, right_stop_score)
    else:
        gesture, selected_score = _choose_gesture(rule_scores, threshold=0.66)

    if gesture == "unknown":
        confidence = min(0.59, max(0.35, selected_score))
    else:
        confidence = min(0.96, 0.58 + selected_score * 0.38)

    features = {
        "left_arm_up": left_arm_up,
        "right_arm_up": right_arm_up,
        "left_arm_high": left_arm_high,
        "right_arm_high": right_arm_high,
        "left_arm_down": left_arm_down,
        "right_arm_down": right_arm_down,
        "left_arm_horizontal": left_arm_horizontal,
        "right_arm_horizontal": right_arm_horizontal,
        "left_arm_vertical_up": left_arm_vertical_up,
        "right_arm_vertical_up": right_arm_vertical_up,
        "left_arm_diagonal_down": left_arm_diagonal_down,
        "right_arm_diagonal_down": right_arm_diagonal_down,
        "left_wrist_near_chest": left_wrist_near_chest,
        "right_wrist_near_chest": right_wrist_near_chest,
        "left_wrist_near_head": left_wrist_near_head,
        "right_wrist_near_head": right_wrist_near_head,
        "left_elbow_angle": round(left_elbow_angle, 2),
        "right_elbow_angle": round(right_elbow_angle, 2),
        "left_elbow_extended": left_elbow_extended,
        "right_elbow_extended": right_elbow_extended,
        "left_height_ratio": round(left_height_ratio, 4),
        "right_height_ratio": round(right_height_ratio, 4),
        "left_dx_ratio": round(left_dx_ratio, 4),
        "right_dx_ratio": round(right_dx_ratio, 4),
        "left_wrist_x": round(float(left_wrist["x"]), 4),
        "right_wrist_x": round(float(right_wrist["x"]), 4),
        "left_wrist_y": round(float(left_wrist["y"]), 4),
        "right_wrist_y": round(float(right_wrist["y"]), 4),
        "left_relative_x_ratio": round((float(left_wrist["x"]) - float(left_shoulder["x"])) / shoulder_width, 4),
        "right_relative_x_ratio": round((float(right_wrist["x"]) - float(right_shoulder["x"])) / shoulder_width, 4),
        "shoulder_width": round(shoulder_width, 4),
        "torso_height": round(torso_height, 4),
        "pose_visibility": round(pose_visibility, 4),
        "stop_side": "left" if left_stop_score >= right_stop_score else "right",
    }

    return {
        "gesture": gesture,
        "confidence": round(confidence, 4),
        "features": features,
        "rule_scores": rule_scores,
        "matched_rule": gesture if gesture != "unknown" else "none",
        "classifier_type": "rule_based_pose_score",
    }


def recognize_traffic_gesture_frame(
    image_bgr: np.ndarray,
    output_path: Path | None = None,
    static_image_mode: bool = True,
) -> dict:
    """对一帧 BGR 图像执行人体姿态检测和交警手势规则分类。"""
    if mp is None:
        raise RuntimeError("未安装 mediapipe。请先执行：pip install mediapipe")

    if image_bgr is None or not isinstance(image_bgr, np.ndarray):
        raise RuntimeError("输入图像为空或格式错误")

    started = time.perf_counter()
    image_height, image_width = image_bgr.shape[:2]
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    pose = _get_pose(static_image_mode=static_image_mode)
    results = pose.process(image_rgb)

    if not results.pose_landmarks:
        output_image = draw_chinese_label(
            image_bgr.copy(),
            "未检测到人体姿态",
            30,
            30,
            background_rgb=(180, 90, 0),
        )

        if output_path is not None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(output_path), output_image):
                raise RuntimeError("交警手势识别标注图保存失败")

        return {
            "model": "MediaPipe Pose",
            "gesture": "unknown",
            "gesture_name": "未检测到人体姿态",
            "traffic_command": "无法确定交通指令",
            "confidence": 0.0,
            "landmarks": [],
            "keypoints": [],
            "features": {"reason": "pose_not_detected"},
            "rule_scores": {},
            "matched_rule": "none",
            "classifier_type": "rule_based_pose_score",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    landmarks = [
        landmark_to_dict(landmark, index, image_width, image_height)
        for index, landmark in enumerate(results.pose_landmarks.landmark)
    ]
    points = {item["index"]: item for item in landmarks}
    classify_result = classify_traffic_gesture(points)

    gesture = classify_result["gesture"]
    command_info = TRAFFIC_COMMANDS.get(gesture, TRAFFIC_COMMANDS["unknown"])
    keypoints = [points[index] for index in POSE_LANDMARK_NAMES if index in points]

    output_image = image_bgr.copy()
    mp.solutions.drawing_utils.draw_landmarks(
        output_image,
        results.pose_landmarks,
        mp.solutions.pose.POSE_CONNECTIONS,
        landmark_drawing_spec=mp.solutions.drawing_styles.get_default_pose_landmarks_style(),
    )

    label = f"{command_info['gesture_name']} {classify_result['confidence']:.2f}"
    output_image = draw_chinese_label(output_image, label, 30, 30)

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), output_image):
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
        "rule_scores": classify_result["rule_scores"],
        "matched_rule": classify_result["matched_rule"],
        "classifier_type": classify_result["classifier_type"],
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def recognize_traffic_gesture_image(input_path: Path, output_path: Path) -> dict:
    image_bgr = cv2.imread(str(input_path))

    if image_bgr is None:
        raise RuntimeError("图片读取失败，请检查图片格式是否正确")

    return recognize_traffic_gesture_frame(
        image_bgr=image_bgr,
        output_path=output_path,
        static_image_mode=True,
    )


def _unwrap_frame_item(item: dict) -> tuple[dict, int | None]:
    if isinstance(item.get("result"), dict):
        return item["result"], item.get("frame_index")
    return item, item.get("frame_index")


def _keypoint_map(result: dict) -> dict[int, dict]:
    points = {}
    for point in result.get("keypoints", []) or []:
        try:
            points[int(point["index"])] = point
        except Exception:
            continue
    return points


def _trajectory_stats(values: list[float]) -> dict:
    if not values:
        return {
            "range": 0.0,
            "std": 0.0,
            "direction_changes": 0,
            "start": None,
            "end": None,
            "min": None,
            "max": None,
            "delta": 0.0,
        }

    deltas = []
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        if abs(delta) >= 0.01:
            deltas.append(1 if delta > 0 else -1)

    direction_changes = sum(
        1 for index in range(1, len(deltas)) if deltas[index] != deltas[index - 1]
    )

    block = max(1, min(5, len(values) // 4 or 1))
    start_value = statistics.median(values[:block])
    end_value = statistics.median(values[-block:])

    return {
        "range": round(max(values) - min(values), 4),
        "std": round(statistics.pstdev(values), 4) if len(values) >= 2 else 0.0,
        "direction_changes": direction_changes,
        "start": round(start_value, 4),
        "end": round(end_value, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "delta": round(end_value - start_value, 4),
    }


def _ratio(items: list[dict], key: str) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if bool(item.get(key))) / len(items)


def _numeric_values(items: list[dict], key: str) -> list[float]:
    values = []
    for item in items:
        value = item.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _arm_temporal_evidence(features_list: list[dict], side: str) -> dict:
    height_values = _numeric_values(features_list, f"{side}_height_ratio")
    dx_values = _numeric_values(features_list, f"{side}_dx_ratio")
    elbow_values = _numeric_values(features_list, f"{side}_elbow_angle")

    height_stats = _trajectory_stats(height_values)
    dx_stats = _trajectory_stats(dx_values)

    high_ratio = sum(1 for value in height_values if value > 0.30) / max(1, len(height_values))
    very_high_ratio = sum(1 for value in height_values if value > 0.55) / max(1, len(height_values))
    down_ratio = sum(1 for value in height_values if value < -0.30) / max(1, len(height_values))
    extended_ratio = sum(1 for value in elbow_values if value >= 132) / max(1, len(elbow_values))

    return {
        "height": height_stats,
        "dx": dx_stats,
        "high_ratio": round(high_ratio, 4),
        "very_high_ratio": round(very_high_ratio, 4),
        "down_ratio": round(down_ratio, 4),
        "extended_ratio": round(extended_ratio, 4),
        "horizontal_ratio": round(_ratio(features_list, f"{side}_arm_horizontal"), 4),
        "vertical_up_ratio": round(_ratio(features_list, f"{side}_arm_vertical_up"), 4),
        "diagonal_down_ratio": round(_ratio(features_list, f"{side}_arm_diagonal_down"), 4),
        "near_chest_ratio": round(_ratio(features_list, f"{side}_wrist_near_chest"), 4),
        "near_head_ratio": round(_ratio(features_list, f"{side}_wrist_near_head"), 4),
    }


def _stop_temporal_score(moving: dict, other: dict) -> tuple[float, dict]:
    moving_height = moving["height"]
    other_height = other["height"]

    amplitude_score = _linear_score(float(moving_height.get("range") or 0), 0.45, 1.40)
    peak_score = _linear_score(float(moving_height.get("max") or -9), 0.22, 0.85)
    starts_low_score = _linear_score(-float(moving_height.get("min") or 0), 0.18, 0.95)
    extension_score = _linear_score(float(moving.get("extended_ratio") or 0), 0.20, 0.72)
    vertical_score = max(
        _linear_score(float(moving.get("vertical_up_ratio") or 0), 0.04, 0.28),
        _linear_score(float(moving.get("near_head_ratio") or 0), 0.02, 0.20),
    )
    other_down_score = _linear_score(float(other.get("down_ratio") or 0), 0.25, 0.75)
    other_static_score = 1.0 - _linear_score(float(other_height.get("range") or 0), 0.10, 0.45)

    score = (
        0.25 * amplitude_score
        + 0.22 * peak_score
        + 0.12 * starts_low_score
        + 0.14 * extension_score
        + 0.10 * vertical_score
        + 0.10 * other_down_score
        + 0.07 * other_static_score
    )

    # 对“单臂大幅上举、另一只手几乎不动”的标准停止视频进行明确加成。
    if (
        float(moving_height.get("range") or 0) >= 0.90
        and float(other_height.get("range") or 0) <= 0.30
        and float(moving_height.get("max") or -9) >= 0.25
    ):
        score = max(score, 0.82)

    return round(_clamp01(score), 4), {
        "amplitude_score": round(amplitude_score, 4),
        "peak_score": round(peak_score, 4),
        "starts_low_score": round(starts_low_score, 4),
        "extension_score": round(extension_score, 4),
        "vertical_score": round(vertical_score, 4),
        "other_down_score": round(other_down_score, 4),
        "other_static_score": round(other_static_score, 4),
    }



def _straight_temporal_score(left: dict, right: dict) -> tuple[float, dict]:
    """
    直行信号时序规则。

    中国道路交通警察直行信号通常表现为：
    - 一侧手臂在肩部高度保持水平伸展，作为“锚定臂”；
    - 另一侧手臂在接近肩部高度做明显横向摆动，运动过程中会经过胸前；
    - 整体运动以水平方向为主，垂直起伏相对有限。

    该结构与“变道信号”最大的区别是：
    - 直行：存在持续的对侧水平锚定臂；
    - 变道：通常只有一只手臂横向摆动，另一臂主要保持下垂。
    """

    def score_pattern(anchor: dict, moving: dict) -> tuple[float, dict]:
        anchor_horizontal = float(anchor.get("horizontal_ratio") or 0)
        anchor_extended = float(anchor.get("extended_ratio") or 0)
        anchor_height_range = float(anchor.get("height", {}).get("range") or 0)

        moving_dx_range = float(moving.get("dx", {}).get("range") or 0)
        moving_height_range = float(moving.get("height", {}).get("range") or 0)
        moving_extended = float(moving.get("extended_ratio") or 0)
        moving_horizontal = float(moving.get("horizontal_ratio") or 0)
        moving_near_chest = float(moving.get("near_chest_ratio") or 0)

        anchor_score = _linear_score(anchor_horizontal, 0.14, 0.62)
        anchor_extended_score = _linear_score(anchor_extended, 0.18, 0.72)
        anchor_stability_score = 1.0 - _linear_score(anchor_height_range, 0.18, 0.80)

        sweep_score = _linear_score(moving_dx_range, 0.22, 0.95)
        moving_extended_score = _linear_score(moving_extended, 0.16, 0.70)
        moving_pose_score = _linear_score(
            max(moving_horizontal, moving_near_chest),
            0.12,
            0.62,
        )
        horizontal_motion_score = 1.0 - _linear_score(
            moving_height_range,
            0.45,
            1.20,
        )

        score = (
            0.30 * anchor_score
            + 0.10 * anchor_extended_score
            + 0.08 * anchor_stability_score
            + 0.28 * sweep_score
            + 0.10 * moving_extended_score
            + 0.10 * moving_pose_score
            + 0.04 * horizontal_motion_score
        )

        # 标准直行视频中，只要出现“稳定水平锚定臂 + 另一臂明显横向扫动”，
        # 即使两臂没有长时间同时水平，也应当判为直行。
        if (
            anchor_horizontal >= 0.18
            and moving_dx_range >= 0.30
            and max(moving_horizontal, moving_near_chest) >= 0.16
        ):
            score = max(score, 0.70)

        return round(_clamp01(score), 4), {
            "anchor_horizontal_score": round(anchor_score, 4),
            "anchor_extended_score": round(anchor_extended_score, 4),
            "anchor_stability_score": round(anchor_stability_score, 4),
            "moving_sweep_score": round(sweep_score, 4),
            "moving_extended_score": round(moving_extended_score, 4),
            "moving_pose_score": round(moving_pose_score, 4),
            "horizontal_motion_score": round(horizontal_motion_score, 4),
            "anchor_horizontal_ratio": round(anchor_horizontal, 4),
            "moving_horizontal_ratio": round(moving_horizontal, 4),
            "moving_near_chest_ratio": round(moving_near_chest, 4),
            "moving_dx_range": round(moving_dx_range, 4),
            "moving_height_range": round(moving_height_range, 4),
        }

    left_anchor_score, left_anchor_detail = score_pattern(left, right)
    right_anchor_score, right_anchor_detail = score_pattern(right, left)

    if left_anchor_score >= right_anchor_score:
        return left_anchor_score, {
            "anchor_side": "left",
            "moving_side": "right",
            "left_anchor_score": left_anchor_score,
            "right_anchor_score": right_anchor_score,
            "selected_detail": left_anchor_detail,
            "other_detail": right_anchor_detail,
        }

    return right_anchor_score, {
        "anchor_side": "right",
        "moving_side": "left",
        "left_anchor_score": left_anchor_score,
        "right_anchor_score": right_anchor_score,
        "selected_detail": right_anchor_detail,
        "other_detail": left_anchor_detail,
    }


def _lane_change_temporal_score(left: dict, right: dict) -> tuple[float, dict]:
    """
    变道信号时序规则。

    变道信号按“一只手臂横向摆动，另一只手臂主要保持下垂”识别。
    若对侧手臂长期保持水平，则更符合直行信号，不应判为变道。
    """

    def score_pattern(moving: dict, other: dict) -> tuple[float, dict]:
        moving_dx_range = float(moving.get("dx", {}).get("range") or 0)
        moving_height_range = float(moving.get("height", {}).get("range") or 0)
        moving_horizontal = float(moving.get("horizontal_ratio") or 0)
        moving_extended = float(moving.get("extended_ratio") or 0)

        other_down = float(other.get("down_ratio") or 0)
        other_horizontal = float(other.get("horizontal_ratio") or 0)

        sweep_score = _linear_score(moving_dx_range, 0.28, 1.05)
        horizontal_score = _linear_score(moving_horizontal, 0.12, 0.58)
        extended_score = _linear_score(moving_extended, 0.16, 0.72)
        other_down_score = _linear_score(other_down, 0.22, 0.72)
        other_not_horizontal_score = 1.0 - _linear_score(
            other_horizontal,
            0.10,
            0.45,
        )
        horizontal_motion_score = 1.0 - _linear_score(
            moving_height_range,
            0.50,
            1.25,
        )

        score = (
            0.34 * sweep_score
            + 0.22 * horizontal_score
            + 0.12 * extended_score
            + 0.20 * other_down_score
            + 0.08 * other_not_horizontal_score
            + 0.04 * horizontal_motion_score
        )

        # 对侧手臂若持续水平，说明更像直行中的锚定臂，降低变道分数。
        score *= 1.0 - 0.35 * _linear_score(other_horizontal, 0.12, 0.55)

        if (
            moving_dx_range >= 0.38
            and other_down >= 0.30
            and other_horizontal <= 0.16
        ):
            score = max(score, 0.68)

        return round(_clamp01(score), 4), {
            "moving_sweep_score": round(sweep_score, 4),
            "moving_horizontal_score": round(horizontal_score, 4),
            "moving_extended_score": round(extended_score, 4),
            "other_down_score": round(other_down_score, 4),
            "other_not_horizontal_score": round(other_not_horizontal_score, 4),
            "horizontal_motion_score": round(horizontal_motion_score, 4),
            "moving_dx_range": round(moving_dx_range, 4),
            "moving_height_range": round(moving_height_range, 4),
            "moving_horizontal_ratio": round(moving_horizontal, 4),
            "other_down_ratio": round(other_down, 4),
            "other_horizontal_ratio": round(other_horizontal, 4),
        }

    left_moving_score, left_moving_detail = score_pattern(left, right)
    right_moving_score, right_moving_detail = score_pattern(right, left)

    if left_moving_score >= right_moving_score:
        return left_moving_score, {
            "moving_side": "left",
            "other_side": "right",
            "left_moving_score": left_moving_score,
            "right_moving_score": right_moving_score,
            "selected_detail": left_moving_detail,
            "other_detail": right_moving_detail,
        }

    return right_moving_score, {
        "moving_side": "right",
        "other_side": "left",
        "left_moving_score": left_moving_score,
        "right_moving_score": right_moving_score,
        "selected_detail": right_moving_detail,
        "other_detail": left_moving_detail,
    }


def _turn_temporal_score(left: dict, right: dict, direction: str) -> tuple[float, dict]:
    """左/右转弯时序规则：目标侧手臂摆动，对侧手臂朝前并相对稳定。"""
    if direction not in {"left", "right"}:
        raise ValueError("direction must be left or right")

    moving = left if direction == "left" else right
    anchor = right if direction == "left" else left

    moving_dx_range = float(moving.get("dx", {}).get("range") or 0)
    moving_height_range = float(moving.get("height", {}).get("range") or 0)
    moving_horizontal = float(moving.get("horizontal_ratio") or 0)
    moving_near_chest = float(moving.get("near_chest_ratio") or 0)
    moving_extended = float(moving.get("extended_ratio") or 0)

    anchor_dx_range = float(anchor.get("dx", {}).get("range") or 0)
    anchor_height_range = float(anchor.get("height", {}).get("range") or 0)
    anchor_near_chest = float(anchor.get("near_chest_ratio") or 0)
    anchor_down = float(anchor.get("down_ratio") or 0)
    anchor_horizontal = float(anchor.get("horizontal_ratio") or 0)

    moving_sweep_score = _linear_score(moving_dx_range, 0.24, 0.95)
    moving_pose_score = _linear_score(max(moving_horizontal, moving_near_chest), 0.12, 0.58)
    moving_extended_score = _linear_score(moving_extended, 0.18, 0.72)
    horizontal_motion_score = 1.0 - _linear_score(moving_height_range, 0.65, 1.50)

    # 朝向镜头的锚定臂在二维图像中往往落在胸前附近，而不是表现为水平长臂。
    anchor_forward_score = _linear_score(anchor_near_chest, 0.10, 0.48)
    anchor_support_score = max(
        anchor_forward_score,
        0.68 * _linear_score(anchor_down, 0.18, 0.62),
    )
    anchor_static_score = 1.0 - _linear_score(
        max(anchor_dx_range, anchor_height_range),
        0.22,
        0.90,
    )

    score = (
        0.25 * moving_sweep_score
        + 0.23 * moving_pose_score
        + 0.10 * moving_extended_score
        + 0.08 * horizontal_motion_score
        + 0.20 * anchor_support_score
        + 0.14 * anchor_static_score
    )

    # 两臂都大幅横向运动且对侧长期水平时，更像直行，不像转弯。
    straight_conflict = min(
        _linear_score(anchor_dx_range, 0.38, 1.05),
        _linear_score(anchor_horizontal, 0.18, 0.58),
    )
    score *= 1.0 - 0.50 * straight_conflict

    # 对侧明显下垂但不在胸前时，更可能是变道；转弯分略降。
    lane_change_conflict = _linear_score(anchor_down, 0.42, 0.82) * (
        1.0 - _linear_score(anchor_near_chest, 0.08, 0.28)
    )
    score *= 1.0 - 0.20 * lane_change_conflict

    if (
        moving_dx_range >= 0.34
        and max(moving_horizontal, moving_near_chest) >= 0.16
        and anchor_dx_range <= 0.82
        and max(anchor_near_chest, anchor_down) >= 0.18
    ):
        score = max(score, 0.68)

    return round(_clamp01(score), 4), {
        "direction": direction,
        "moving_side": direction,
        "anchor_side": "right" if direction == "left" else "left",
        "moving_dx_range": round(moving_dx_range, 4),
        "moving_height_range": round(moving_height_range, 4),
        "moving_horizontal_ratio": round(moving_horizontal, 4),
        "moving_near_chest_ratio": round(moving_near_chest, 4),
        "moving_extended_ratio": round(moving_extended, 4),
        "anchor_dx_range": round(anchor_dx_range, 4),
        "anchor_height_range": round(anchor_height_range, 4),
        "anchor_near_chest_ratio": round(anchor_near_chest, 4),
        "anchor_down_ratio": round(anchor_down, 4),
        "anchor_horizontal_ratio": round(anchor_horizontal, 4),
        "moving_sweep_score": round(moving_sweep_score, 4),
        "moving_pose_score": round(moving_pose_score, 4),
        "anchor_support_score": round(anchor_support_score, 4),
        "anchor_static_score": round(anchor_static_score, 4),
        "straight_conflict": round(straight_conflict, 4),
        "lane_change_conflict": round(lane_change_conflict, 4),
    }


def _left_turn_wait_temporal_score(left: dict, right: dict) -> tuple[float, dict]:
    """左转弯待转：左臂在身体左下方斜向上下摆动，右臂以下垂稳定为主。"""
    height = left.get("height", {})
    left_height_range = float(height.get("range") or 0)
    left_height_delta = float(height.get("delta") or 0)
    left_direction_changes = int(height.get("direction_changes") or 0)
    left_diagonal = float(left.get("diagonal_down_ratio") or 0)
    left_down = float(left.get("down_ratio") or 0)
    left_extended = float(left.get("extended_ratio") or 0)

    right_down = float(right.get("down_ratio") or 0)
    right_motion = max(
        float(right.get("height", {}).get("range") or 0),
        float(right.get("dx", {}).get("range") or 0),
    )

    diagonal_score = _linear_score(left_diagonal, 0.08, 0.42)
    vertical_motion_score = _linear_score(left_height_range, 0.24, 1.05)
    low_position_score = _linear_score(left_down, 0.16, 0.62)
    extension_score = _linear_score(left_extended, 0.18, 0.72)
    other_down_score = _linear_score(right_down, 0.24, 0.72)
    other_static_score = 1.0 - _linear_score(right_motion, 0.16, 0.62)
    repeated_motion_score = max(
        _linear_score(left_direction_changes, 0, 2),
        _linear_score(abs(left_height_delta), 0.18, 0.62),
    )

    score = (
        0.30 * diagonal_score
        + 0.25 * vertical_motion_score
        + 0.10 * low_position_score
        + 0.08 * extension_score
        + 0.13 * other_down_score
        + 0.08 * other_static_score
        + 0.06 * repeated_motion_score
    )

    if left_diagonal >= 0.12 and left_height_range >= 0.30 and right_down >= 0.25:
        score = max(score, 0.69)

    return round(_clamp01(score), 4), {
        "moving_side": "left",
        "left_height_range": round(left_height_range, 4),
        "left_height_delta": round(left_height_delta, 4),
        "left_direction_changes": left_direction_changes,
        "left_diagonal_down_ratio": round(left_diagonal, 4),
        "left_down_ratio": round(left_down, 4),
        "left_extended_ratio": round(left_extended, 4),
        "right_down_ratio": round(right_down, 4),
        "right_motion_range": round(right_motion, 4),
        "diagonal_score": round(diagonal_score, 4),
        "vertical_motion_score": round(vertical_motion_score, 4),
        "other_down_score": round(other_down_score, 4),
        "other_static_score": round(other_static_score, 4),
        "repeated_motion_score": round(repeated_motion_score, 4),
    }


def _slow_down_temporal_score(left: dict, right: dict) -> tuple[float, dict]:
    """减速慢行：右臂由较高位置向右下方反复下压，左臂通常下垂。"""
    height = right.get("height", {})
    right_height_range = float(height.get("range") or 0)
    right_height_delta = float(height.get("delta") or 0)
    right_height_max = float(height.get("max") or -9)
    right_direction_changes = int(height.get("direction_changes") or 0)
    right_diagonal = float(right.get("diagonal_down_ratio") or 0)
    right_horizontal = float(right.get("horizontal_ratio") or 0)
    right_extended = float(right.get("extended_ratio") or 0)

    left_down = float(left.get("down_ratio") or 0)
    left_motion = max(
        float(left.get("height", {}).get("range") or 0),
        float(left.get("dx", {}).get("range") or 0),
    )

    diagonal_score = _linear_score(right_diagonal, 0.08, 0.42)
    vertical_motion_score = _linear_score(right_height_range, 0.34, 1.20)
    starts_high_score = _linear_score(right_height_max, -0.05, 0.48)
    downward_or_repeat_score = max(
        _linear_score(-right_height_delta, 0.12, 0.55),
        _linear_score(right_direction_changes, 0, 2),
    )
    arm_pose_score = _linear_score(max(right_diagonal, right_horizontal), 0.10, 0.48)
    extension_score = _linear_score(right_extended, 0.18, 0.72)
    other_down_score = _linear_score(left_down, 0.22, 0.70)
    other_static_score = 1.0 - _linear_score(left_motion, 0.18, 0.72)

    score = (
        0.25 * vertical_motion_score
        + 0.20 * diagonal_score
        + 0.10 * starts_high_score
        + 0.12 * downward_or_repeat_score
        + 0.10 * arm_pose_score
        + 0.07 * extension_score
        + 0.10 * other_down_score
        + 0.06 * other_static_score
    )

    if right_height_range >= 0.45 and right_diagonal >= 0.10 and left_down >= 0.22:
        score = max(score, 0.70)

    return round(_clamp01(score), 4), {
        "moving_side": "right",
        "right_height_range": round(right_height_range, 4),
        "right_height_delta": round(right_height_delta, 4),
        "right_height_max": round(right_height_max, 4),
        "right_direction_changes": right_direction_changes,
        "right_diagonal_down_ratio": round(right_diagonal, 4),
        "right_horizontal_ratio": round(right_horizontal, 4),
        "right_extended_ratio": round(right_extended, 4),
        "left_down_ratio": round(left_down, 4),
        "left_motion_range": round(left_motion, 4),
        "vertical_motion_score": round(vertical_motion_score, 4),
        "diagonal_score": round(diagonal_score, 4),
        "starts_high_score": round(starts_high_score, 4),
        "downward_or_repeat_score": round(downward_or_repeat_score, 4),
        "other_down_score": round(other_down_score, 4),
        "other_static_score": round(other_static_score, 4),
    }


def _pull_over_temporal_score(left: dict, right: dict) -> tuple[float, dict]:
    """靠边停车：左臂保持停止姿态，右臂在胸前/水平方向反复摆动。"""
    left_height = left.get("height", {})
    left_high_ratio = max(
        float(left.get("high_ratio") or 0),
        float(left.get("vertical_up_ratio") or 0),
        float(left.get("near_head_ratio") or 0),
    )
    left_peak = float(left_height.get("max") or -9)
    left_extended = float(left.get("extended_ratio") or 0)

    right_dx_range = float(right.get("dx", {}).get("range") or 0)
    right_height_range = float(right.get("height", {}).get("range") or 0)
    right_horizontal = float(right.get("horizontal_ratio") or 0)
    right_near_chest = float(right.get("near_chest_ratio") or 0)
    right_extended = float(right.get("extended_ratio") or 0)

    stop_arm_score = max(
        _linear_score(left_high_ratio, 0.06, 0.34),
        _linear_score(left_peak, 0.20, 0.72),
    )
    stop_extension_score = _linear_score(left_extended, 0.20, 0.72)
    sweep_score = _linear_score(right_dx_range, 0.24, 0.95)
    sweep_pose_score = _linear_score(max(right_horizontal, right_near_chest), 0.10, 0.52)
    sweep_extension_score = _linear_score(right_extended, 0.16, 0.70)
    horizontal_motion_score = 1.0 - _linear_score(right_height_range, 0.60, 1.45)

    score = (
        0.31 * stop_arm_score
        + 0.10 * stop_extension_score
        + 0.28 * sweep_score
        + 0.16 * sweep_pose_score
        + 0.08 * sweep_extension_score
        + 0.07 * horizontal_motion_score
    )

    if left_high_ratio >= 0.08 and right_dx_range >= 0.32 and max(right_horizontal, right_near_chest) >= 0.12:
        score = max(score, 0.76)

    return round(_clamp01(score), 4), {
        "stop_side": "left",
        "sweep_side": "right",
        "left_high_ratio": round(left_high_ratio, 4),
        "left_peak_height": round(left_peak, 4),
        "left_extended_ratio": round(left_extended, 4),
        "right_dx_range": round(right_dx_range, 4),
        "right_height_range": round(right_height_range, 4),
        "right_horizontal_ratio": round(right_horizontal, 4),
        "right_near_chest_ratio": round(right_near_chest, 4),
        "right_extended_ratio": round(right_extended, 4),
        "stop_arm_score": round(stop_arm_score, 4),
        "sweep_score": round(sweep_score, 4),
        "sweep_pose_score": round(sweep_pose_score, 4),
        "horizontal_motion_score": round(horizontal_motion_score, 4),
    }

def _frame_selected_score(result: dict, gesture: str) -> float:
    scores = result.get("rule_scores") or {}
    score = scores.get(gesture)
    if isinstance(score, (int, float)):
        return float(score)

    if result.get("gesture") == gesture:
        return float(result.get("confidence", 0) or 0)

    return 0.0


def aggregate_traffic_gesture_sequence(
    frame_items: list[dict],
    vote_threshold: float = 0.55,
    min_valid_frames: int = 3,
) -> dict:
    """八类交警手势连续帧人工规则状态机。"""
    normalized = []
    for item in frame_items:
        if not isinstance(item, dict):
            continue
        result, frame_index = _unwrap_frame_item(item)
        if not isinstance(result, dict):
            continue
        normalized.append({"result": result, "frame_index": frame_index})

    if not normalized:
        command = TRAFFIC_COMMANDS["unknown"]
        return {
            "model": "MediaPipe Pose + Rule State Machine",
            "gesture": "unknown",
            "gesture_name": command["gesture_name"],
            "traffic_command": command["traffic_command"],
            "confidence": 0.0,
            "stable_frames": 0,
            "valid_frames": 0,
            "classified_frames": 0,
            "window_size": 0,
            "vote_ratio": 0.0,
            "vote_counts": {},
            "rule_scores": {},
            "temporal_evidence": {},
            "best_frame_index": None,
            "keypoints": [],
            "features": {"reason": "empty_sequence"},
            "classifier_type": "rule_based_temporal_state_machine",
            "rule_version": "traffic_rule_v6_calibrated_signature_fusion",
        }

    pose_items = [
        item for item in normalized
        if item["result"].get("keypoints")
        and item["result"].get("features", {}).get("reason") not in {
            "pose_not_detected", "missing_required_keypoints",
        }
    ]
    classified_items = [
        item for item in normalized
        if item["result"].get("gesture") not in (None, "", "unknown")
    ]
    features_list = [
        item["result"].get("features", {})
        for item in pose_items
        if isinstance(item["result"].get("features"), dict)
    ]

    left_evidence = _arm_temporal_evidence(features_list, "left")
    right_evidence = _arm_temporal_evidence(features_list, "right")

    left_stop_score, left_stop_detail = _stop_temporal_score(left_evidence, right_evidence)
    right_stop_score, right_stop_detail = _stop_temporal_score(right_evidence, left_evidence)
    stop_temporal_score = max(left_stop_score, right_stop_score)
    stop_side = "left" if left_stop_score >= right_stop_score else "right"

    straight_temporal_score, straight_detail = _straight_temporal_score(left_evidence, right_evidence)
    lane_change_raw_score, lane_change_detail = _lane_change_temporal_score(left_evidence, right_evidence)
    left_turn_score, left_turn_detail = _turn_temporal_score(left_evidence, right_evidence, "left")
    right_turn_score, right_turn_detail = _turn_temporal_score(left_evidence, right_evidence, "right")
    left_turn_wait_score, left_turn_wait_detail = _left_turn_wait_temporal_score(left_evidence, right_evidence)
    slow_down_score, slow_down_detail = _slow_down_temporal_score(left_evidence, right_evidence)
    pull_over_score, pull_over_detail = _pull_over_temporal_score(left_evidence, right_evidence)

    left_dx_range = float(left_evidence.get("dx", {}).get("range") or 0)
    right_dx_range = float(right_evidence.get("dx", {}).get("range") or 0)
    two_arm_motion_score = _linear_score(min(left_dx_range, right_dx_range), 0.20, 0.80)
    horizontal_anchor_score = _linear_score(
        max(float(left_evidence.get("horizontal_ratio") or 0), float(right_evidence.get("horizontal_ratio") or 0)),
        0.24, 0.60,
    )
    straight_structure_strength = min(two_arm_motion_score, horizontal_anchor_score)
    lane_change_adjusted_score = round(
        _clamp01(lane_change_raw_score * (1.0 - 0.58 * straight_structure_strength)),
        4,
    )

    # V5：停止信号是最容易“吞掉”其他动作的通用上举模式。
    # 因此先计算更具体的动作结构，再对停止得分做冲突抑制。
    selected_stop_detail = left_stop_detail if stop_side == "left" else right_stop_detail
    stop_moving_evidence = left_evidence if stop_side == "left" else right_evidence
    stop_other_evidence = right_evidence if stop_side == "left" else left_evidence

    pull_over_signature_strength = min(
        _linear_score(pull_over_score, 0.58, 0.78),
        max(
            float(pull_over_detail.get("stop_arm_score") or 0),
            _linear_score(float(pull_over_detail.get("left_peak_height") or -9), 0.16, 0.62),
        ),
        max(
            float(pull_over_detail.get("sweep_score") or 0),
            float(pull_over_detail.get("sweep_pose_score") or 0),
        ),
    )

    left_turn_signature_strength = min(
        _linear_score(left_turn_score, 0.58, 0.74),
        max(
            float(left_turn_detail.get("moving_sweep_score") or 0),
            float(left_turn_detail.get("moving_pose_score") or 0),
        ),
        max(
            float(left_turn_detail.get("anchor_support_score") or 0),
            float(left_turn_detail.get("anchor_static_score") or 0),
        ),
    )
    right_turn_signature_strength = min(
        _linear_score(right_turn_score, 0.58, 0.74),
        max(
            float(right_turn_detail.get("moving_sweep_score") or 0),
            float(right_turn_detail.get("moving_pose_score") or 0),
        ),
        max(
            float(right_turn_detail.get("anchor_support_score") or 0),
            float(right_turn_detail.get("anchor_static_score") or 0),
        ),
    )

    left_wait_signature_strength = min(
        _linear_score(left_turn_wait_score, 0.58, 0.73),
        max(
            float(left_turn_wait_detail.get("diagonal_score") or 0),
            float(left_turn_wait_detail.get("vertical_motion_score") or 0),
        ),
        max(
            float(left_turn_wait_detail.get("other_down_score") or 0),
            float(left_turn_wait_detail.get("other_static_score") or 0),
        ),
    )

    slow_signature_strength = min(
        _linear_score(slow_down_score, 0.58, 0.73),
        max(
            float(slow_down_detail.get("diagonal_score") or 0),
            float(slow_down_detail.get("downward_or_repeat_score") or 0),
        ),
        max(
            float(slow_down_detail.get("vertical_motion_score") or 0),
            float(slow_down_detail.get("other_down_score") or 0),
        ),
    )

    selected_straight_detail_for_conflict = straight_detail.get("selected_detail", {})
    straight_signature_strength = min(
        _linear_score(straight_temporal_score, 0.64, 0.80),
        _linear_score(
            float(selected_straight_detail_for_conflict.get("anchor_horizontal_ratio") or 0),
            0.22,
            0.55,
        ),
        _linear_score(
            float(selected_straight_detail_for_conflict.get("moving_dx_range") or 0),
            0.35,
            0.90,
        ),
    )

    lane_signature_strength = min(
        _linear_score(lane_change_adjusted_score, 0.60, 0.78),
        max(
            float(lane_change_detail.get("selected_detail", {}).get("moving_sweep_score") or 0),
            float(lane_change_detail.get("selected_detail", {}).get("moving_horizontal_score") or 0),
        ),
        max(
            float(lane_change_detail.get("selected_detail", {}).get("other_down_score") or 0),
            float(lane_change_detail.get("selected_detail", {}).get("other_not_horizontal_score") or 0),
        ),
    )

    stop_conflict_strength = max(
        pull_over_signature_strength,
        left_turn_signature_strength,
        right_turn_signature_strength,
        left_wait_signature_strength,
        slow_signature_strength,
        0.78 * straight_signature_strength,
        0.55 * lane_signature_strength,
    )

    stop_other_motion_range = max(
        float(stop_other_evidence.get("height", {}).get("range") or 0),
        float(stop_other_evidence.get("dx", {}).get("range") or 0),
    )
    stop_other_activity_score = max(
        _linear_score(stop_other_motion_range, 0.22, 0.85),
        _linear_score(float(stop_other_evidence.get("horizontal_ratio") or 0), 0.12, 0.50),
        _linear_score(float(stop_other_evidence.get("diagonal_down_ratio") or 0), 0.08, 0.34),
    )

    # 对侧手臂明显参与、或某个更具体动作结构成立时，停止得分必须降低。
    stop_adjusted_score = round(
        _clamp01(
            stop_temporal_score
            * (1.0 - 0.62 * stop_conflict_strength)
            * (1.0 - 0.18 * stop_other_activity_score)
        ),
        4,
    )

    vote_counts = Counter(
        item["result"].get("gesture")
        for item in classified_items
        if item["result"].get("gesture")
    )
    classified_count = len(classified_items)
    static_vote_scores = {
        gesture: vote_counts.get(gesture, 0) / max(1, classified_count)
        for gesture in TRAFFIC_COMMANDS if gesture != "unknown"
    }

    frame_rule_scores: dict[str, list[float]] = {
        name: [] for name in TRAFFIC_COMMANDS if name != "unknown"
    }
    for item in pose_items:
        scores = item["result"].get("rule_scores") or {}
        for name in frame_rule_scores:
            value = scores.get(name)
            if isinstance(value, (int, float)):
                frame_rule_scores[name].append(float(value))

    score_summary = {}
    for name, values in frame_rule_scores.items():
        score_summary[name] = {
            "mean": round(sum(values) / len(values), 4) if values else 0.0,
            "max": round(max(values), 4) if values else 0.0,
            "frames_ge_0_62": sum(1 for value in values if value >= 0.62),
        }

    left_horizontal_ratio = float(left_evidence.get("horizontal_ratio") or 0)
    right_horizontal_ratio = float(right_evidence.get("horizontal_ratio") or 0)
    both_horizontal_ratio = min(left_horizontal_ratio, right_horizontal_ratio)

    raw_temporal_rule_scores = {
        "stop": round(max(stop_adjusted_score, 0.24 * static_vote_scores.get("stop", 0)), 4),
        "straight": round(max(
            straight_temporal_score,
            0.35 * static_vote_scores.get("straight", 0),
            0.62 * both_horizontal_ratio
            + 0.19 * float(left_evidence.get("extended_ratio") or 0)
            + 0.19 * float(right_evidence.get("extended_ratio") or 0),
        ), 4),
        "pull_over": round(max(pull_over_score, 0.35 * static_vote_scores.get("pull_over", 0)), 4),
        "lane_change": round(max(lane_change_adjusted_score, 0.25 * static_vote_scores.get("lane_change", 0)), 4),
        "left_turn": round(max(left_turn_score, 0.35 * static_vote_scores.get("left_turn", 0)), 4),
        "right_turn": round(max(right_turn_score, 0.35 * static_vote_scores.get("right_turn", 0)), 4),
        "left_turn_wait": round(max(left_turn_wait_score, 0.35 * static_vote_scores.get("left_turn_wait", 0)), 4),
        "slow_down": round(max(slow_down_score, 0.35 * static_vote_scores.get("slow_down", 0)), 4),
    }

    def _arm_stability_score(evidence: dict) -> float:
        height_range = float(evidence.get("height", {}).get("range") or 0)
        dx_range = float(evidence.get("dx", {}).get("range") or 0)
        return _clamp01(min(
            1.0 - _linear_score(height_range, 0.25, 0.75),
            1.0 - _linear_score(dx_range, 0.12, 0.45),
        ))

    def _turn_command_score(active: dict, other: dict) -> float:
        peak_score = _linear_score(float(active.get("height", {}).get("max") or -9), 0.12, 0.50)
        chest_score = _linear_score(float(active.get("near_chest_ratio") or 0), 0.10, 0.35)
        motion_score = _linear_score(float(active.get("height", {}).get("range") or 0), 0.65, 1.45)
        other_down_score = _linear_score(float(other.get("down_ratio") or 0), 0.72, 0.98)
        anti_diagonal_score = 1.0 - _linear_score(float(active.get("diagonal_down_ratio") or 0), 0.10, 0.28)
        high_score = max(
            _linear_score(float(active.get("high_ratio") or 0), 0.04, 0.22),
            peak_score,
        )
        raw_score = (
            0.22 * peak_score
            + 0.23 * chest_score
            + 0.18 * motion_score
            + 0.16 * other_down_score
            + 0.11 * anti_diagonal_score
            + 0.10 * high_score
        )
        # 转弯动作必须经过胸前；否则单臂上举、停止或靠边会被误判为转弯。
        return _clamp01(raw_score * (0.30 + 0.70 * chest_score))

    def _lane_command_score(active: dict, other: dict) -> float:
        other_stability = _arm_stability_score(other)
        active_peak = float(active.get("height", {}).get("max") or -9)
        raw_score = (
            0.22 * _linear_score(float(active.get("height", {}).get("range") or 0), 0.65, 1.25)
            + 0.18 * _linear_score(float(active.get("dx", {}).get("range") or 0), 0.25, 0.70)
            + 0.20 * _linear_score(float(active.get("near_chest_ratio") or 0), 0.08, 0.28)
            + 0.18 * other_stability
            + 0.12 * (1.0 - _linear_score(active_peak, 0.12, 0.35))
            + 0.10 * (1.0 - _linear_score(float(active.get("diagonal_down_ratio") or 0), 0.12, 0.28))
        )
        return _clamp01(raw_score * (0.45 + 0.55 * other_stability))

    def _stop_command_score(active: dict, other: dict) -> float:
        other_stability = _arm_stability_score(other)
        raw_score = (
            0.25 * _linear_score(float(active.get("height", {}).get("max") or -9), 0.45, 0.80)
            + 0.18 * _linear_score(float(active.get("high_ratio") or 0), 0.08, 0.20)
            + 0.14 * _linear_score(float(active.get("vertical_up_ratio") or 0), 0.08, 0.20)
            + 0.18 * other_stability
            + 0.13 * (1.0 - _linear_score(float(active.get("near_chest_ratio") or 0), 0.08, 0.22))
            + 0.12 * (1.0 - _linear_score(float(active.get("diagonal_down_ratio") or 0), 0.08, 0.22))
        )
        # 停止必须有稳定的另一只手；靠边停车中另一只手明显摆动，不能再判停止。
        return _clamp01(raw_score * (0.35 + 0.65 * other_stability))

    left_motion_strength = max(
        float(left_evidence.get("height", {}).get("range") or 0),
        float(left_evidence.get("dx", {}).get("range") or 0),
    )
    right_motion_strength = max(
        float(right_evidence.get("height", {}).get("range") or 0),
        float(right_evidence.get("dx", {}).get("range") or 0),
    )

    if left_motion_strength >= right_motion_strength:
        lane_active_side = "left"
        lane_active = left_evidence
        lane_other = right_evidence
    else:
        lane_active_side = "right"
        lane_active = right_evidence
        lane_other = left_evidence

    left_turn_command_score = _turn_command_score(right_evidence, left_evidence)
    right_turn_command_score = _turn_command_score(left_evidence, right_evidence)
    lane_command_score = _lane_command_score(lane_active, lane_other)

    left_stop_command_score = _stop_command_score(left_evidence, right_evidence)
    right_stop_command_score = _stop_command_score(right_evidence, left_evidence)
    if left_stop_command_score >= right_stop_command_score:
        v6_stop_side = "left"
        stop_command_score = left_stop_command_score
    else:
        v6_stop_side = "right"
        stop_command_score = right_stop_command_score

    selected_straight_detail = straight_detail.get("selected_detail", {})

    pull_over_signature = (
        float(left_evidence.get("height", {}).get("max") or -9) >= 0.55
        and float(left_evidence.get("high_ratio") or 0) >= 0.12
        and float(right_evidence.get("dx", {}).get("range") or 0) >= 0.50
        and float(right_evidence.get("height", {}).get("range") or 0) >= 0.35
        and pull_over_score >= 0.62
    )
    straight_signature = (
        straight_temporal_score >= 0.72
        and float(selected_straight_detail.get("anchor_horizontal_ratio") or 0) >= 0.34
        and min(left_dx_range, right_dx_range) >= 0.45
    )
    left_wait_signature = (
        float(left_evidence.get("diagonal_down_ratio") or 0) >= 0.22
        and float(right_evidence.get("height", {}).get("range") or 0) <= 0.36
        and float(right_evidence.get("dx", {}).get("range") or 0) <= 0.18
        and left_turn_wait_score >= 0.64
    )
    slow_signature = (
        float(right_evidence.get("diagonal_down_ratio") or 0) >= 0.22
        and float(left_evidence.get("height", {}).get("range") or 0) <= 0.36
        and float(left_evidence.get("dx", {}).get("range") or 0) <= 0.18
        and slow_down_score >= 0.68
    )
    # 面向摄像机时，指令方向与画面中主要运动手臂相反：
    # 右臂经过胸前 -> 左转；左臂经过胸前 -> 右转。
    left_turn_signature = (
        float(right_evidence.get("height", {}).get("max") or -9) >= 0.22
        and float(right_evidence.get("near_chest_ratio") or 0) >= 0.18
        and float(right_evidence.get("height", {}).get("range") or 0) >= 0.85
        and float(left_evidence.get("down_ratio") or 0) >= 0.85
        and float(right_evidence.get("diagonal_down_ratio") or 0) < 0.18
    )
    right_turn_signature = (
        float(left_evidence.get("height", {}).get("max") or -9) >= 0.22
        and float(left_evidence.get("near_chest_ratio") or 0) >= 0.18
        and float(left_evidence.get("height", {}).get("range") or 0) >= 0.85
        and float(right_evidence.get("down_ratio") or 0) >= 0.85
        and float(left_evidence.get("diagonal_down_ratio") or 0) < 0.18
    )
    lane_signature = (
        _arm_stability_score(lane_other) >= 0.55
        and float(lane_active.get("height", {}).get("max") or -9) < 0.18
        and float(lane_active.get("near_chest_ratio") or 0) >= 0.10
        and float(lane_active.get("height", {}).get("range") or 0) >= 0.70
        and float(lane_active.get("dx", {}).get("range") or 0) >= 0.30
        and float(lane_active.get("diagonal_down_ratio") or 0) < 0.18
    )
    stop_signature = (
        (
            (
                float(left_evidence.get("height", {}).get("max") or -9) >= 0.60
                or float(left_evidence.get("high_ratio") or 0) >= 0.12
            )
            and _arm_stability_score(right_evidence) >= 0.60
            and float(left_evidence.get("near_chest_ratio") or 0) < 0.12
            and float(left_evidence.get("diagonal_down_ratio") or 0) < 0.12
        )
        or (
            (
                float(right_evidence.get("height", {}).get("max") or -9) >= 0.60
                or float(right_evidence.get("high_ratio") or 0) >= 0.12
            )
            and _arm_stability_score(left_evidence) >= 0.60
            and float(right_evidence.get("near_chest_ratio") or 0) < 0.12
            and float(right_evidence.get("diagonal_down_ratio") or 0) < 0.12
        )
    )

    temporal_rule_scores = {
        "stop": round(stop_command_score if stop_signature else min(stop_command_score, 0.59), 4),
        "straight": round(straight_temporal_score if straight_signature else min(straight_temporal_score, 0.59), 4),
        "pull_over": round(pull_over_score if pull_over_signature else min(pull_over_score, 0.59), 4),
        "lane_change": round(lane_command_score if lane_signature else min(lane_command_score, 0.59), 4),
        "left_turn": round(left_turn_command_score if left_turn_signature else min(left_turn_command_score, 0.59), 4),
        "right_turn": round(right_turn_command_score if right_turn_signature else min(right_turn_command_score, 0.59), 4),
        "left_turn_wait": round(left_turn_wait_score if left_wait_signature else min(left_turn_wait_score, 0.59), 4),
        "slow_down": round(slow_down_score if slow_signature else min(slow_down_score, 0.59), 4),
    }

    decision_support_ratios = {
        "stop": max(
            float((left_evidence if v6_stop_side == "left" else right_evidence).get("high_ratio") or 0),
            float((left_evidence if v6_stop_side == "left" else right_evidence).get("vertical_up_ratio") or 0),
        ),
        "straight": float(
            (left_evidence if straight_detail.get("anchor_side") == "left" else right_evidence)
            .get("horizontal_ratio") or 0
        ),
        "pull_over": min(
            max(float(left_evidence.get("high_ratio") or 0), float(left_evidence.get("vertical_up_ratio") or 0)),
            max(
                float(right_evidence.get("horizontal_ratio") or 0),
                float(right_evidence.get("near_chest_ratio") or 0),
                min(1.0, float(right_evidence.get("dx", {}).get("range") or 0) / 1.0),
            ),
        ),
        "lane_change": max(
            float(lane_active.get("near_chest_ratio") or 0),
            min(1.0, float(lane_active.get("dx", {}).get("range") or 0) / 2.5),
        ),
        "left_turn": max(
            float(right_evidence.get("near_chest_ratio") or 0),
            float(right_evidence.get("high_ratio") or 0),
        ),
        "right_turn": max(
            float(left_evidence.get("near_chest_ratio") or 0),
            float(left_evidence.get("high_ratio") or 0),
        ),
        "left_turn_wait": float(left_evidence.get("diagonal_down_ratio") or 0),
        "slow_down": float(right_evidence.get("diagonal_down_ratio") or 0),
    }

    # V6：使用互斥动作签名，而不是仅按一个固定优先级比较重叠分数。
    if pull_over_signature:
        selected, selected_score = "pull_over", temporal_rule_scores["pull_over"]
    elif straight_signature:
        selected, selected_score = "straight", temporal_rule_scores["straight"]
    elif left_wait_signature:
        selected, selected_score = "left_turn_wait", temporal_rule_scores["left_turn_wait"]
    elif slow_signature:
        selected, selected_score = "slow_down", temporal_rule_scores["slow_down"]
    elif left_turn_signature:
        selected, selected_score = "left_turn", temporal_rule_scores["left_turn"]
    elif right_turn_signature:
        selected, selected_score = "right_turn", temporal_rule_scores["right_turn"]
    elif lane_signature:
        selected, selected_score = "lane_change", temporal_rule_scores["lane_change"]
    elif stop_signature:
        stop_side = v6_stop_side
        selected, selected_score = "stop", temporal_rule_scores["stop"]
    else:
        selected, selected_score = _choose_gesture(temporal_rule_scores, threshold=0.60)

    def best_item_for(gesture: str) -> dict:
        candidates = pose_items or normalized
        if gesture == "stop":
            key = f"{stop_side}_height_ratio"
            return max(candidates, key=lambda item: (
                float(item["result"].get("features", {}).get(key, -9) or -9),
                _frame_selected_score(item["result"], gesture),
            ))
        if gesture == "straight":
            return max(candidates, key=lambda item: (
                int(bool(item["result"].get("features", {}).get("left_arm_horizontal")))
                + int(bool(item["result"].get("features", {}).get("right_arm_horizontal"))),
                _frame_selected_score(item["result"], gesture),
            ))
        if gesture == "pull_over":
            return max(candidates, key=lambda item: (
                int(bool(item["result"].get("features", {}).get("left_arm_high")))
                + int(bool(item["result"].get("features", {}).get("left_arm_vertical_up"))),
                int(bool(item["result"].get("features", {}).get("right_arm_horizontal")))
                + int(bool(item["result"].get("features", {}).get("right_wrist_near_chest"))),
                _frame_selected_score(item["result"], gesture),
            ))
        if gesture in {"left_turn", "right_turn"}:
            # 面向摄像机的视频中：右臂主要运动对应左转，左臂主要运动对应右转。
            side = "right" if gesture == "left_turn" else "left"
            return max(candidates, key=lambda item: (
                int(bool(item["result"].get("features", {}).get(f"{side}_arm_horizontal")))
                + int(bool(item["result"].get("features", {}).get(f"{side}_wrist_near_chest"))),
                _frame_selected_score(item["result"], gesture),
            ))
        if gesture == "left_turn_wait":
            return max(candidates, key=lambda item: (
                int(bool(item["result"].get("features", {}).get("left_arm_diagonal_down"))),
                _frame_selected_score(item["result"], gesture),
            ))
        if gesture == "slow_down":
            return max(candidates, key=lambda item: (
                int(bool(item["result"].get("features", {}).get("right_arm_diagonal_down"))),
                _frame_selected_score(item["result"], gesture),
            ))
        if gesture == "lane_change":
            return max(candidates, key=lambda item: (
                int(bool(item["result"].get("features", {}).get("right_arm_horizontal"))),
                _frame_selected_score(item["result"], gesture),
            ))
        return max(candidates, key=lambda item: _frame_selected_score(item["result"], gesture))

    if selected == "unknown":
        best_item = max(normalized, key=lambda item: float(item["result"].get("confidence", 0) or 0))
        stable_frames = 0
        vote_ratio = 0.0
        final_confidence = min(0.59, max(0.35, selected_score))
        stable = False
    else:
        best_item = best_item_for(selected)
        static_selected_frames = vote_counts.get(selected, 0)
        rule_selected_frames = score_summary.get(selected, {}).get("frames_ge_0_62", 0)
        stable_frames = max(static_selected_frames, int(rule_selected_frames or 0))

        if stable_frames == 0:
            evidence_ratio = float(decision_support_ratios.get(selected, 0.0) or 0.0)
            stable_frames = max(1, int(round(evidence_ratio * max(1, len(pose_items)))))

        vote_ratio = stable_frames / max(1, len(pose_items))
        stable = len(pose_items) >= min_valid_frames and selected_score >= 0.60
        vis_values = _numeric_values(features_list, "pose_visibility")
        avg_visibility = sum(vis_values) / len(vis_values) if vis_values else 0.0
        consistency = min(1.0, vote_ratio + 0.45 * static_vote_scores.get(selected, 0))
        final_confidence = min(0.95, 0.54 * selected_score + 0.20 * avg_visibility + 0.16 * consistency + 0.08)

    command = TRAFFIC_COMMANDS.get(selected, TRAFFIC_COMMANDS["unknown"])
    best_result = dict(best_item["result"])

    temporal_evidence = {
        "left_arm": left_evidence,
        "right_arm": right_evidence,
        "stop": {
            "selected_side": stop_side,
            "score": round(stop_adjusted_score, 4),
            "raw_score": round(stop_temporal_score, 4),
            "conflict_strength": round(stop_conflict_strength, 4),
            "other_activity_score": round(stop_other_activity_score, 4),
            "other_motion_range": round(stop_other_motion_range, 4),
            "pull_over_conflict": round(pull_over_signature_strength, 4),
            "left_turn_conflict": round(left_turn_signature_strength, 4),
            "right_turn_conflict": round(right_turn_signature_strength, 4),
            "left_wait_conflict": round(left_wait_signature_strength, 4),
            "slow_down_conflict": round(slow_signature_strength, 4),
            "straight_conflict": round(straight_signature_strength, 4),
            "lane_change_conflict": round(lane_signature_strength, 4),
            "left_score": round(left_stop_score, 4),
            "right_score": round(right_stop_score, 4),
            "left_detail": left_stop_detail,
            "right_detail": right_stop_detail,
            "rule": "单臂上举至头部附近且另一臂稳定；另一臂参与摆动时不判停止",
        },
        "straight": {
            "score": round(straight_temporal_score, 4),
            **straight_detail,
            "rule": "一侧手臂持续水平伸展，另一侧手臂在肩部附近横向摆动",
        },
        "lane_change": {
            "score": round(lane_change_adjusted_score, 4),
            "raw_score": round(lane_change_raw_score, 4),
            "two_arm_motion_score": round(two_arm_motion_score, 4),
            "horizontal_anchor_score": round(horizontal_anchor_score, 4),
            "straight_structure_strength": round(straight_structure_strength, 4),
            **lane_change_detail,
            "rule": "单臂中低位经过胸前摆动，另一臂稳定下垂；高位经过胸前则判为转弯",
        },
        "left_turn": {
            "score": round(left_turn_command_score, 4),
            "base_score": round(left_turn_score, 4),
            "active_side": "right",
            "signature_matched": bool(left_turn_signature),
            **left_turn_detail,
            "rule": "右臂经过胸前并达到较高位置，左臂主要保持下垂，判定左转",
        },
        "right_turn": {
            "score": round(right_turn_command_score, 4),
            "base_score": round(right_turn_score, 4),
            "active_side": "left",
            "signature_matched": bool(right_turn_signature),
            **right_turn_detail,
            "rule": "左臂经过胸前并达到较高位置，右臂主要保持下垂，判定右转",
        },
        "left_turn_wait": {
            "score": round(left_turn_wait_score, 4),
            **left_turn_wait_detail,
            "rule": "左臂在身体左下方斜向上下摆动，右臂保持下垂",
        },
        "slow_down": {
            "score": round(slow_down_score, 4),
            **slow_down_detail,
            "rule": "右臂由较高位置向右下方反复下压，左臂保持下垂",
        },
        "pull_over": {
            "score": round(pull_over_score, 4),
            **pull_over_detail,
            "rule": "左臂保持停止姿态，右臂在胸前/水平方向摆动示意靠边",
        },
        "decision": {
            "selected": selected,
            "selected_score": round(selected_score, 4),
            "signature_matches": {
                "stop": bool(stop_signature),
                "straight": bool(straight_signature),
                "lane_change": bool(lane_signature),
                "left_turn": bool(left_turn_signature),
                "right_turn": bool(right_turn_signature),
                "left_turn_wait": bool(left_wait_signature),
                "slow_down": bool(slow_signature),
                "pull_over": bool(pull_over_signature),
            },
            "support_ratios": {
                key: round(float(value or 0), 4)
                for key, value in decision_support_ratios.items()
            },
            "lane_active_side": lane_active_side,
            "turn_direction_mapping": "右臂主要运动->左转；左臂主要运动->右转",
            "raw_rule_scores": raw_temporal_rule_scores,
        },
        "frame_rule_score_summary": score_summary,
        "pose_frames": len(pose_items),
        "classified_frames": classified_count,
    }

    best_result.update({
        "model": "MediaPipe Pose + Eight-Gesture Rule State Machine V6",
        "gesture": selected,
        "gesture_name": command["gesture_name"],
        "traffic_command": command["traffic_command"],
        "confidence": round(final_confidence, 4),
        "stable_frames": int(stable_frames),
        "valid_frames": len(pose_items),
        "classified_frames": classified_count,
        "window_size": len(normalized),
        "vote_ratio": round(vote_ratio, 4),
        "vote_counts": dict(vote_counts),
        "rule_scores": temporal_rule_scores,
        "temporal_evidence": temporal_evidence,
        "sequence_features": {
            "left_height": left_evidence["height"],
            "right_height": right_evidence["height"],
            "left_dx": left_evidence["dx"],
            "right_dx": right_evidence["dx"],
        },
        "best_frame_index": best_item["frame_index"],
        "temporal_stable": bool(stable),
        "temporal_policy": "八类姿态评分 + 连续帧轨迹 + 动作签名校准 + 支持帧融合",
        "classifier_type": "rule_based_temporal_state_machine",
        "rule_version": "traffic_rule_v6_calibrated_signature_fusion",
        "matched_rule": selected if selected != "unknown" else "none",
    })
    return best_result

