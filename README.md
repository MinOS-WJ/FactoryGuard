# FactoryGuard 厂区智防平台

面向中小制造企业厂区的本地视频智能检测与处警闭环平台。系统接入现有 NVR 的 RTSP 子码流，在普通 Windows 电脑上完成实时目标检测、区域入侵判定、事件取证与通知闭环；NVR 继续负责 7×24 连续录像。

## 定位与规模

| 维度 | 说明 |
| --- | --- |
| 目标场景 | 小公司厂区：围墙、仓库、装卸区、车间入口、配电房等关键区域 |
| 典型规模 | 8～32 路摄像机、1～2 台 NVR、1 台 Windows 值守电脑 |
| 运行方式 | 核心能力以后台 Windows 服务运行；桌面界面为可选控制台，关闭界面不影响检测 |
| 硬件要求 | 普通 Windows 10 / 11 x64 电脑；GPU 可选（DirectML），无独显时降帧运行 |
| 数据位置 | 检测、事件与截图片段全部在本机处理与保存，不依赖云端 |

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 设备接入 | NVR 通道批量接入、主/子码流配置、能力探测、通道状态管理 |
| 实时检测 | 本地 ONNX 推理（DirectML 优先、CPU 兜底），目标级检测 |
| 规则判定 | 多边形电子围栏、绊线、方向判定、布防时间表、多帧确认与冷却去重 |
| 事件闭环 | 事件中心、截图、事件短片段、NVR 精确定位、状态流转与误报反馈 |
| 通知联动 | Webhook（企业微信/微信/钉钉/飞书/自定义预留）、SMTP 邮件、自定义 HTTP 短信；支持多通道 Outbox、重试与离线补偿 |
| 运行可靠性 | Windows 服务、Job Object、看门狗、崩溃自愈、GPU/CPU 降级 |
| 可观测性 | 健康监测、结构化日志、一键诊断包、操作审计 |
| 模拟环境 | 模拟 NVR、虚拟点位、场景剧本、异常注入与自动断言 |

## 与 NVR 的职责划分

| 能力 | NVR | FactoryGuard |
| --- | --- | --- |
| 7×24 连续录像 | 负责（权威） | 不负责 |
| 视频流提供 | 提供 RTSP 主/子码流 | 消费子码流 |
| 实时智能检测 | — | 负责 |
| 告警与通知 | 部分设备内置 | 负责 |
| 事件取证 | 提供完整录像回溯 | 事件短片段 + NVR 精确定位 |

首版取流采用 RTSP 子码流；GB/T 28181 仅作为后续平台级联与对外对接的预留方向。

## 架构与关键技术

整体为模块化单体 + 端口适配器分层：领域层不依赖界面框架与数据库，外部依赖（RTSP、推理引擎、通知渠道、未来国标网关）均以适配接口接入，模拟环境与真实设备共用同一套接口。

| 方面 | 选择 |
| --- | --- |
| 服务宿主 | Windows 服务 + 监督器 + Job Object，子进程按通道隔离 |
| 桌面端 | Qt 6 + QML，独立进程，通过命名管道与服务通信 |
| 媒体管线 | 每路独立 FFmpeg 子进程，RTSP over TCP，有界帧缓冲 |
| 推理 | ONNX Runtime，DirectML 优先、CPU 兜底，可替换引擎 |
| 持久化 | SQLite（WAL）+ 本地文件，事务性发件箱保证事件与通知不丢 |
| 本地通信 | Windows 命名管道 + JSON Lines，ACL 控制访问 |
| 密钥保护 | Windows DPAPI，凭据与 Webhook Secret 不明文落盘 |
| 部署 | 安装包 + 服务注册，支持无人登录自启、升级与回滚 |

## 文档

- [FactoryGuard_DESIGN_REPORT.md](FactoryGuard_DESIGN_REPORT.md)：行业市场调研结果与软件系统设计（系统目标与边界、功能与非功能需求、总体架构、模块设计、核心算法、数据设计、接口与消息、界面设计、模拟环境、安全设计、运维设计、测试与验收）。

## 开发环境与首个可运行切片

项目使用传统的 `requirements.txt` 依赖文件；其中是 CPU 版 `onnxruntime`，不要在无 GPU 主机上安装 `onnxruntime-gpu`。

