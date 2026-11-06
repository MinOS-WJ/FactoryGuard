# 黑盒测试报告

> 面向交付验收。只从"一个真实使用者能看到什么"的角度验证产品，不引用任何内部实现。
> 配套视频与截图见 [`evidence/`](evidence/)。

## 1. 报告信息

| 项目 | 内容 |
| --- | --- |
| 产品 | FactoryGuard 智守 |
| 报告日期 | 2026-11-06 |
| 被测对象 | `src/main.py` 真实入口、真实 Qt 界面、真实 FFmpeg 解码、真实 SQLite 存储 |
| 测试类型 | 黑盒（Black-box）功能验收 |
| 测试数据 | 客户提供的 3 段真实监控录像（L107 / L108 监控头，1920x1080 H.264） |
| 证据目录 | [`docs/testing/evidence/`](evidence/) |
| 结论 | **10 项黑盒用例全部通过，附真实录像与界面截图** |

## 2. 黑盒测试方法

黑盒测试只做三件事：

1. **启动真实程序** —— 跑 `python src/main.py <命令>`，不导入任何内部模块；
2. **观察可观测结果** —— 进程退出码、stdout 的 JSON、落盘文件、数据库行、画面像素；
3. **用客户自己的录像做输入** —— 把 L107/L108 真实监控录像当作摄像头信号源，验证接入链路。

断言不引用 `factoryguard.*` 内部结构，因此测试对重构是诚实。。

## 3. 被测功能面

| 功能面 | 覆盖命令/界面 | 结果 |
| --- | --- | --- |
| 命令行入口 | `--help` | PASS |
| 部署预检 | `health` | PASS |
| 通知渠道清单 | `channels` | PASS |
| 媒体清理 | `cleanup-media --dry-run` | PASS |
| 通知补发 | `dispatch-outbox` | PASS |
| 摄像头取流 | `probe-rtsp` | PASS |
| 真实录像接入 | 模拟器播放客户录像 | PASS |
| 画面保真度 | 交付帧 vs 源文件对比 | PASS |
| 界面截图 | 6 个页面离屏渲染 | PASS |
| 实时画面录制 | 客户录像实时预览录屏 | PASS |

## 4. 用例执行记录

### 4.1 入口与运维命令（真实入口黑盒）

| 编号 | 用例 | 输入 | 期望 | 实测 | 结果 |
| --- | --- | --- | --- | --- | --- |
| BB-01 | 命令清单只含当前命令 | `main.py --help` | 列出 gui/tray/probe-rtsp/health/channels/dispatch-outbox/cleanup-media；不含 simulate/detect-rtsp/monitor-rtsp | 退出码 0，命令清单完全匹配 | PASS |
| BB-02 | 预检返回机器可读报告 | `main.py health --db <tmp> --media-dir <tmp>` | stdout 为 JSON，含数据库路径与检查项 | 退出码 0，JSON 可解析 | PASS |
| BB-03 | 通知渠道与鉴权状态 | `main.py channels` | 7 个渠道，webhook/email/sms 均 implemented，需鉴权者标记 authorization_required | 完全匹配 | PASS |
| BB-04 | 媒体清理可观测且安全 | `main.py cleanup-media --dry-run --retention-days 0` | 六个计数字段，dry-run 不删文件 | 返回 `scanned/eligible/removed/bytes_freed/skipped/errors` 全 0 | PASS |
| BB-05 | 空队列补发不报错 | `main.py dispatch-outbox --webhook-url https://127.0.0.1:1/...` | 退出码 0，claimed/sent/retried/failed 均为 0 | 完全匹配 | PASS |

### 4.2 客户真实录像接入（新增，见 `tests/test_blackbox_real_media.py`）

