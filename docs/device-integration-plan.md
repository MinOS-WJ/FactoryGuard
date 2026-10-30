# FactoryGuard 标准设备接入改造方案

## 目标

FactoryGuard 是面向小微制造企业的本地视频安全控制台。接入真实摄像机、NVR 和编码器时：

- 设备原生事件和录像能力优先；
- 摄像头或 NVR 不提供所需分析能力时，才启用本机 ONNX 兜底；
- 不增加集中推理服务器、云服务或强制 GPU；
- 保留现有 RTSP、规则、事件审计、通知 Outbox 和本地媒体闭环。

## 接入层级

```text
厂区 Site
└── 区域 Area
    └── 物理设备 VideoDevice
        └── 视频通道 CameraDevice
            └── 视频流 VideoStream
```

旧数据库中的摄像头会在迁移时生成物理设备并关联原通道。旧 RTSP 地址、规则、事件和通知任务继续可用。

## 协议分工

| 协议 | 责任 | 首期能力 |
| --- | --- | --- |
| RTSP/RTP | 通用取流兜底 | FFmpeg 长连接、主/子码流、断流重连 |
| ONVIF | 国际摄像机和 NVR | WS-Discovery、设备信息、GetServices、Media Profile、RTSP URI、PullPoint 原生事件、Profile G 录像查询（设备支持时） |
| GB/T 28181-2022 | 国内设备和 NVR 接入本机平台 | SIP 注册、鉴权、保活、目录、状态、实时点播、报警、BYE；录像回放能力按设备联调结果启用 |
| 本机 ONNX | 设备用不到原生分析时的备用 | CPU ONNX Runtime，按策略和规则需要启动 |

首期 GB/T 28181 角色是“设备/NVR 注册到 FactoryGuard”，不是向上级平台级联。ONVIF Profile S 只作为老设备兼容；新设备优先 Profile T，录像使用 Profile G，原生分析事件使用 Profile M。页面只显示设备实际探测到的能力，不宣称未验证的完整标准认证。

## 统一适配器

`DeviceAdapter` 提供：

- `probe()`
- `get_device_info()`
- `get_capabilities()`
- `list_channels()`
- `get_live_stream()`
- `search_recordings()`
- `get_recording_uri()`
- `subscribe_events()`
- `close()`

适配器分为 `RtspAdapter`、`OnvifAdapter`、`Gb28181Adapter` 和手工地址兜底。协议适配器只做协议通信，不决定业务规则、通知或事件状态。

## 分析策略

设备和通道支持：

- `native_only`：只接收设备原生事件；
- `local_fallback`：原生事件优先，能力缺失或订阅失效时启用本机分析；
- `local_primary`：本机分析为主，设备事件作为补充；
- `disabled`：不进行事件分析。

默认策略为 `local_fallback`。分析决策只在本机分析真正运行时显示检测框；普通预览不展示模型、输入尺寸和置信度。

## 事件与去重

新增 `NativeEvent`，事件记录包含：

- `source`：`onvif`、`gb28181`、`local_inference`、`device_health`；
- `source_event_id`；
- `native`；
- `analysis_policy`；
- `correlation_key`；
- `device_id`。

同一设备事件 ID 只入库一次。原生事件和本机兜底事件在规则的去重窗口内共享 `correlation_key`，只产生一个有效通知，避免设备报警和本机检测重复打扰。设备原生规则使用事件代码映射，原有区域入侵、绊线、过线计数、人数和滞留规则继续使用画面条件。

## 持久化与安全

SQLite 新增物理设备、能力快照、设备凭据引用、订阅状态、录像索引、模型配置和模型绑定。旧库启动时自动补齐字段并生成兼容设备。

设备密码使用 Windows DPAPI 加密，数据库只保存 `credential_ref` 和密文。配置包导出默认不包含密码、令牌或 Webhook/SMTP/SMS 秘密；导入旧配置包后需要重新填写凭据。诊断输出统一脱敏。

## 页面

- 工作台：总览、监控中心、事件中心；
- 网络与设备：设备发现、设备与通道、设备能力、录像检索、协议诊断；
- 通知：通知中心、Webhook、邮件 SMTP、短信通知服务；
- 系统：常规设置、本机备用分析、存储与媒体、资源监控。

设备接入页只填写区域、设备名称、协议、地址和认证。设备能力页按探测结果显示视频、事件、录像、PTZ/IO。录像页优先查询设备/NVR，设备不支持时明确显示不可用。协议诊断页显示注册、订阅、连接和最近错误，不把冗长协议日志放进主界面。

## 实施顺序

1. 保留 RTSP 兼容链路，完成设备模型、SQLite 迁移、DPAPI 凭据和分析策略。
2. 完成 ONVIF 发现、认证、设备/通道、Media Profile、RTSP URI、PullPoint 事件和 Profile G 录像。
3. 完成 GB/T 28181 SIP 注册、目录、状态、报警、实时点播和会话释放。
4. 将监控运行时拆分为预览取流、设备事件和按需本机分析，接入跨来源去重。
5. 完成设备页面、设备事件规则编辑、协议诊断、`tools/device_probe.py` 和现场互操作测试。

## 验收条件

- 手工 RTSP、ONVIF 和 GB/T 28181 设备能在同一拓扑模型中管理；
- 能显示设备、通道、能力快照、在线状态和失败原因；
- 能打开实时视频，设备原生能力正常时不持续运行本机 ONNX；
- 设备缺少所需事件能力时自动启用本机备用分析；
- 原生事件、规则事件、通知 Outbox 和事件处置闭环可追溯；
- 同一语义事件不会产生重复有效通知；
- 配置导出、诊断包和界面不泄漏密码；
- 未通过互操作测试的协议能力不显示为已支持。
