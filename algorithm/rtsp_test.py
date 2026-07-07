import cv2
from pathlib import Path
from datetime import datetime

RTSP_URL = "rtsp://10.126.59.120:8554/live/live3"

output_dir = Path("demo/rtsp_frames")
output_dir.mkdir(parents=True, exist_ok=True)

cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)

if not cap.isOpened():
    print("无法打开视频流:", RTSP_URL)
    raise SystemExit

frame_count = 80
sample_interval = 5
saved_count = 0

for index in range(frame_count):
    ret, frame = cap.read()

    if not ret or frame is None:
        print(f"第 {index} 帧读取失败")
        continue

    if index % sample_interval == 0:
        filename = output_dir / f"rtsp_frame_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{index:03d}.jpg"
        cv2.imwrite(str(filename), frame)
        saved_count += 1
        print("已保存:", filename)

cap.release()

print(f"完成。共读取 {frame_count} 帧，保存 {saved_count} 张抽样帧。")