```powershell
conda activate factoryguard
python -m pip install -r requirements.txt
python -m pytest
python src/main.py
python src/main.py --help
python src/main.py simulate --frames 6
python src/main.py detect-rtsp --url rtsp://localhost/live
python src/main.py probe-rtsp --url rtsp://localhost/live
python src/main.py monitor-rtsp --url rtsp://localhost/live --frames 3
python src/main.py cleanup-media --db data/factoryguard.db --retention-days 30 --dry-run
python src/main.py dispatch-outbox --db data/factoryguard.db --webhook-url https://example.test/factoryguard --webhook-secret "$env:FACTORYGUARD_WEBHOOK_SECRET"
python src/main.py dispatch-outbox --db data/factoryguard.db --email-host smtp.example.com --email-port 587 --email-username "$env:FACTORYGUARD_EMAIL_USERNAME" --email-password "$env:FACTORYGUARD_EMAIL_PASSWORD" --email-sender factoryguard@example.com --email-recipients "ops@example.com;security@example.com" --email-security starttls
python src/main.py dispatch-outbox --db data/factoryguard.db --sms-endpoint https://sms.example.com/send --sms-token "$env:FACTORYGUARD_SMS_TOKEN" --sms-sender FactoryGuard --sms-template-id event-alert --sms-recipients "13800138000;13900139000"
python src/main.py dispatch-outbox --watch --interval 5 --db data/factoryguard.db --webhook-url https://example.test/factoryguard --webhook-secret "$env:FACTORYGUARD_WEBHOOK_SECRET"
python src/main.py gui --url rtsp://localhost/live
python src/main.py tray --url rtsp://localhost/live
```

直接运行 `python src/main.py` 会打开 Qt 控制台；关闭窗口会隐藏到系统托盘，监控 worker 继续运行。`gui` 是同样的显式启动方式，`tray` 直接以托盘模式启动。桌面端使用 `requirements.txt` 中的 PySide6，推理仍固定为 CPU `onnxruntime`。
项目不需要安装为 Python 包，直接运行 `python src/main.py` 即可启动。

控制台提供实时预览：监控线程把原始画面和 YOLO 检测框送到预览区，规则事件会把事件前的短帧缓存编码为 MP4，并同时保存 PNG 快照到 `data/events-media`。事件记录支持关键字、级别、推流实体和最近时间范围筛选；选中记录后可双击或点击“查看回放媒体”回放，并可切换浅色、深色和海蓝主题，主题选择会保存在本机设置中。

桌面端采用系统级壳层，左侧导航按“工作台、网络与设备、系统”分组，拆分为 13 个独立页面：总览、监控中心、拓扑与设备、事件审计、系统审计、通知中心、常规设置、集中推理、Webhook 通道、邮件 SMTP、短信通知服务、存储与媒体、健康诊断。通道开关、凭据、调度队列、备份和诊断分别落在对应页面，避免把运维操作集中在一个超长设置页。拓扑页展示 `Site → Area → CameraDevice → VideoStream` 的嵌套树和推流清单；点击树节点或清单中的任一路推流，会把对应 RTSP 地址、模型和画面参数带入监控中心。监控中心同时提供单路检测框主画面和按推流动态生成的 1/4/9/16 路多宫格预览；未选择具体推流时，监控 worker 会按拓扑中的全部启用推流并行运行。

面向专家和甲方的集中推理架构论证、TCO 模型、部署边界与文献依据见 [`paper/centralized-inference-whitepaper.md`](paper/centralized-inference-whitepaper.md)。

“Webhook 通道”页提供企业微信群机器人、微信服务号/企业微信应用、钉钉机器人、飞书机器人和自定义 HTTPS Webhook 五个服务商入口。厂商专用消息模板与返回值校验作为适配扩展点预留；当前统一使用 FactoryGuard 通用 HTTPS Webhook 协议和 HMAC 签名。“邮件 SMTP”页提供 163、139、QQ、Outlook、Google 和自定义 SMTP 预设，切换预设会自动填充服务器、端口和加密方式。

Webhook、SMTP 和短信页面都支持同时维护多个同类实例，可新增、复制、删除和分别启用。每个实例拥有独立的 Outbox 通道键（例如 `webhook:gate-a`、`email:ops`、`sms:night-shift`），同一事件会为每个启用实例分别生成投递任务；某个实例失败不会覆盖或阻塞其他实例。密钥、密码和 Token 仍只保留在当前进程，软件重启后需要重新输入。

拓扑页支持在软件内新增厂区、嵌套区域、摄像头和主/子/分析推流，保存后立即写入 SQLite 并刷新总览。事件审计页支持按状态筛选，并可将事件置为“已确认、误报、已关闭”；每次处置都会同步更新事件载荷并写入审计日志。运行期间每一路推流独立报告启动、运行、降级、离线和停止状态，拓扑清单会实时反映具体推流的健康状态。

拓扑页支持批量导入和导出 UTF-8 CSV。CSV 一行对应一路推流，同时携带厂区、嵌套区域、摄像头、推流角色、分辨率、帧率、模型和启用状态；没有推流的摄像头、没有摄像头的区域也可以作为空配置导入。

