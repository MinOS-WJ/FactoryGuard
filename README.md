# FactoryGuard 厂区智防平台（开源练习）

FactoryGuard 是一个用于学习 Windows 后台服务、AI 视频分析、事件闭环和可靠性工程的开源练习项目。项目使用虚拟摄像机、模拟 NVR 和本地测试接收器，不面向真实生产部署。

## 学习目标

- 设计一个 Windows 后台服务与可选桌面控制台协作的应用；
- 模拟 RTSP/ONVIF 视频接入、电子围栏、绊线、多帧确认和告警冷却；
- 使用 ONNX Runtime 练习本地 person/car/truck 检测；
- 使用 SQLite、截图和短视频片段组织事件数据；
- 通过故障注入理解进程崩溃、网络中断、磁盘异常、通知失败和恢复流程；
- 用完整设计文档展示需求、架构、测试、安全和可靠性之间的关系。

## 文档

- [立项设计报告](FactoryGuard_DESIGN_REPORT.md)

## 练习技术栈

- Windows 后台服务与可靠性监督器
- Python 3.11+
- PySide6 + Qt 6 QML
- FFmpeg / RTSP
- ONNX Runtime（DirectML/CPU）
- SQLite
- PyInstaller + NSIS
- 虚拟摄像机、模拟 NVR、本地通知接收器

## 重要说明

本项目不要求也不建议直接用于真实厂区安防。文档中的容量、阈值、故障矩阵和恢复策略仅用于学习、模拟测试和设计讨论。

## 分支策略

- `master`：仅保留 README、LICENSE 等基础文件；
- `develop`：日常练习和文档开发分支。

## 许可证

本项目采用 [MIT License](LICENSE)。
