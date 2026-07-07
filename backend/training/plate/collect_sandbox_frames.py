"""
沙盘车牌训练帧采集脚本骨架

用途：
1. 从老师沙盘 RTSP 视频流中自动采集图片帧
2. 保存到 backend/datasets/plate_sandbox/images
3. 后续人工标注每张图片中的多个车牌 bbox
4. 用于训练沙盘车牌检测模型

注意：
- 本脚本只负责采集图片，不负责训练 YOLO
- 一张图中可能有多个车牌，后续标注时必须全部标注
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect frames from RTSP stream for sandbox plate dataset.")
    parser.add_argument(
        "--rtsp-url",
        type=str,
        required=True,
        help="RTSP 视频流地址，例如 rtsp://10.126.59.120:8554/live/live12",
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default="live12",
        help="视频源编号，用于生成文件名。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("backend/datasets/plate_sandbox/images"),
        help="图片输出目录。",
    )
    parser.add_argument(
        "--frame-count",
        type=int,
        default=300,
        help="读取总帧数。",
    )
    parser.add_argument(
        "--sample-interval",
        type=int,
        default=10,
        help="每隔多少帧保存一张。",
    )
    parser.add_argument(
        "--warmup-frames",
        type=int,
        default=10,
        help="开始采集前跳过的预热帧数。",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="是否显示采集画面。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.rtsp_url, cv2.CAP_FFMPEG)

    if not cap.isOpened():
        raise RuntimeError(f"无法打开 RTSP 视频流：{args.rtsp_url}")

    saved = 0
    read_count = 0

    print("开始采集沙盘车牌训练帧")
    print(f"RTSP：{args.rtsp_url}")
    print(f"输出目录：{args.output_dir}")

    for idx in range(args.frame_count + args.warmup_frames):
        ret, frame = cap.read()
        if not ret or frame is None:
            print(f"第 {idx} 帧读取失败")
            time.sleep(0.1)
            continue

        if idx < args.warmup_frames:
            continue

        read_count += 1
        effective_idx = idx - args.warmup_frames

        if effective_idx % args.sample_interval == 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{args.source_id}_{timestamp}_{effective_idx:06d}.jpg"
            output_path = args.output_dir / filename
            cv2.imwrite(str(output_path), frame)
            saved += 1
            print(f"已保存：{output_path}")

        if args.display:
            cv2.imshow("Collect Sandbox Plate Frames", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    if args.display:
        cv2.destroyAllWindows()

    print("采集完成")
    print(f"读取有效帧数：{read_count}")
    print(f"保存图片数：{saved}")
    print("后续步骤：使用标注工具为每张图片标注所有车牌框，并转换为 YOLO 格式。")


if __name__ == "__main__":
    main()
