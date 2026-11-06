# 系统架构

## 1. 架构概览

```text
src/main.py
└── factoryguard.cli
    └── MerchantMainWindow
        ├── 工作台 / 实时画面 / 现场问询 / 险情中心
        ├── 区域与设备 / 系统设置
        ├── FactoryAssistant
        │   ├── 本地规则问答
        │   └── OpenAI Chat Completions 兼容客户端
        ├── DeviceManager
        │   ├── ONVIF
        │   ├── GB/T 28181
        │   └── RTSP
        ├── FFmpeg 媒体预览
        ├── BrandBanner / MerchantTheme（默认与客户 SVG 横幅）
        ├── AppConfigStore（JSON 软件配置）
        └── SQLiteStore（业务数据）

tools/camera_sim
├── ONVIF HTTP + WS-Discovery
├── GB/T 28181 SIP 模拟
└── 多客户端 RTSP / RTP 测试画面
```

产品采用单机进程架构。界面、后台值守线程、设备服务和本地存储运行在同一台电脑上，不依赖云端数据库。

## 2. 分层职责

| 层 | 主要模块 | 职责 |
| --- | --- | --- |
| 启动层 | `cli.py`、`merchant_console.py` | 参数解析、启动画面、主窗口、系统托盘 |
| 展示层 | `merchant_widgets.py`、`merchant_theme.py` | 页面、卡片、状态、默认/客户 SVG 横幅、样式和交互 |
| 领域层 | `domain/models.py`、`incidents.py`、`topology.py` | 设备、区域、险情、事件和实体关系 |
| 智能问询 | `agent/context.py`、`service.py`、`llm.py` | 本地快照、规则问答、大模型适配 |
| 设备层 | `devices/`、`DeviceManager` | 标准协议适配、探测、通道、事件 |
| 媒体层 | `media/ffmpeg.py`、`media/rtsp.py` | RTSP 解码、首帧探测、实时预览 |
| 通知层 | `notifications/incidents.py`、`adapters.py` | Webhook、邮件和短信投递，失败隔离 |
| 存储层 | `storage/sqlite.py`、`config.py` | 业务数据、软件配置、一次性迁移 |
| 安全层 | `security/credentials.py` | 兼容旧凭据迁移；新配置使用明文 JSON |

## 3. 配置与业务数据边界

### 软件配置

路径：`config/factoryguard.json`

- LLM 服务商、地址、模型、Key、超时、启停；
- GB/T 28181 平台编号、域、端口、密码、启停；
- 摄像头、NVR 和编码器凭据；
- 品牌 SVG 相对路径与横幅高度；
- 后续可扩展界面偏好和启动策略。

### 业务数据

路径：`data/factoryguard.db`

- 场所、区域、设备、摄像头、码流；
- 险情、对话、设备事件和审计；
- 设备健康、订阅状态和录像索引。

配置不得写入数据库；业务数据不得写入软件配置文件。

## 4. 启动流程

1. CLI 解析 `--config`、业务数据库路径和桌面模式；
2. 显示品牌启动画面；
3. 读取或创建 JSON 配置；
4. 执行一次性的旧注册表/数据库凭据迁移；
5. 初始化业务数据库和实体拓扑；
6. 构建主界面、加载险情与问询历史；
7. 恢复已启用的 GB/T 28181 服务；
8. 创建系统托盘图标；
9. 关闭启动画面并显示工作台，或以 `tray` 模式隐藏到托盘。

## 5. 后台值守

- 主窗口关闭后默认隐藏到托盘，不销毁进程；
- 20 秒周期刷新设备状态、险情和摘要；
- GB/T 28181 SIP 服务在独立后台线程运行；
- 实时视频、设备探测、ONVIF PullPoint、国标解析和大模型请求使用独立线程；
- ONVIF 订阅启动前先探测事件能力，失败状态写入设备订阅表并显示在摄像头提示中；
- 托盘“退出 FactoryGuard”才执行完整清理。

## 6. 线程模型

| 工作 | 线程模型 | 清理策略 |
| --- | --- | --- |
| 实时预览 | 一次性 `QThread` 子类 | 自然结束后清空引用；关闭时等待断开 |
| 设备健康检测 | `QObject` + `QThread` | `request_stop` 后延迟 `quit` |
| ONVIF 码流解析 | `QObject` + `QThread` | 完成后关闭设备适配器 |
| 大模型请求 | `QObject` + `QThread` | 超时或完成后切回主线程 |
| 险情通知 | 每次投递 `QObject` + `QThread` | 后台发送，失败不影响险情落库 |
| GB/T 28181 SIP | asyncio 后台线程 | `DeviceManager.close()` 停止事件循环 |
| ONVIF PullPoint | 每设备独立 Python 线程 | 停止订阅、关闭适配器与本地数据库 |

所有线程关闭操作必须幂等，且不得从已经删除的 PySide6 包装器上继续调用 C++ 方法。

## 7. 文字问询设计

### 本地快照

`build_operational_context()` 从业务数据库读取：

- 场所、区域、摄像头与码流；
- 设备健康与订阅状态；
- 未关闭险情；
- 最近 24 小时设备事件。

快照不包含配置文件中的密码、API Key 或原始视频帧。

### 回答流程

1. 识别用户问题是否包含险情登记意图；
2. 需要登记时先在本地创建 Incident；
3. 生成本地规则回答作为兜底；
4. 若大模型启用且连接完整，则在后台线程请求回答；
5. 大模型失败时使用本地回答，不阻断险情处置；
6. 用户与助手消息写入本地业务数据库。

## 8. 可观测与故障隔离

- 配置错误在启动时明确提示，不覆盖损坏文件；
- ONVIF、GB/T 28181、RTSP 单设备失败不影响其他设备；
- 媒体线程、设备线程和 LLM 线程各自隔离；
- 状态栏显示刷新和连接结果；
- 审计记录保存配置、险情和处置变更。

## 9. 品牌横幅与界面主题

- 未配置客户横幅时，`BrandBanner` 绘制默认品牌区，保证顶部结构完整；
- 客户上传 SVG 后，SVG 以保持宽高比并裁切铺满的方式替换整条顶部横幅；
- 装载前检查文件存在、大小上限、SVG 根节点及脚本内容；
- 横幅文件统一复制到配置文件同级的 `branding/banner.svg`，配置文件只保存相对路径；
- 主题采用暖灰背景、深绿强调和细边框卡片的商业工作台语言，不展示算法工程概念或营销式 AI 装饰。

## 10. 测试与质量门禁

- `tests/test_camera_simulator.py` 覆盖 ONVIF、GB/T 28181 和 RTSP 模拟链路；
- `tests/test_merchant_console.py` 覆盖默认横幅、客户 SVG、非法 SVG 和配置持久化；
- `ruff check src tests tools` 作为静态检查门禁；
- `ruff format --check src tests tools` 作为格式门禁；
- `python -m compileall -q src tests tools` 与 `python -m pytest -q` 作为最终回归门禁。