| 编号 | 用例 | 期望 | 实测 | 结果 |
| --- | --- | --- | --- | --- |
| BB-06 | 客户录像经 RTSP 模拟器可播放 | 模拟器就绪，RTSP 地址可取到 360x640 画面 | 就绪成功，取到帧，标准差 51.9（非纯色画面） | PASS |
| BB-07 | 真实录像可作为摄像头接入平台 | `main.py probe-rtsp` 返回成功 | `status=ok`、宽 640 高 360、帧号有效、地址已脱敏 | PASS |
| BB-08 | 交付画面与源文件一致（不花屏） | 交付帧的水平梯度 >= 源文件的 75% | 源 9.58 / 交付 9.52，比值 0.99 | PASS |
| BB-09 | 出厂配置通过预检 | `main.py health` 退出码 0，无 error 项 | 退出码 0，FFmpeg/磁盘/媒体目录全部 ok | PASS |
| BB-10 | 客户录像接入后拓扑正确 | 库中 1 摄像头 1 设备，预检清单可读 | inventory.cameras=1、inventory.devices=1 | PASS |

> **BB-08 的意义**：MPEG-TS over RTP 在收发缓冲区过小时会丢包，画面会呈现"竖条纹"。
> 该用例把交付帧与源文件直接解码帧做梯度比对，任何丢包都会立刻让断言失败。
> 这项用例在本轮同时促成了模拟器 RTP 接收缓冲区从默认值提升到 8 MB 的修复。

### 4.3 界面与录屏证据

| 编号 | 证据 | 说明 | 结果 |
| --- | --- | --- | --- |
| BB-11 | 六页面截图 | 工作台 / 实时画面 / 险情中心 / 区域与设备 / 现场问询 / 系统设置，1440x900 | PASS |
| BB-12 | 控制台巡览录像 | `console-walkthrough.mp4`，逐页切换 | PASS |
| BB-13 | 真实录像实时预览录屏 | `live-view-real-footage.mp4`，客户 L107 录像在平台实时画面中播放 | PASS |

## 5. 证据清单

### 5.1 截图

| 文件 | 页面 | 说明 |
| --- | --- | --- |
| `evidence/page-dashboard.png` | 工作台 | 今日值守概览、四个指标卡、重点区域画面、待处理险情 |
| `evidence/page-live.png` | 实时画面 | 场所/区域/状态筛选、摄像头卡片、多路查看 |
| `evidence/page-incidents.png` | 险情中心 | 筛选栏、险情列表、详情处置区（含"补充位置"） |
| `evidence/page-devices.png` | 区域与设备 | 场所管理、区域卡片、摄像头管理 |
| `evidence/page-assistant.png` | 现场问询 | 问询记录、问询范围、可点选示例问题 |
| `evidence/page-settings.png` | 系统设置 | 文字问询、国标服务、通知、品牌横幅、本地环境 |

### 5.2 视频

| 文件 | 时长 | 内容 |
| --- | --- | --- |
| `evidence/live-view-real-footage.mp4` | 8.3 秒 / 50 帧 / 1120x720 | **平台实时画面播放客户 L107 真实录像**。画面含原始 OSD 时间戳与场景，可直接证明接入与解码链路可用 |
| `evidence/console-walkthrough.mp4` | 逐页巡览 | 控制台六个页面切换过程 |

### 5.3 机器可读清单

- `evidence/manifest.json` —— 截图清单、分辨率、生成时间
- `evidence/footage-recording.json` —— 录屏所用源片段、帧数、帧率

## 6. 复现方式

~~~powershell
# 环境
conda activate factoryguard

# 一键重跑黑盒用例（含真实录像接入）
python -m pytest tests/test_acceptance_blackbox.py tests/test_blackbox_real_media.py -v

# 重新生成截图与录屏
python -m tools.capture_evidence --db data/evidence.db --config config/evidence.json --out docs/testing/evidence
python -m tools.record_footage --out docs/testing/evidence --fps 6 --seconds 14
~~~

## 7. 已知限制

- 真实录像为**回放**接入，不是现场实时设备；网络抖动、断电重启、厂商私有协议仍需现场联调；
- 模拟器每台设备仅一路通道，不覆盖 NVR 级联、PTZ 与录像回放；
- 录屏在离屏模式下由界面帧合成，不含鼠标轨迹；
- 交付录像中出现的原始 OSD 时间戳属于客户设备自带信息，平台不做解析与展示；
- 界面截图基于 1440x900，窄屏适配由 `tests/test_merchant_console.py` 的多分辨率回归覆盖。

## 8. 结论

黑盒验收通过。产品真实入口可用、出厂配置可预检通过、客户真实录像可接入并**保真**解码、
六个业务页面可正常呈现，证据齐备可用于交付答辩。
