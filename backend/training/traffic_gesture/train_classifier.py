"""
交警手势分类模型训练脚本骨架

输入：
- extract_pose_features.py 生成的 .npz 文件

输出：
- backend/models/traffic_gesture/traffic_gesture_classifier.pkl
- backend/models/traffic_gesture/metrics.json

说明：
- 第一阶段使用 RandomForestClassifier，训练快、部署简单、容易解释
- 后续可替换为 MLP、SVM 或 PyTorch 模型
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train traffic gesture classifier from pose features.")
    parser.add_argument(
        "--features",
        type=Path,
        default=Path("backend/training/traffic_gesture/traffic_pose_features.npz"),
        help="特征 npz 文件。",
    )
    parser.add_argument(
        "--label-map",
        type=Path,
        default=Path("backend/models/traffic_gesture/label_map.json"),
        help="标签映射 JSON。",
    )
    parser.add_argument(
        "--model-output",
        type=Path,
        default=Path("backend/models/traffic_gesture/traffic_gesture_classifier.pkl"),
        help="模型输出路径。",
    )
    parser.add_argument(
        "--metrics-output",
        type=Path,
        default=Path("backend/models/traffic_gesture/metrics.json"),
        help="评估指标输出路径。",
    )
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--n-estimators", type=int, default=300)
    return parser.parse_args()


def safe_train_test_split(X: np.ndarray, y: np.ndarray, test_size: float, random_state: int):
    """样本少或类别样本不足时，自动取消 stratify，避免报错。"""
    unique, counts = np.unique(y, return_counts=True)
    can_stratify = len(unique) > 1 and np.all(counts >= 2)

    return train_test_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
        stratify=y if can_stratify else None,
    )


def main() -> None:
    args = parse_args()

    if not args.features.exists():
        raise FileNotFoundError(f"特征文件不存在：{args.features}")

    data = np.load(args.features, allow_pickle=True)
    X = data["X"]
    y = data["y"]

    if len(set(y.tolist())) < 2:
        raise RuntimeError("至少需要两个类别才能训练分类模型。")

    label_map: Dict[str, Any] = {}
    if args.label_map.exists():
        label_map = json.loads(args.label_map.read_text(encoding="utf-8"))

    X_train, X_test, y_train, y_test = safe_train_test_split(
        X, y, test_size=args.test_size, random_state=args.random_state
    )

    model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        random_state=args.random_state,
        class_weight="balanced",
        n_jobs=-1,
    )

    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro", zero_division=0)

    id_to_label = label_map.get("id_to_label", {})
    target_names = [id_to_label.get(str(i), str(i)) for i in sorted(set(y.tolist()))]

    report = classification_report(
        y_test,
        y_pred,
        labels=sorted(set(y.tolist())),
        target_names=target_names if len(target_names) == len(set(y.tolist())) else None,
        output_dict=True,
        zero_division=0,
    )

    metrics = {
        "task_type": "traffic_gesture",
        "model_type": "RandomForestClassifier",
        "sample_count": int(len(y)),
        "train_count": int(len(y_train)),
        "test_count": int(len(y_test)),
        "feature_dim": int(X.shape[1]),
        "accuracy": float(acc),
        "macro_f1": float(macro_f1),
        "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        "classification_report": report,
    }

    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, args.model_output)
    args.metrics_output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print("交警手势模型训练完成")
    print(f"Accuracy：{acc:.4f}")
    print(f"Macro F1：{macro_f1:.4f}")
    print(f"模型文件：{args.model_output}")
    print(f"指标文件：{args.metrics_output}")


if __name__ == "__main__":
    main()
