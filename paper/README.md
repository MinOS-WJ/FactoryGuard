# FactoryGuard 文献资料包

整理日期：2026-10-20

本目录只收录可公开下载的开放获取版本。付费墙论文和商业报告不存放全文，只保留 DOI/官方入口，避免绕过版权限制。

## 项目论证报告

- [`centralized-inference-whitepaper.md`](centralized-inference-whitepaper.md)：面向专家和甲方的集中推理架构论证、TCO 模型、可靠性/合规设计、验收指标与证据矩阵。

## 已下载论文

| 文件 | 文献 | 来源 | 与 FactoryGuard 的关系 |
|---|---|---|---|
| `deep-learning-anomaly-detection-survey-2019.pdf` | Chalapathy & Chawla, *Deep Learning for Anomaly Detection: A Survey*, arXiv:1901.03407, 2019 | https://arxiv.org/abs/1901.03407 | 异常检测方法总览，可作为事件识别、误报抑制和后续无监督检测的入口 |
| `single-scene-video-anomaly-survey-2020.pdf` | *A Survey of Single-Scene Video Anomaly Detection*, IEEE TPAMI, 2020, DOI: 10.1109/TPAMI.2020.3040591；预印本 arXiv:2004.05993 | https://arxiv.org/abs/2004.05993 | 单摄像头场景下的视频异常检测综述，适合规则之外的行为异常扩展 |
| `video-anomaly-detection-overview-2018.pdf` | Kiran et al., *An Overview of Deep Learning Based Methods for Unsupervised and Semi-Supervised Anomaly Detection in Videos*, Journal of Imaging, 2018, DOI: 10.3390/jimaging4020036 | https://arxiv.org/abs/1801.04264 | 视频异常检测的监督方式、数据集和评估方法综述 |
| `edge-intelligence-survey-2019.pdf` | Zhou et al., *Edge Intelligence: Paving the Last Mile of Artificial Intelligence With Edge Computing*, Proceedings of the IEEE, 2019, DOI: 10.1109/JPROC.2019.2918951 | https://arxiv.org/abs/1905.01994 | 中心推理、边缘取流、终端轻量化与网络带宽权衡的理论依据 |
| `yolo-architectures-review-2023.pdf` | Terven et al., *A Comprehensive Review of YOLO Architectures in Computer Vision: From YOLOv1 to YOLOv8 and YOLO-NAS*, Machine Learning and Knowledge Extraction, 2023, DOI: 10.3390/make5040083 | https://doi.org/10.3390/make5040083 | YOLO 系列结构、速度和精度权衡，用于模型选型与部署说明 |
| `yolov10-2024.pdf` | *YOLOv10: Real-Time End-to-End Object Detection*, arXiv:2405.14458, 2024 | https://arxiv.org/abs/2405.14458 | 新一代实时检测架构，可作为模型升级与 NMS 优化参考 |
| `yolov11-overview-2024.pdf` | *YOLOv11: An Overview of the Key Architectural Enhancements*, arXiv:2410.17725, 2024 | https://arxiv.org/abs/2410.17725 | 与项目当前 `yolo11n` 模型直接相关的架构说明 |
| `deepsort-2017.pdf` | Wojke et al., *Simple Online and Realtime Tracking with a Deep Association Metric*, arXiv:1703.07402, 2017 | https://arxiv.org/abs/1703.07402 | 多目标跟踪与轨迹 ID 维护的基础方法 |
| `bytetrack-2022.pdf` | Zhang et al., *ByteTrack: Multi-Object Tracking by Associating Every Detection Box*, ECCV 2022, arXiv:2110.06864 | https://arxiv.org/abs/2110.06864 | 低成本、高召回跟踪方案，适合多人同时触发同类告警的场景 |
| `ppe-detection-sustainable-2023.pdf` | *Personal Protective Equipment Detection: A Deep-Learning-Based Sustainable Approach*, Sustainability, 2023, DOI: 10.3390/su151813990 | https://doi.org/10.3390/su151813990 | 安全帽、防护服等 PPE 检测的应用参考 |
| `real-time-ppe-compliance-2023.pdf` | *Real-Time Personal Protective Equipment Compliance Detection Based on Deep Learning Algorithm*, Sustainability, 2023, DOI: 10.3390/su15010391 | https://doi.org/10.3390/su15010391 | 工业现场实时 PPE 合规检测与部署参考 |

## 建议优先阅读顺序

1. `edge-intelligence-survey-2019.pdf`：先确定集中推理、边缘取流和终端轻量化的整体架构。
2. `yolo-architectures-review-2023.pdf` + `yolov11-overview-2024.pdf`：确定模型选型、量化和中心 GPU 容量。
3. `single-scene-video-anomaly-survey-2020.pdf`：评估规则告警之外是否增加行为异常检测。
4. `deepsort-2017.pdf` + `bytetrack-2022.pdf`：完善多人、多轨迹和同类告警独立去重。
5. PPE 两篇：作为工厂安全场景的落地案例和指标参考。

## 付费墙或需机构访问的补充文献

以下文献与项目高度相关，但本次未下载全文：

- *Computer vision for behaviour-based safety in construction: A review and future directions*, Advanced Engineering Informatics, 2020, DOI: 10.1016/j.aei.2019.100980
- *A systematic review of computer vision-based human pose estimation for occupational safety*, International Journal of Industrial Ergonomics, 2026, DOI: 10.1016/j.ergon.2026.103931
- *Spatula: Efficient cross-camera video analytics on large camera networks*, IEEE/ACM Symposium on Edge Computing, 2020, DOI: 10.1109/SEC50012.2020.00016
- *Edge–Cloud Collaborative Real-Time Video Object Detection for Industrial Surveillance Systems*, IEEE Intelligent Systems, 2025, DOI: 10.1109/MIS.2025.3539221
- *A Survey on Video Anomaly Detection*, IEEE DELCON, 2022, DOI: 10.1109/DELCON54057.2022.9753580

## 标准与合规线索

这些标准通常需要从官方或授权渠道获取，不建议使用来源不明的 PDF：

- GB/T 28181-2022《公共安全视频监控联网系统信息传输、交换、控制技术要求》
- GB 35114-2017《公共安全视频监控联网信息安全技术要求》
- GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》
- GB 50348-2018《安全防范工程技术标准》
- ISO 45001:2018《职业健康安全管理体系》
- IEC 62443 系列《工业自动化和控制系统网络安全》
- ISO/IEC 27001:2022《信息安全管理体系》
- 《中华人民共和国个人信息保护法》——视频画面、人脸和员工轨迹处理需重点评估

## 建议写入论文/方案的检索主题

- industrial computer vision worker safety
- video anomaly detection in surveillance
- personal protective equipment detection deep learning
- edge intelligence video analytics
- centralized versus edge inference for multi-camera surveillance
- multi-object tracking for industrial safety
- smart factory occupational safety computer vision
- event-driven notification and transactional outbox
- video surveillance privacy and compliance
