# 车主手势训练脚本说明

## 脚本

- `extract_hand_features.py`：提取 MediaPipe Hands 手部关键点特征。
- `train_classifier.py`：训练车主手势分类器。

## 推荐流程

```powershell
python backend/training/owner_gesture/extract_hand_features.py
python backend/training/owner_gesture/train_classifier.py
```

当前骨架默认支持目录结构：

```text
backend/datasets/owner_gesture_hagrid/
├── palm/
├── fist/
├── thumb_up/
└── ...
```

第一阶段优先训练静态手势。动态手势后续通过连续帧轨迹或视频动作模型扩展。
