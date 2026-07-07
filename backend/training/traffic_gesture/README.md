# 交警手势训练脚本说明

## 脚本

- `extract_pose_features.py`：提取 MediaPipe Pose 人体关键点特征。
- `train_classifier.py`：训练交警手势分类器。

## 推荐流程

```powershell
python backend/training/traffic_gesture/extract_pose_features.py
python backend/training/traffic_gesture/train_classifier.py
```

当前骨架默认支持目录结构：

```text
backend/datasets/traffic_gesture/
├── stop/
├── forward/
├── left_turn/
└── ...
```

如果老师数据集为视频 + CSV 标签，需要后续补充视频逐帧解析逻辑。
