# FactoryGuard 智守

面向商户店主、物业和安保负责人的多摄像头、多区域智能安防平台。

FactoryGuard 聚焦三件事：

1. 通过 **ONVIF、GB/T 28181、RTSP** 接入摄像头、NVR 或编码器；
2. 用“场所 → 区域 → 摄像头”的层级管理多路视频和设备状态；
3. 通过可选的 **大语言模型文本问询**查询现场情况，或直接用自然语言登记险情。

视觉侧只做视频流解码、设备状态和设备原生告警接入，不加载视觉模型，也不对画面做目标检测。大语言模型仅处理脱敏后的文字运营摘要。

## 核心能力

### 多摄像头与多区域

- 场所、区域、摄像头、码流四级实体管理；
- 同一区域支持多台摄像头，同一摄像头支持主码流、子码流等多路视频；
- 在线、离线、待检测、已停用状态集中展示，并标明最近检测时间；
- 启动 60 秒后自动检测一次，之后每 10 分钟检测，也可随时手动刷新；
- 可暂停摄像头接入进行维护，恢复后重新参与取流和告警；
- 支持通过 ONVIF 自动发现、GB/T 28181 设备注册和手工 RTSP 地址接入；
- 添加摄像头时自动检测重复连接，避免重复取流和重复告警；
- 支持单路实时预览与最多四路同屏查看，超过四路时可自选画面，关闭画面墙统一停止解码；
- 单路预览采用 1280×720 / 8 FPS，四路画面墙采用 960×540 / 4 FPS，兼顾流畅度与多路资源占用；
- 单路预览和四路画面墙中的单格断流后都可原位重连，无需关闭窗口。
- 实时画面只做解码显示，不读取视频帧进行模型分析。

### 现场问询与大模型增强

- 使用本地数据库生成结构化“现场快照”，回答摄像头、区域、设备和险情问题；
- 支持用户说“上报险情：仓库北门有人闯入”，自动创建险情并记录时间、区域和摄像头；
- 无大模型配置时启用本地规则助手，核心问询和险情上报仍可用；
- 配置 OpenAI Chat Completions 兼容接口后，由大模型生成更自然的分析回答；
- 大模型只接收脱敏后的运营快照，不读取设备密码或原始数据库表；
- 大模型服务商、接口地址、模型名称、密钥和超时均可在“系统设置”中配置，并可单独启用或关闭增强。

可直接询问：

- “现在有哪些险情需要处理？”
- “哪些摄像头离线了？”
- “成品仓库现在是什么情况？”
- “总结一下今天的现场安全态势。”
- “上报险情：东门有人翻越围栏，目前仍在现场。”

### 险情闭环

- 险情等级：提示、关注、高危、紧急；
- 处置状态：待处理、处理中、已解决、误报；已解决和误报可经确认后重新打开；
- 支持用户上报、现场问询登记、设备告警和系统发现；
- 展示发生区域、关联摄像头、描述和处置时间；
- 状态变更写入本地审计记录，险情详情直接展示创建、确认、解决、误报和重新打开历史；
- 可按需将新险情异步发送到 HTTPS Webhook、SMTP 邮箱或企业自有短信网关。

## 快速开始

需要 Python 3.11+、Windows 10/11 和 PySide6。视频播放需要 FFmpeg；仓库内若已有 `src/third_party/ffmpeg-*` 会自动发现，也可设置 `FACTORYGUARD_FFMPEG`。

```powershell
conda activate factoryguard
python -m pip install -r requirements.txt
python src/main.py
```

运行测试：

```powershell
ruff check src tests tools
ruff format --check src tests tools
python -m compileall -q src tests tools
python -m pytest -q
```

生成一套多区域演示数据，便于查看完整工作台：

```powershell
python tools/seed_merchant_demo.py --db data/factoryguard.db
python src/main.py
```

## 首次配置

1. 打开“区域与设备”，先创建一个区域，例如“东门”“成品仓库”或“停车场”；
2. 点击“添加摄像头”，选择 ONVIF、GB/T 28181 或 RTSP；
3. 保存后在“实时画面”查看视频；需要同时关注多个位置时点击“多路查看”，超过四路时选择要显示的画面，并点击“刷新设备状态”检查离线设备；
4. 打开“系统设置”，按需配置问询接口或国标服务；
5. 日常通过“工作台”查看安全态势，在“险情中心”完成处置。

## 启动与后台常驻

- 启动时显示带有初始化进度的品牌启动画面；
- 初始化完成后进入商户工作台；
- 同一 Windows 用户只允许运行一个 FactoryGuard 实例，避免重复建立摄像头订阅；
- 关闭主窗口时不会退出，而是隐藏到系统托盘继续后台值守；
- 托盘菜单可打开控制台、上报险情、刷新现场状态或彻底退出；
- 自动化测试可设置 `FACTORYGUARD_DISABLE_TRAY=1` 禁用托盘常驻；
- “系统设置 → 本地运行环境”可一键创建备份 ZIP，并显示备份数量、总大小和最近备份时间；备份同时包含客户 SVG 横幅；
- 恢复备份会先校验数据库和清单，在应用退出后的下一次启动前替换数据，并自动保留恢复前安全备份。