拓扑实体支持启用/停用生命周期管理。停用厂区、区域、摄像头或推流后，Worker 会沿父子层级过滤对应视频流，保留配置和历史事件；重新启用后即可恢复纳管，所有状态变更写入审计日志。

每一路推流的健康状态会持久化到 SQLite，记录最近状态、脱敏原因、最近成功时间和重连次数。软件重启后仍可在拓扑和诊断页面查看上次运行结果。

“健康诊断”页提供健康快照和诊断导出，包含数据库完整性、待发送通知、磁盘容量、实体数量和各推流状态；诊断 JSON 写入数据库目录下的 `diagnostics`，导出前会脱敏 RTSP 用户名、密码、Webhook Secret、SMTP 密码和短信令牌。

“存储与媒体”页提供 SQLite 在线备份。备份使用 SQLite backup API 和临时文件原子替换，适合在监控运行期间生成可校验的配置与事件快照。

“存储与媒体”页提供媒体保留策略和“清理过期媒体”操作；命令行可使用 `cleanup-media` 预览或执行清理。清理只处理数据库记录过的媒体文件，事件、通知 Outbox 和审计历史始终保留。

“存储与媒体”页支持导出和导入 JSON 配置包，内容包括厂区拓扑、推流、规则、布防状态和时间表；Webhook Secret、SMTP 密码、短信令牌不会导出，RTSP URL 中的用户名和密码也会脱敏。导入前必须停止监控，导入会替换配置表并保留事件、Outbox 和审计历史。

“短信通知服务”页提供自定义 HTTP 短信网关，支持 Bearer Token、签名/发送方、模板 ID 和手机号列表。阿里云、腾讯云、华为云作为专用 API 适配入口预留，正式启用前需要补充对应云厂商签名算法和模板审核参数。

规则配置也持久化在 SQLite 的 `detection_rule` 表中。Worker 和 `monitor-rtsp` 启动时会优先加载对应推流的启用规则；没有配置时才创建并保存全画面人员检测规则。拓扑页可为当前推流新增或编辑规则，配置多边形/绊线坐标、目标类别、置信度、确认帧、冷却时间和方向，并启用或停用选中规则；保存前会执行领域几何校验。

每路推流还拥有独立的布防状态，保存在 SQLite 的 `stream_arming` 表中。拓扑页可以对选中推流布防或撤防；Worker 会周期性读取状态，撤防期间仍保留视频预览和检测统计，但规则不会生成正式事件，状态变更会写入审计日志。

布防时间表保存在 SQLite 的 `arming_schedule` 表中，支持按周设置多个时间段、跨午夜时段、节假日和临时布防/撤防覆盖，并可选择本机、UTC 或 IANA 时区（例如 `Asia/Shanghai`）。拓扑页的“时间表”入口保存后，Worker 和 CLI 会按“手动布防状态 × 时间表”计算最终是否允许产生事件。

事件审计页还支持导出当前筛选结果为 Excel 兼容的 UTF-8 CSV，以及导出最近审计日志为 JSON；文件默认写入数据库目录下的 `exports`。

规则引擎按“规则 + 目标轨迹”维护候选、确认和冷却状态。同一条规则在同一帧出现多个目标时，会为每个目标分别生成事件和去重键；一个目标进入冷却不会影响其他同类目标，也不会影响其他通知实例的独立投递。

`dispatch-outbox` 是独立的通知投递命令：它以租约方式领取待发送 Outbox，可调用 HTTPS Webhook、SMTP 邮件或自定义 HTTP 短信网关，成功后标记为 sent，失败则按退避时间重新进入 retry。Webhook Secret、SMTP 密码和短信 Token 只通过命令参数或环境变量传入，不写入数据库。SMTP 支持 STARTTLS、SSL/TLS 和明文三种模式；邮件收件人和手机号可用逗号、分号或换行分隔。

追加 `--watch` 后，命令会作为常驻投递进程持续轮询，适合交给 Windows 任务计划、服务包装器或其他进程监督器运行；按 `Ctrl+C` 会安全退出并释放数据库连接。

桌面端也可以在监控启动后自动运行 Outbox 调度器。Webhook 可使用 `--webhook-url`、`--webhook-secret` 或 `FACTORYGUARD_WEBHOOK_*` 环境变量；SMTP 可使用 `--email-host`、`--email-port`、`--email-username`、`--email-password`、`--email-sender`、`--email-recipients`、`--email-security`，也支持对应的 `FACTORYGUARD_EMAIL_*` 环境变量；短信命令行投递支持 `--sms-endpoint`、`--sms-token`、`--sms-sender`、`--sms-template-id`、`--sms-recipients` 和 `FACTORYGUARD_SMS_*`。“通知中心”页可以调整轮询间隔、单批数量、刷新队列、立即发送失败重试项；“Webhook 通道”“邮件 SMTP”和“短信通知服务”页分别维护通道开关与凭据。调度器使用独立 SQLite 连接和发送租约，不会阻塞实时视频线程；关闭监控时会安全停止，未发送记录会保留给下一次启动或 `dispatch-outbox` 命令继续处理。

