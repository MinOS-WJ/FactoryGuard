# FactoryGuard 厂区智防平台

FactoryGuard 是一套面向中小型生产企业的轻量级 AI 视频安防软件，与现有 NVR（硬盘录像机）协同工作：NVR 负责连续录像，FactoryGuard 负责实时区域入侵检测、告警和处警闭环。

## 产品目标

- 围墙、仓库、车间、财务室等重点区域有人闯入时实时报警；
- 支持电子围栏、绊线、多帧确认、告警冷却和布防时间表；
- 本地 ONNX Runtime 推理，支持 GPU/CPU；
- 支持企业微信、钉钉群机器人 Webhook 通知；
- 内置虚拟厂区、模拟 NVR 和模拟检测器，便于无真机开发、演示和测试；
- 面向 Windows 值守电脑，支持 8～32 路摄像机的典型小型厂区场景。

## 文档

- [新项目立项设计报告](FactoryGuard_DESIGN_REPORT.md)

## 技术规划（首版）

- Windows 桌面端：PySide6/QML
- 视频处理：FFmpeg、RTSP 子码流、抽帧检测
- AI 推理：ONNX Runtime（DirectML/CPU）
- 事件存储：事件元数据、截图、告警前后 MP4 片段
- 安装交付：PyInstaller + NSIS

## 仓库状态

当前仓库处于项目启动阶段，首先沉淀立项与架构设计，后续将逐步补充模拟环境、领域模型、设备接入、媒体管线、规则引擎和桌面 UI。
