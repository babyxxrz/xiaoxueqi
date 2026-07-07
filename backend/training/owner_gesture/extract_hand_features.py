"""
车主手势关键点特征提取脚本骨架

用途：
1. 从车主手势图片数据集中提取 MediaPipe Hands 手部关键点
2. 将关键点保存为 .npz 特征文件
3. 为后续 train_classifier.py 训练分类器提供输入

推荐数据组织方式：
backend/datasets/owner_gesture_hagrid/
├── palm/
│   ├── 001.jpg
│   └── 002.jpg
├── fist/
├── thumb_up/
├── thumb_down/
└── ...

说明：
- 第一阶段建议只训练项目需要的静态手势子集
- 动态手势如左滑、右滑、挥手、画圈建议后续基于连续帧轨迹实现
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
    parser = argparse.ArgumentParser(description="Extract MediaPipe Hands features for owner gesture classification.")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("backend/datasets/owner_gesture_hagrid"),
        help="车主手势数据集根目录，默认按 label/image.jpg 形式组织。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/training/owner_gesture/owner_hand_features.npz"),
        help="输出特征文件路径。",
    )
    parser.add_argument(
        "--label-map",
        type=Path,
        default=Path("backend/models/owner_gesture/label_map.json"),
        help="输出标签映射 JSON。",
    )
    parser.add_argument("--min-detection-confidence", type=float, default=0.5)
    return parser.parse_args()


def collect_image_samples(dataset_dir: Path) -> List[Tuple[Path, str]]:
    samples: List[Tuple[Path, str]] = []

    if not dataset_dir.exists():
        raise FileNotFoundError(f"数据集目录不存在：{dataset_dir.resolve()}")

    for label_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
        label = label_dir.name
        for image_path in sorted(label_dir.rglob("*")):
            if image_path.suffix.lower() in IMAGE_SUFFIXES:
                samples.append((image_path, label))

    return samples


def normalize_hand_landmarks(landmarks) -> np.ndarray:
    """
    将 MediaPipe Hands 的 21 个关键点转换为固定长度特征。

    特征设计：
    - 使用 wrist 关键点作为原点
    - 使用中指 MCP 与 wrist 的距离作为尺度
    - 输出 21 * 3 = 63 维坐标特征
    """
    points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks], dtype=np.float32)

    wrist = points[0]
    middle_mcp = points[9]
    scale = np.linalg.norm(middle_mcp[:2] - wrist[:2])
    scale = max(float(scale), 1e-6)

    normalized = (points - wrist) / scale
    return normalized.reshape(-1).astype(np.float32)


def extract_hand_feature(image_path: Path, hands) -> Optional[np.ndarray]:
    image = cv2.imread(str(image_path))
    if image is None:
        return None

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = hands.process(rgb)

    if not result.multi_hand_landmarks:
        return None

    # 第一阶段只取最清晰的一只手。后续可扩展多手场景。
    landmarks = result.multi_hand_landmarks[0].landmark
    return normalize_hand_landmarks(landmarks)


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

    mp_hands = mp.solutions.hands

    with mp_hands.Hands(
        static_image_mode=True,
        max_num_hands=1,
        model_complexity=1,
        min_detection_confidence=args.min_detection_confidence,
    ) as hands:
        for image_path, label in tqdm(samples, desc="Extracting hand features"):
            feature = extract_hand_feature(image_path, hands)
            if feature is None:
                skipped += 1
                continue

            features.append(feature)
            labels.append(label_to_id[label])
            paths.append(str(image_path))

    if not features:
        raise RuntimeError("没有成功提取到任何手部关键点，请检查数据集图片是否包含清晰手部。")

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
        "note": "MediaPipe Hands 21 landmarks, normalized by wrist and middle_mcp distance.",
    }

    args.label_map.write_text(json.dumps(label_map, ensure_ascii=False, indent=2), encoding="utf-8")

    print("车主手势特征提取完成")
    print(f"有效样本数：{len(y)}")
    print(f"跳过样本数：{skipped}")
    print(f"特征维度：{X.shape[1]}")
    print(f"特征文件：{args.output}")
    print(f"标签映射：{args.label_map}")


if __name__ == "__main__":
    main()