标准 Conda 环境位于 Miniconda 的环境目录中，可在 `conda env list` 看到。首次创建可以使用：

```powershell
$condaBase = conda info --base
conda create --prefix "$condaBase\envs\factoryguard" python=3.13 pip
conda activate factoryguard
python -m pip install -r requirements.txt
python -m pytest
python src/main.py simulate
```

当前首个切片覆盖：纯领域几何校验、多帧确认与冷却去重、CPU/模拟推理接口、Event + Notification Outbox SQLite 事务、HTTPS Webhook 签名适配，以及不依赖摄像头/NVR 的确定性模拟链路。模拟命令应输出 `events: 1`、`pending_notifications: 1` 和 `integrity: "ok"`。

仓库中提供的 `src/third_party/ffmpeg-9.0.2-full_build` 会被自动发现。也可以通过 `FACTORYGUARD_FFMPEG` 指定其他 `ffmpeg.exe`；媒体适配层始终以参数数组启动进程，并强制 RTSP over TCP。

`detect-rtsp` 默认读取 `rtsp://localhost/live`、1280×720 的源，并使用 `models/yolo11n.onnx` 的 CPU 推理；可通过 `--frames`、`--confidence` 和 `--model` 调整测试参数。

`monitor-rtsp` 在有限帧模式下继续执行“RTSP → FFmpeg → YOLO CPU → 多帧规则 → Event → SQLite Outbox”，适合冒烟测试和后续接入 Windows Service；默认规则是 CH01 全画面人员检测，连续 3 帧确认后生成事件。

拓扑页选中推流后可以点击“测试连接”，系统会在独立线程中调用 FFmpeg 获取首帧并返回分辨率或具体错误。命令行也可以使用 `probe-rtsp` 做同样的单帧连通性检查。

## 适用范围

- 当前仓库已包含首个可运行的领域核心、模拟链路、RTSP/FFmpeg/ONNX CPU 冒烟测试、有限帧事件闭环和 Qt 控制台托盘原型；Windows 服务、生产级流进程恢复和安装包仍按设计报告后续迭代。
- 模拟环境（模拟 NVR + 虚拟视频源 + 模拟检测器）是系统的正式组成部分，可在无真实摄像机与 NVR 的条件下跑通"取流 → 检测 → 规则 → 事件 → 通知 → 处置"全链路。
- 报告中的容量、阈值、故障矩阵与恢复策略为设计基线，落地时需按实际设备、画面条件与现场网络校准。
- 运行环境需满足报告第 12.3 节的环境基线；NVR 与值守主机应处于独立网络，不对公网暴露视频端口。

## 分支策略

- `master`：稳定分支，对外提供完整文档（含设计报告），可直接查阅；
- `develop`：日常迭代分支，改动先提交至此，稳定后再合入 `master`。

## 许可证

本项目采用 [MIT License](LICENSE)。
## 业务实体与多路推流

运行时按物理层级组织资源，不再把一个摄像头或一个数字通道当成系统边界：

Site（厂区）
└── Area（区域，可继续嵌套）
    └── CameraDevice（摄像头）
        ├── VideoStream(main，主码流)
        ├── VideoStream(sub，子码流)
        └── VideoStream(analysis，分析码流)

factoryguard.domain.Topology 负责校验父子关系，并可通过 stream_id 解析完整的 site → area → camera → stream 路径。Frame、Rule 和 Event 都携带这四级身份；旧的 channel_id 仍作为兼容别名保留。SQLite 会保存 site、area、camera_device、video_stream 实体表，事件表同时保存实体外键和完整路径载荷，旧数据库启动时会自动补齐新增列。

Qt Worker 按每个启用的 VideoStream 启动独立的 RTSP、YOLO、规则和事件链路。控制台左侧的实体树可以选择具体推流；不选择时会按数据库中的全部启用推流运行。传统单路参数仍然有效，例如：

python src/main.py monitor-rtsp --stream-id stream-main --site-id site-1 --area-id area-2 --camera-id camera-1

`monitor-rtsp` 也支持重复传入 `--stream STREAM_ID=URL` 运行多路推流；传入 `--topology-db` 时会自动读取数据库中的全部启用 VideoStream。
