# 车牌训练脚本说明

## 当前脚本

- `collect_sandbox_frames.py`：从沙盘 RTSP 视频流采集训练图片帧。

## 后续计划

1. 使用 `collect_sandbox_frames.py` 采集沙盘图片。
2. 使用标注工具标注每张图片中的所有车牌框。
3. 转换为 YOLO 格式。
4. 使用 YOLOv8n / YOLOv5n 训练沙盘车牌检测模型。
5. 将训练出的模型接入 `backend/algorithm/plate_recognizer.py`。

## 多车牌要求

一张图片中可能有多个车牌，标注时必须全部标注。YOLO 标签示例：

```text
0 0.312 0.452 0.085 0.034
0 0.681 0.477 0.073 0.031
```

每一行代表一个车牌框。