## 品牌与界面

- 顶部始终显示完整的默认品牌横幅，不使用空白占位；
- 在“系统设置 → 顶部品牌横幅”上传商户自己的 SVG，即可整条替换；
- 建议使用 8:1 至 12:1 的横向 SVG，高度可设为 64～160 px；
- 文件上限 5 MB；含脚本或无法解析的 SVG 会被拒绝；
- 点击“恢复默认横幅”可随时回到内置品牌外观；
- 界面采用暖灰、深绿和细边框卡片的商业工作台语言，减少工程和 AI 营销感。

## 大模型配置

支持 OpenAI Chat Completions 兼容接口。可以在界面中填写，也可以通过环境变量注入：

```powershell
$env:FACTORYGUARD_LLM_ENDPOINT = "https://api.openai.com/v1/chat/completions"
$env:FACTORYGUARD_LLM_MODEL = "你的模型名称"
$env:FACTORYGUARD_LLM_API_KEY = "你的密钥"
$env:FACTORYGUARD_LLM_TIMEOUT = "35"
python src/main.py
```

界面配置统一写入 `config/factoryguard.json`，不再保存到 Windows 注册表或业务数据库。可从 [配置示例](config/factoryguard.example.json) 开始手工编辑。当前定位为小规模本地部署，API Key 和密码直接以明文保存在该文件中，请限制文件访问权限并避免随项目分发。未配置大模型时，“本地规则问答”仍可处理设备状态、区域概况和险情登记等核心问题。

配置文件位置也可以通过参数或环境变量指定：

```powershell
$env:FACTORYGUARD_CONFIG = "config/factoryguard.local.json"
python src/main.py gui --config config/factoryguard.local.json
```

## ONVIF 接入

- “区域与设备”页可直接扫描局域网 ONVIF 设备；
- 添加时需要填写 ONVIF 服务地址和设备账号；
- 若设备已经有 RTSP 地址，也可以直接使用 RTSP 方式接入；
- 设备凭据保存在本地 JSON 配置文件中，不进入运营快照或导出文件。

## GB/T 28181 接入

在“系统设置 → GB/T 28181 国标服务”中配置平台编号、SIP 域、SIP 端口和注册密码，然后启用服务。摄像头注册成功后，可将其设备编号绑定到区域。

可选环境变量：

```powershell
$env:FACTORYGUARD_GB_PASSWORD = "SIP 注册密码"
```

国标服务用于设备注册、目录、告警和设备媒体会话；上下级平台级联不在当前产品范围内。

## 架构

```text
PySide6 商户工作台
├── 工作台 / 实时画面 / 现场问询 / 险情中心 / 区域与设备 / 系统设置
├── 现场问询：本地上下文 + 可选大模型适配器
├── 品牌层：默认横幅 + 客户 SVG 定制
├── 设备服务：ONVIF / GB(T) 28181 / RTSP
├── 媒体服务：FFmpeg 解码与实时预览
└── SQLite：拓扑、设备、险情、对话、事件与审计
```

关键技术选择：

| 方面 | 当前实现 |
| --- | --- |
| 桌面 UI | PySide6 Qt Widgets，新商户工作台 |
| 设备接入 | ONVIF、GB/T 28181、RTSP / NVR |
| 实时视频 | FFmpeg 子进程直接解码，不加载本地模型 |
| 现场问询 | 本地运营快照 + 可选 Chat Completions 兼容端点 |
| 本地持久化 | JSON 软件配置 + SQLite 业务数据 + 本地媒体文件 |
| 隐私 | 本地优先；大模型仅接收脱敏运营摘要 |

## 数据与安全

- 视频优先在本机处理；只有在明确配置大模型后，文字化运营摘要才会发送到该接口；
- 运营摘要不包含设备密码、API 密钥或原始视频帧；
- 险情与处置动作保存在本地 SQLite，并写入审计日志；
- 生产环境建议把摄像头与 NVR 放在独立视频 VLAN，不对公网暴露 RTSP / ONVIF 端口。

## 模型边界

- 不安装 ONNX Runtime，不随软件分发视觉模型文件；
- 不提供目标检测、视觉规则、模型绑定或推理配置；
- 可选大语言模型只负责文本问答和险情描述整理；
- 摄像头密码、API Key、原始视频帧不会发送给大语言模型。

## 文档

- [文档中心](docs/README.md)
- [需求分析](docs/requirements/01-requirements-analysis.md)
- [交付答辩材料索引](docs/testing/00-delivery-briefing.md)
- [测试报告](docs/testing/03-test-report.md)
- [黑盒测试报告](docs/testing/04-blackbox-test-report.md)
- [白盒测试报告](docs/testing/05-whitebox-test-report.md)
- [部署手册](docs/operations/01-deployment.md)
- [用户手册](docs/operations/02-user-guide.md)
- [商户控制台产品说明](docs/product/merchant-console.md)
- [工具说明](tools/README.md)
- [多摄像头模拟器](tools/camera_sim/README.md)

## 许可证

本项目采用 [MIT License](LICENSE)。
