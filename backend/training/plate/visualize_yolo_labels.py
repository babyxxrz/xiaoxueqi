"""
Visualize YOLO labels for sandbox plate dataset.

Example:
    python backend/training/plate/visualize_yolo_labels.py \
        --dataset-dir backend/datasets/plate_sandbox_yolo \
        --split train \
        --max-images 30
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def list_images(images_dir: Path) -> list[Path]:
    return sorted([p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS])


def parse_label_file(label_path: Path) -> list[tuple[int, float, float, float, float]]:
    labels = []
    if not label_path.exists():
        return labels
    for line in label_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            class_id = int(float(parts[0]))
            x, y, w, h = map(float, parts[1:5])
            labels.append((class_id, x, y, w, h))
        except ValueError:
            continue
    return labels


def draw_labels(image_path: Path, label_path: Path, output_path: Path) -> None:
    image = cv2.imread(str(image_path))
    if image is None:
        print(f"跳过无法读取的图片：{image_path}")
        return

    h, w = image.shape[:2]
    labels = parse_label_file(label_path)

    for class_id, x, y, bw, bh in labels:
        xmin = int((x - bw / 2) * w)
        ymin = int((y - bh / 2) * h)
        xmax = int((x + bw / 2) * w)
        ymax = int((y + bh / 2) * h)
        xmin = max(0, min(xmin, w - 1))
        xmax = max(0, min(xmax, w - 1))
        ymin = max(0, min(ymin, h - 1))
        ymax = max(0, min(ymax, h - 1))

        cv2.rectangle(image, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)
        cv2.putText(
            image,
            f"plate:{class_id}",
            (xmin, max(ymin - 5, 15)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), image)


def main() -> int:
    parser = argparse.ArgumentParser(description="Visualize YOLO plate annotations.")
    parser.add_argument("--dataset-dir", default="backend/datasets/plate_sandbox_yolo", help="YOLO 数据集目录。")
    parser.add_argument("--split", default="train", choices=["train", "val", "test"], help="数据划分。")
    parser.add_argument("--max-images", type=int, default=30, help="最多可视化多少张。")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default=None, help="预览图输出目录。默认 dataset/preview/split。")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    images_dir = dataset_dir / "images" / args.split
    labels_dir = dataset_dir / "labels" / args.split
    output_dir = Path(args.output_dir) if args.output_dir else dataset_dir / "preview" / args.split

    if not images_dir.exists():
        raise FileNotFoundError(f"图片目录不存在：{images_dir}")

    images = list_images(images_dir)
    if not images:
        print(f"未找到图片：{images_dir}")
        return 1

    rng = random.Random(args.seed)
    rng.shuffle(images)
    selected = images[: args.max_images]

    for image_path in selected:
        label_path = labels_dir / f"{image_path.stem}.txt"
        output_path = output_dir / image_path.name
        draw_labels(image_path, label_path, output_path)
        print(f"已生成预览：{output_path}")

    print(f"完成。预览目录：{output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
