"""
交警手势关键点特征提取脚本骨架

用途：
1. 从交警手势图片数据集中提取 MediaPipe Pose 人体关键点
2. 将关键点保存为 .npz 特征文件
3. 为后续 train_classifier.py 训练分类器提供输入

推荐数据组织方式：
backend/datasets/traffic_gesture/
├── stop/
│   ├── 001.jpg
│   └── 002.jpg
├── forward/
├── left_turn/
├── right_turn/
└── ...

说明：
- 当前脚本优先支持“按类别文件夹组织的图片数据集”
- 如果老师数据集是 mp4 + csv 逐帧标签，后续需要在 collect_image_samples() 里补充视频逐帧解析逻辑
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np
from tqdm import tqdm


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract MediaPipe Pose features for traffic gesture classification.")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("backend/datasets/traffic_gesture"),
        help="交警手势数据集根目录，默认按 label/image.jpg 形式组织。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/training/traffic_gesture/traffic_pose_features.npz"),
        help="输出特征文件路径。",
    )
    parser.add_argument(
        "--label-map",
        type=Path,
        default=Path("backend/models/traffic_gesture/label_map.json"),
        help="输出标签映射 JSON。",
    )
    parser.add_argument(
        "--min-detection-confidence",
        type=float,
        default=0.5,
        help="MediaPipe Pose 最小检测置信度。",
    )
    return parser.parse_args()


def collect_image_samples(dataset_dir: Path) -> List[Tuple[Path, str]]:
    """按 label/image.jpg 目录结构收集图片样本。"""
    samples: List[Tuple[Path, str]] = []

    if not dataset_dir.exists():
        raise FileNotFoundError(f"数据集目录不存在：{dataset_dir.resolve()}")

    for label_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
        label = label_dir.name
        for image_path in sorted(label_dir.rglob("*")):
            if image_path.suffix.lower() in IMAGE_SUFFIXES:
                samples.append((image_path, label))

    return samples


def normalize_pose_landmarks(landmarks) -> np.ndarray:
    """
    将 MediaPipe Pose 的 33 个关键点转换为固定长度特征。

    特征设计：
    - 每个关键点保留 x, y, z, visibility
    - 使用左右髋中心作为原点做相对归一化
    - 使用左右肩距离作为尺度归一化，减少画面远近影响

    输出维度：33 * 4 = 132
    """
    points = np.array(
        [[lm.x, lm.y, lm.z, lm.visibility] for lm in landmarks],
        dtype=np.float32,
    )

    # MediaPipe Pose 索引：11 left_shoulder, 12 right_shoulder, 23 left_hip, 24 right_hip
    left_shoulder = points[11, :3]
    right_shoulder = points[12, :3]
    left_hip = points[23, :3]
    right_hip = points[24, :3]

    origin = (left_hip + right_hip) / 2.0
    shoulder_width = np.linalg.norm(left_shoulder[:2] - right_shoulder[:2])
    scale = max(float(shoulder_width), 1e-6)

    coords = points[:, :3]
    coords = (coords - origin) / scale

    features = np.concatenate([coords, points[:, 3:4]], axis=1)
    return features.reshape(-1).astype(np.float32)


def extract_pose_feature(image_path: Path, pose) -> Optional[np.ndarray]:
    image = cv2.imread(str(image_path))
    if image is None:
        return None

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = pose.process(rgb)

    if not result.pose_landmarks:
        return None

    return normalize_pose_landmarks(result.pose_landmarks.landmark)


def main() -> None:
    args = parse_args()

    samples = collect_image_samples(args.dataset_dir)
    if not samples:
        raise RuntimeError(
            f"未找到图片样本。请确认目录结构是否为：{args.dataset_dir}/label/*.jpg"
        )

    label_names = sorted({label for _, label in samples})
    label_to_id: Dict[str, int] = {label: idx for idx, label in enumerate(label_names)}
    id_to_label: Dict[str, str] = {str(idx): label for label, idx in label_to_id.items()}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.label_map.parent.mkdir(parents=True, exist_ok=True)

    features: List[np.ndarray] = []
    labels: List[int] = []
    paths: List[str] = []
    skipped = 0

    mp_pose = mp.solutions.pose

    with mp_pose.Pose(
        static_image_mode=True,
        model_complexity=1,
        enable_segmentation=False,
        min_detection_confidence=args.min_detection_confidence,
    ) as pose:
        for image_path, label in tqdm(samples, desc="Extracting pose features"):
            feature = extract_pose_feature(image_path, pose)
            if feature is None:
                skipped += 1
                continue

            features.append(feature)
            labels.append(label_to_id[label])
            paths.append(str(image_path))

    if not features:
        raise RuntimeError("没有成功提取到任何人体姿态关键点，请检查数据集图片是否包含清晰人体。")

    X = np.stack(features).astype(np.float32)
    y = np.array(labels, dtype=np.int64)
    sample_paths = np.array(paths)

    np.savez_compressed(args.output, X=X, y=y, paths=sample_paths)

    label_map = {
        "label_to_id": label_to_id,
        "id_to_label": id_to_label,
        "feature_dim": int(X.shape[1]),
        "sample_count": int(len(y)),
        "skipped_count": int(skipped),
        "note": "MediaPipe Pose 33 landmarks, normalized by hip center and shoulder width.",
    }

    args.label_map.write_text(json.dumps(label_map, ensure_ascii=False, indent=2), encoding="utf-8")

    print("交警手势特征提取完成")
    print(f"有效样本数：{len(y)}")
    print(f"跳过样本数：{skipped}")
    print(f"特征维度：{X.shape[1]}")
    print(f"特征文件：{args.output}")
    print(f"标签映射：{args.label_map}")


if __name__ == "__main__":
    main()
