# 测试用例

## 1. 配置与迁移

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-CFG-001 | 读取不存在的配置 | 使用默认值，不创建损坏文件 | `tests/test_config.py` |
| TC-CFG-002 | 配置缺省字段 | 与默认值合并 | `tests/test_config.py` |
| TC-CFG-003 | 保存 LLM 配置 | JSON 包含 provider/endpoint/model/key/enabled | `tests/test_merchant_console.py` |
| TC-CFG-004 | 重启后读取 LLM 配置 | LLM 模型 ID 和 Key 恢复 | `tests/test_merchant_console.py` |
| TC-CFG-005 | 非法 JSON | 抛出明确 `ConfigFileError` | `tests/test_config.py` |
| TC-CFG-006 | 设备凭据保存 | 写入配置 JSON，不出现在 SQLite | `tests/test_config.py` |
| TC-CFG-007 | 旧配置迁移 | 旧注册表/数据库设置转入 JSON | 自动测试/代码审查 |
| TC-CFG-008 | 配置文件路径参数 | `--config` 传递到商户控制台 | `tests/test_main.py` |
| TC-CFG-009 | 新配置无厂商默认值 | 服务商、接口、国标平台编号和域为空 | `tests/test_config.py` |
| TC-CFG-010 | 品牌横幅配置 | 相对路径和高度写入 `branding` | `tests/test_merchant_console.py` |
| TC-CFG-011 | 相对媒体目录 | 健康检查按当前工作目录解析 events-media | `tests/test_main.py` |
| TC-CFG-012 | 配置写入失败 | 内存配置回滚，不得显示未落盘的假成功状态 | `tests/test_config.py` |
| TC-CFG-013 | 启动异常提示 | 数据库损坏或目录异常时显示中文启动失败提示并返回错误码 | `tests/test_merchant_console.py` |
| TC-CFG-014 | 一键数据备份 | 在线生成含配置、数据库和清单的 ZIP，恢复出的数据库可读取 | `tests/test_backup.py`、`tests/test_merchant_console.py` |
| TC-CFG-015 | 首次运行备份 | 配置尚未保存时先写入默认 JSON，再生成包含配置的备份 | `tests/test_config.py`、`tests/test_merchant_console.py` |
| TC-CFG-016 | 安全恢复备份 | 校验 ZIP 和数据库，暂存到下次启动，成功恢复并生成恢复前安全备份 | `tests/test_backup.py`、`tests/test_merchant_console.py` |
| TC-CFG-017 | 备份版本兼容 | 高于当前主版本的备份被拒绝，避免恢复不兼容数据库结构 | `tests/test_backup.py` |
| TC-CFG-018 | 备份状态摘要 | 显示备份数量、总大小和最近备份时间，并在备份后刷新 | `tests/test_merchant_console.py` |
| TC-CFG-019 | 嵌套配置默认值 | 局部通知配置仍保留端口、安全方式、收件人等嵌套默认值 | `tests/test_config.py` |
| TC-CFG-020 | 客户横幅备份恢复 | 备份、恢复和安全备份均保留 `branding/banner.svg` | `tests/test_backup.py` |
| TC-CFG-021 | 嵌套配置递归合并 | 只更新 email.host 时保留端口、安全方式、收件人及其他渠道配置 | `tests/test_config.py` |
| TC-CFG-022 | 旧配置迁移保护 | 保留新模型/服务商设置，补入旧密钥并转移设备凭据；无法解密的旧密钥不得删除 | `tests/test_config.py` |
| TC-CFG-023 | 备份清单一致性 | 清单声明的配置/客户横幅存在状态必须与 ZIP 实际成员一致，否则拒绝恢复 | `tests/test_backup.py` |
| TC-CFG-024 | 摄像头原子保存 | 设备、通道、码流任一写入失败时全部回滚，不留下半套拓扑 | `tests/test_storage.py` |
| TC-CFG-025 | 凭据保存回滚 | 拓扑保存失败时删除新凭据或恢复旧凭据，不留下孤儿凭据 | `tests/test_merchant_console.py` |
| TC-CFG-026 | 凭据保存保留健康元数据 | 更新凭据后仍保留最近探测时间、健康状态和错误详情 | `tests/test_config.py` |
| TC-CFG-027 | 摄像头引用一致性 | 原子保存拒绝设备、区域、通道或码流引用不匹配的拓扑 | `tests/test_storage.py` |
| TC-CFG-028 | 无配置横幅备份 | 配置文件缺失时仍备份 `branding/banner.svg`，清单准确标记横幅存在 | `tests/test_backup.py` |
| TC-CFG-029 | 区域原子保存 | 新增和编辑均将场所与区域一起保存，区域写入失败不留下孤儿场所；父级和自引用错误在事务前拒绝 | `tests/test_storage.py`、`tests/test_merchant_console.py` |

## 2. 摄像头、设备与模拟器

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-DEV-001 | 摄像头 URL 含账号密码 | 地址与凭据拆分，预览不显示密码 | `tests/test_merchant_console.py` |
| TC-DEV-002 | ONVIF 接入向导 | 只显示地址与账号密码字段 | `tests/test_merchant_console.py` |
| TC-DEV-003 | GB28181 接入向导 | 显示设备编号和通道，隐藏地址字段 | `tests/test_merchant_console.py` |
| TC-DEV-004 | RTSP 接入向导 | 显示 RTSP 地址和码流名称 | UI 检查 |
| TC-DEV-005 | ONVIF 探测与通道 | 返回能力、通道和码流 URI | `tests/test_device_integration.py` |
| TC-DEV-006 | GB28181 注册/目录/事件 | 平台接收注册并映射事件 | `tests/test_device_integration.py` |
| TC-DEV-007 | 流健康更新 | 更新状态、详情和恢复计数 | `tests/test_device_integration.py` |
| TC-DEV-008 | 离线摄像头展示 | 状态和检查提示清楚可辨 | UI 检查 |
| TC-DEV-009 | ONVIF 模拟设备 | 发现、设备信息、能力、Profile S 和通道可查询 | `tests/test_camera_simulator.py` |
| TC-DEV-010 | ONVIF 模拟码流 | 返回 RTSP 地址并可读取首帧 | `tests/test_camera_simulator.py` |
| TC-DEV-011 | GB28181 模拟注册 | Digest 注册、目录和告警进入平台队列 | `tests/test_camera_simulator.py` |
| TC-DEV-012 | GB28181 模拟点播 | INVITE 后通过 RTP/MPEG-TS 读取首帧 | `tests/test_camera_simulator.py` |
| TC-DEV-013 | 模拟器独立命令 | 不设置 PYTHONPATH 也能执行 `python -m tools.camera_sim --help` | `tests/test_camera_simulator.py` |
| TC-DEV-014 | 模拟器非法参数 | 拒绝负数时长/等待时间和非法端口 | `tests/test_camera_simulator.py` |
| TC-DEV-015 | RTSP 连接测试 | 返回源/处理分辨率和首帧读取结果 | `tests/test_stream_probe.py` |
| TC-DEV-016 | 编辑摄像头 | 保留设备/摄像头/码流 ID 并更新名称、地址和凭据 | `tests/test_merchant_console.py` |
| TC-DEV-017 | 删除摄像头 | 清理码流和健康状态；共享设备仅在最后通道删除时移除 | `tests/test_storage.py` |
| TC-DEV-018 | 区域编辑删除 | 可重命名空区域，非空区域拒绝删除 | `tests/test_merchant_console.py`、`tests/test_storage.py` |
| TC-DEV-019 | ONVIF 原生事件订阅 | 启动订阅前探测能力，事件通过回调进入实时队列 | `tests/test_device_integration.py`、`tests/test_merchant_console.py` |
| TC-DEV-020 | ONVIF 连续告警去重 | 相同 PropertyOperation 的不同告警获得不同事件指纹 | `tests/test_device_integration.py` |
| TC-DEV-021 | 订阅健康展示 | 订阅状态和异常原因进入摄像头提示与运营快照 | `tests/test_device_integration.py` |
| TC-DEV-022 | 摄像头暂停恢复 | 暂停后不取流、不生成设备险情；共享设备仅在最后通道停用时停订阅 | `tests/test_storage.py`、`tests/test_merchant_console.py` |
| TC-DEV-023 | 国标服务启动检测 | 等待 SIP 端口实际绑定；失败抛出明确错误，运行退出后界面显示已停止 | `tests/test_config.py` |
| TC-DEV-024 | 状态检测时间 | 摄像头卡片和运营快照显示最近检测时间，未检测时明确提示 | `tests/test_device_integration.py`、`tests/test_merchant_console.py` |
| TC-DEV-025 | 状态时效与可信度 | 无检测时间、超过 30 分钟或非标准状态不显示在线；新鲜设备探测结果可显示在线 | `tests/test_incidents_assistant.py` |
| TC-DEV-026 | 缺少 FFmpeg | 手动检测提示缺组件，自动检测跳过，后台兜底记为未确认而不是离线 | `tests/test_merchant_console.py` |
| TC-DEV-027 | 多设备事件去重隔离 | 不同设备使用相同通道和厂商事件号时，关联键、去重键与界面队列仍彼此独立 | `tests/test_device_integration.py`、`tests/test_merchant_console.py` |
| TC-DEV-028 | 最佳码流健康口径 | 状态检测与预览使用同一最佳码流；主码流在线但子码流离线时摄像头显示离线 | `tests/test_incidents_assistant.py` |
| TC-DEV-029 | RTSP 直连地址编辑 | ONVIF/GB 通道的 RTSP 地址变化时同步更新实际播放码流，并保留码流 ID 和名称 | `tests/test_merchant_console.py` |
| TC-DEV-030 | 原生事件来源标注 | ONVIF 与 GB/T 28181 告警按真实来源写入上报人，新险情立即刷新侧栏统计 | `tests/test_merchant_console.py` |
| TC-DEV-031 | 国标配置错误降级 | 手工配置非法 SIP 端口时平台仍可启动，并显示“配置错误” | `tests/test_merchant_console.py` |
| TC-DEV-032 | 国标启动失败状态 | SIP 端口占用导致启动失败后，刷新不会把错误覆盖为“未启用” | `tests/test_merchant_console.py` |
| TC-DEV-033 | 摄像头凭据逐字段合并 | 只改账号时保留旧密码，只改密码时保留旧账号；播放地址查询参数含 `@` 时仍正确注入凭据 | `tests/test_merchant_console.py` |
| TC-DEV-034 | 编辑时阻止重复连接 | 摄像头可保留自身连接，但不得改成另一台摄像头正在使用的地址 | `tests/test_merchant_console.py` |
| TC-DEV-035 | RTSP 特殊字符凭据 | 账号密码中的 `@`、`:`、`/`、`#`、`%` 正确编码；查询参数中的 `@` 不干扰账号注入 | `tests/test_merchant_console.py` |
| TC-DEV-036 | IPv6 RTSP 地址 | 拆分凭据后保留 IPv6 方括号和端口，生成合法可播放地址 | `tests/test_merchant_console.py` |
| TC-DEV-037 | ONVIF 技术主题转业务标题 | 移动、遮挡、区域入侵等常见主题显示中文业务标题，原始主题保留用于诊断 | `tests/test_device_integration.py` |
| TC-DEV-038 | ONVIF 告警拓扑与文案 | 内部设备 ID 可绑定区域和摄像头；单通道可安全回退；详情使用业务文案并保留原始代码 | `tests/test_merchant_console.py` |
| TC-DEV-039 | ONVIF 地址变更缓存失效 | 编辑设备地址后清除旧健康状态；缓存主机不一致或状态未确认时不直接播放，重新解析并更新原码流 | `tests/test_merchant_console.py` |
| TC-DEV-040 | 地址变更码流健康失效 | 设备地址变化后所有关联码流改为待检测；健康失效写入异常不误报配置保存失败 | `tests/test_merchant_console.py` |
| TC-DEV-041 | 凭据变更码流健康失效 | 只修改设备账号或密码时，关联码流也改为待检测，不沿用旧在线结果 | `tests/test_merchant_console.py` |
| TC-DEV-042 | ONVIF 多设备选择 | 扫描发现多台设备时显示名称/服务地址列表，选择后预填对应设备 | `tests/test_merchant_console.py` |
| TC-DEV-043 | 自动状态检测 | 启动 60 秒后首次检测、之后每 10 分钟检测；无码流时静默跳过 | `tests/test_merchant_console.py` |
| TC-DEV-044 | 重复摄像头检测 | 同 RTSP 地址或同设备通道禁止重复添加，不同地址仍可添加 | `tests/test_merchant_console.py` |
| TC-DEV-045 | 原生告警规则边界 | 只保存告警码、去重窗口和动作；旧画面几何、置信度、条件树字段被忽略 | `tests/test_storage.py` |
| TC-DEV-046 | 订阅快速重启 | 停止原生订阅后立即重启，旧线程退出不得清除新订阅状态 | `tests/test_device_integration.py` |
| TC-DEV-047 | 国标服务重启 | 国标服务停止后可重新启动，平台关闭次数与启动次数一致 | `tests/test_config.py` |
| TC-DEV-048 | 共享通道删除订阅连续性 | 删除共享设备的非最后通道时不中断订阅，删除最后通道时才停止订阅 | `tests/test_merchant_console.py` |
| TC-DEV-049 | 国标随机端口恢复 | 随机端口绑定失败可重试，失败时关闭 UDP 且不产生资源泄漏 | `tests/test_camera_simulator.py`、`tests/test_main.py` |

## 3. 险情与对话

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-INC-001 | 创建险情 | 持久化并写审计 | `tests/test_incidents_assistant.py` |
| TC-INC-002 | 重复险情 ID | 不重复写入 | `tests/test_incidents_assistant.py` |
| TC-INC-003 | 按区域/等级筛选 | 返回匹配记录 | `tests/test_incidents_assistant.py` |
| TC-INC-004 | 确认、解决、误报 | 状态更新并留痕 | 自动测试 |
| TC-INC-005 | 自然语言登记险情 | 识别区域、等级和摄像头 | `tests/test_incidents_assistant.py` |
| TC-INC-006 | 缺少险情细节 | 不创建空险情，提示补充 | `tests/test_incidents_assistant.py` |
| TC-INC-007 | 本地问询 | 无大模型时仍可回答 | `tests/test_incidents_assistant.py` |
| TC-INC-008 | 对话历史 | 用户/助手消息持久化 | `tests/test_incidents_assistant.py` |
| TC-INC-009 | 原生告警及时性 | 1 秒轮询处置告警、生成险情并触发状态提醒 | `tests/test_merchant_console.py` |
| TC-INC-010 | 险情搜索范围 | 可按区域、摄像头、等级、状态、来源、上报人和描述搜索 | `tests/test_merchant_console.py` |
| TC-INC-011 | 原生事件去重缓存 | 重复事件被拒绝，超过 10,000 条后按先进先出淘汰 | `tests/test_merchant_console.py` |
| TC-INC-012 | 重新打开险情 | 已解决或误报险情经确认回到待处理，并记录纠错审计 | `tests/test_merchant_console.py` |
| TC-INC-013 | 处置记录展示 | 详情页显示创建和状态变更的操作人、时间和动作 | `tests/test_incidents_assistant.py`、`tests/test_merchant_console.py` |
| TC-INC-014 | 未关闭险情不能被历史挤出 | 存在超过 100 条较新的已关闭险情时，较早的未关闭险情仍进入运营上下文和计数 | `tests/test_storage.py` |
| TC-INC-015 | 重复问询上报去重 | 5 分钟内同内容、同区域、同摄像头的连续上报返回原险情，不重复建单或通知 | `tests/test_incidents_assistant.py`、`tests/test_merchant_console.py` |
| TC-INC-016 | 问询引用险情口径 | 运营快照和回答引用只使用险情表 ID，不重复读取旧事件表或生成重复设备事件段落 | `tests/test_incidents_assistant.py` |
| TC-INC-017 | 险情状态机 | 已解决/误报只能先重新打开；非法状态转换被拒绝且不生成审计记录 | `tests/test_incidents_assistant.py` |
| TC-UI-027 | 本地时间显示 | UTC 时间转换为本机时区，非法时间回退为明确提示 | `tests/test_merchant_console.py` |
| TC-UI-028 | 单路预览重连 | 视频断流后显示重新连接按钮，并复用原摄像头地址重试 | `tests/test_merchant_console.py` |
| TC-UI-029 | 画面墙单格重连 | 单个画面格断流后只重启该路解码，其他画面不受影响 | `tests/test_merchant_console.py` |

## 4. 大语言模型

> 本节只涉及文本问答大模型，不涉及视觉模型或视频帧推理。

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-LLM-001 | 配置不完整 | 不启用大语言模型 | 自动测试 |
| TC-LLM-002 | 显式关闭 | 保留配置但回退本地规则 | `tests/test_incidents_assistant.py` |
| TC-LLM-003 | 接口地址补全 | `/v1` 自动补全 Chat Completions | `tests/test_incidents_assistant.py` |
| TC-LLM-004 | 解析标准响应 | 提取 assistant 文本 | `tests/test_incidents_assistant.py` |
| TC-LLM-005 | 连接测试失败 | 显示错误并保留配置 | UI 手动验证 |
| TC-LLM-006 | 接口不可用 | 自动回退本地回答 | UI/异常测试 |
| TC-LLM-007 | 文字边界提示 | 设置页明确不读取、不上传视频画面，也不执行视觉识别 | `tests/test_merchant_console.py` |
| TC-LLM-008 | 手工配置错误降级 | 错误超时值不阻断启动，自动回退本地规则并显示配置错误 | `tests/test_merchant_console.py` |
| TC-LLM-009 | 接口地址与超时校验 | 非 HTTP(S) 地址、非正超时被拒绝，连接测试不会抛出未处理异常 | `tests/test_incidents_assistant.py`、`tests/test_merchant_console.py` |
| TC-LLM-010 | 服务错误脱敏与文案 | HTTP 和网络错误显示脱敏详情，不得回显 API Key；界面和异常不得出现“大模型”内部术语 | `tests/test_incidents_assistant.py`、`tests/test_merchant_console.py` |

## 5. 险情通知

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-NOTIFY-001 | Webhook 通知 | 发送 JSON 载荷并生成正确 HMAC-SHA256 签名 | `tests/test_incident_notifications.py` |
| TC-NOTIFY-002 | 邮件通知 | 使用 SMTP/STARTTLS/SSL 配置发送险情摘要 | `tests/test_incident_notifications.py` |
| TC-NOTIFY-003 | 通知配置持久化 | 开关、地址、收件人和密钥写入 JSON，重启后恢复 | `tests/test_merchant_console.py` |
| TC-NOTIFY-004 | 新险情自动通知 | 原生告警生成险情后调用配置的通知渠道 | `tests/test_merchant_console.py` |
| TC-NOTIFY-005 | 通知失败隔离 | 外部通知失败不影响险情保存和处置 | 服务层异常测试/界面检查 |
| TC-NOTIFY-006 | 短信通知 | HTTPS 短信网关接收险情摘要、模板和号码 | `tests/test_incident_notifications.py` |
| TC-NOTIFY-007 | 投递记录与重试 | 记录状态/尝试次数，仅选择失败渠道重试 | `tests/test_storage.py`、`tests/test_merchant_console.py` |
| TC-NOTIFY-008 | 自动退避重试 | 仅对超过 60 秒且不足 3 次尝试的失败渠道自动重试 | `tests/test_storage.py`、`tests/test_merchant_console.py` |
| TC-NOTIFY-009 | 失败原因可见 | 险情详情展示失败渠道和原因，通知线程异常仍写入投递记录 | `tests/test_merchant_console.py`、`tests/test_incident_notifications.py` |
| TC-NOTIFY-010 | 手工配置错误可见 | JSON 中启用非法通知渠道时设置页显示配置错误，修正并保存后恢复 | `tests/test_merchant_console.py` |
| TC-NOTIFY-011 | 总开关可随时停用 | 总开关关闭时可保存半成品渠道配置，不会发送通知；重新启用时执行完整校验 | `tests/test_incident_notifications.py`、`tests/test_merchant_console.py` |
| TC-NOTIFY-012 | 通知状态文案 | 关闭时保留渠道草稿并显示“通知已关闭”；总开关开启但无渠道时显示“待配置” | `tests/test_merchant_console.py` |
| TC-NOTIFY-013 | 重试渠道隔离 | 线程异常时只给本次实际请求的渠道记录失败，不污染已成功渠道 | `tests/test_merchant_console.py` |
| TC-NOTIFY-014 | 停用草稿容错 | 总开关关闭时可保存非法/不完整邮件短信草稿，设置页读取非法端口不崩溃 | `tests/test_incident_notifications.py`、`tests/test_merchant_console.py` |
| TC-NOTIFY-015 | 渠道名称业务化 | 投递状态和错误详情显示 Webhook、邮件、短信，重试仍使用原始渠道 ID | `tests/test_merchant_console.py` |
| TC-NOTIFY-016 | 停用渠道重试抑制 | 失败渠道关闭后不参与手动/自动重试，详情显示“已停用”且不显示重试按钮 | `tests/test_merchant_console.py` |
| TC-NOTIFY-017 | 停用后历史可见 | 通知总开关关闭后仍显示历史投递和失败原因，只隐藏重试按钮 | `tests/test_merchant_console.py` |

## 6. 界面、后台与产品边界

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-UI-001 | 控制台构建 | 6 个主页面可切换 | `tests/test_merchant_console.py` |
| TC-UI-002 | 导航文案 | 使用工作台/现场问询等业务语言 | 自动测试 |
| TC-UI-003 | 短窗口响应 | 高度不足时显示 2 路重点画面 | UI 检查 |
| TC-UI-004 | 启动画面 | 640×360 正常渲染并显示进度 | `tests/test_merchant_console.py` |
| TC-UI-005 | 关闭窗口 | 隐藏到托盘，后台继续运行 | 自动测试/Windows 手动 |
| TC-UI-006 | 托盘退出 | 显式退出并释放资源 | 自动测试/Windows 手动 |
| TC-UI-007 | 实时预览关闭 | 不出现 QThread 已删除异常 | `tests/test_merchant_console.py` |
| TC-UI-008 | 空数据工作台 | 显示配置引导，不显示误导性正常状态 | UI 检查 |
| TC-UI-009 | 多分辨率 | 1120×640、1280×720、1366×768、1440×900 无水平溢出 | `tests/test_merchant_console.py` |
| TC-UI-010 | 用户 SVG 横幅 | 从配置加载 SVG，按指定高度全宽显示 | `tests/test_merchant_console.py` |
| TC-UI-011 | 默认品牌横幅 | 无客户 SVG 时仍显示完整默认横幅 | `tests/test_merchant_console.py` |
| TC-UI-012 | 非法 SVG 回退 | 含脚本的 SVG 被拒绝并恢复默认横幅 | `tests/test_merchant_console.py` |
| TC-UI-013 | 恢复默认横幅 | 清除客户配置后保留默认横幅 | `tests/test_merchant_console.py` |
| TC-UI-014 | 退出等待后台线程 | 有后台线程时延迟退出，完成后关闭数据库 | `tests/test_merchant_console.py` |
| TC-UI-015 | 无视觉模型栈 | 仓库不含 models、inference、simulation 目录或 ONNX 依赖 | `tests/test_main.py` |
| TC-UI-016 | 旧模型参数拒绝 | CLI 不再接受 `--model` 等视觉模型参数 | `tests/test_main.py` |
| TC-UI-017 | 数值输入可读 | 横幅高度、SMTP 端口、问询超时和 SIP 端口的值可见可读 | `tests/test_merchant_console.py` |
| TC-UI-018 | 区域状态语义 | 未检测摄像头显示“待检测”，不得误标“全部离线” | `tests/test_merchant_console.py` |
| TC-UI-019 | 健康检查边界 | 运维输出报告摄像头和码流，不再提示配置视觉规则 | `tests/test_main.py` |
| TC-UI-020 | 单实例运行 | 第二个控制台实例无法建立实例锁 | `tests/test_merchant_console.py` |
| TC-UI-021 | 后台任务防重入 | ONVIF 扫描和文字服务测试进行中时不会重复启动线程 | `tests/test_merchant_console.py` |
| TC-UI-022 | 通知表单联动 | 总开关控制渠道开关，渠道开关控制对应字段 | `tests/test_merchant_console.py` |
| TC-UI-023 | 周期刷新 | 只刷新当前页面和共享汇总，不覆盖状态栏错误提示 | `tests/test_merchant_console.py` |
| TC-UI-024 | 四路实时画面墙 | 最多同时显示四路，关闭时停止并释放全部解码线程 | `tests/test_merchant_console.py` |
| TC-UI-025 | 多路画面选择 | 超过四路时打开选择器，最多保留四路并按用户选择打开画面墙 | `tests/test_merchant_console.py` |
| TC-UI-026 | 分级预览参数 | 单路使用 1280×720 / 8 FPS，画面墙使用 960×540 / 4 FPS | `tests/test_merchant_console.py` |
| TC-UI-030 | 部分摄像头停用 | 侧栏分别显示在线、停用和待处理险情数量 | `tests/test_merchant_console.py` |
| TC-UI-031 | 混合设备状态 | 侧栏拆分在线、离线、待检测和停用；只有待检测时显示“状态待确认” | `tests/test_merchant_console.py` |
| TC-UI-032 | 工作台不依赖文字服务 | 可选文字问询启用或关闭时，工作台安全摘要文案保持一致 | `tests/test_merchant_console.py` |
| TC-UI-033 | 待处理险情状态 | 摄像头在线但存在中低等级未关闭险情时，显示“有待处理”而不是运行平稳 | `tests/test_merchant_console.py` |
| TC-UI-034 | 删除摄像头凭据异常 | 摄像头删除后拓扑立即刷新；凭据清理失败时明确提示并保留可检查信息 | `tests/test_merchant_console.py` |
| TC-UI-035 | 区域未知状态口径 | 非标准但未确认的健康值在区域卡片显示“待检测”，不得误标为“全部离线” | `tests/test_merchant_console.py` |
| TC-UI-036 | SVG 安全边界 | 拒绝事件属性、外部 href、远程样式 URL、实体声明和 foreignObject，正常渐变 SVG 可用 | `tests/test_merchant_console.py` |
| TC-UI-037 | 区域名称唯一性 | 同一场所内新建或重命名时拒绝重复区域名称，不同场所允许同名 | `tests/test_merchant_console.py` |
| TC-UI-038 | 纯视频预览边界 | 预览组件不提供检测框、置信度、目标标签或检测截图 API | `tests/test_preview.py` |
| TC-UI-039 | 旧视觉模块禁用 | 条件树、几何命中、规则评估和规则模拟调用均明确拒绝，不进入运行路径 | `tests/test_condition_tree.py`、`tests/test_geometry.py`、`tests/test_rules.py`、`tests/test_rule_simulation.py` |
| TC-UI-040 | 审计操作来源业务化 | 处置记录显示当前用户、设备告警服务、现场问询等业务名称，不暴露内部 actor 标识 | `tests/test_merchant_console.py` |
| TC-UI-041 | SVG 保存事务 | 选择新 SVG 只预览不覆盖当前文件，点击保存后才复制并更新配置 | `tests/test_merchant_console.py` |
| TC-UI-042 | 国标批量导入原子性 | 多台设备多通道一次事务写入，任一通道失败整体回滚 | `tests/test_storage.py` |
| TC-UI-043 | 国标导入区域消歧 | 跨场所同名区域标签可区分，导入落到用户选择的区域 | `tests/test_merchant_console.py` |
| TC-UI-044 | 国标导入告警闭环 | 导入后的通道收到设备告警后险情归属到正确区域和摄像头 | `tests/test_merchant_console.py` |
| TC-UI-045 | 区域名称存储层唯一 | 同一场所重复区域名被拒绝，不同场所同名允许 | `tests/test_storage.py` |
| TC-UI-046 | 设备告警时间戳兼容 | 字符串与 datetime 时间戳均可受理，坏事件不丢弃后续告警 | `tests/test_merchant_console.py` |
| TC-UI-047 | 跨场所同名区域显示 | 同名区域与摄像头自动附加场所前缀，唯一名称不加前缀 | `tests/test_incidents_assistant.py` |
| TC-UI-048 | 国标设备重复导入 | 同一国标设备编号已归属其他区域时拒绝导入，不产生新数据 | `tests/test_merchant_console.py` |
| TC-UI-049 | 区域筛选标签一致性 | 实时画面、险情中心和设备页的区域筛选统一显示“场所 / 区域” | `tests/test_merchant_console.py` |
| TC-UI-050 | 配置错误恢复指引 | 启动失败提示包含配置文件路径、可用备份和恢复方法；无备份时说明手工修正 | `tests/test_merchant_console.py` |
| TC-UI-051 | 上报险情标签一致性 | 区域与摄像头下拉显示“场所 / 区域”和“场所 / 区域 / 摄像头” | `tests/test_merchant_console.py` |
| TC-UI-052 | 多场所横幅名称 | 单场所显示名称，多场所显示场所数量，不隐藏其余门店 | `tests/test_merchant_console.py` |
| TC-UI-053 | 定期数据维护 | 每 6 小时清理过期事件/审计/outbox 并 checkpoint，失败时提示不抛出 | `tests/test_merchant_console.py` |
| TC-UI-054 | 保留期清理语义 | 清理过期事件与审计但保留险情和通知历史，非法保留期被拒绝 | `tests/test_storage.py` |
| TC-CFG-031 | 配置并发写入 | 同一文件的多实例共享状态，并发写入不丢失任一侧设置 | `tests/test_config.py` |
| TC-DEV-050 | ONVIF 订阅自愈 | 单次拉取失败后自动退避重试并继续投递告警，持续失败达上限才停止 | `tests/test_device_integration.py` |
| TC-DEV-051 | 断线后订阅恢复 | 设备掉线后无需人工干预即可重新订阅并接收告警 | `tests/test_device_integration.py` |
| TC-UI-055 | 取流失败可操作提示 | 按 ONVIF/国标协议给出不同的排查建议，保留原始错误 | `tests/test_merchant_console.py` |
| TC-UI-056 | 托盘退出释放资源 | 窗口隐藏到托盘后退出仍关闭数据库连接并释放后台任务 | `tests/test_merchant_console.py` |
| TC-UI-057 | 后台告警通知闭环 | 窗口隐藏状态下收到设备告警仍入库并发出通知 | `tests/test_merchant_console.py` |
| TC-DEP-001 | 开机自启动状态 | 查询命令返回当前启用状态与启动项位置，非 Windows 平台明确不支持 | `tests/test_main.py` |
| TC-DEP-002 | 开机自启动启停 | 启用后生成启动快捷方式，停用后移除；设置页开关失败时提示且不保留错误状态 | `tests/test_main.py`、`tests/test_merchant_console.py` |
| TC-UI-058 | 后台任务异常隔离 | 单个定时任务失败不中断巡检周期其他任务，失败被记录并提示 | `tests/test_merchant_console.py` |
| TC-CFG-032 | 废弃凭据清理 | 清理未被任何设备引用的凭据条目，仍在使用的凭据保留 | `tests/test_config.py`、`tests/test_storage.py`、`tests/test_merchant_console.py` |
| TC-INC-018 | 险情区域归属推断 | 只选摄像头不选区域时自动补全区域；问询登记同样生效 | `tests/test_merchant_console.py`、`tests/test_incidents_assistant.py` |
| TC-INC-019 | 险情地点快照 | 险情保存事发时的区域与摄像头名称，区域改名或删除后历史仍可追溯 | `tests/test_incidents_assistant.py` |
| TC-DEV-052 | 媒体错误翻译 | 连接拒绝、超时、认证失败、地址不存在等错误转为中文可操作提示，凭据保持脱敏 | `tests/test_ffmpeg.py` |
| TC-DEV-053 | ONVIF 连接测试 | 接入向导可测试 ONVIF 连接与认证，报告视频通道数量，失败给出原因 | `tests/test_stream_probe.py`、`tests/test_merchant_console.py` |
| TC-DEV-054 | 端口变更失效码流 | 设备改到同主机不同端口后不再复用旧码流 | `tests/test_merchant_console.py` |
| TC-TOP-005 | 场所管理 | 场所可新建、重命名、删除；场所下有区域时拒绝删除 | `tests/test_merchant_console.py`、`tests/test_storage.py` |
| TC-INC-020 | 险情清单导出 | 导出当前筛选结果为 Excel 可直接打开的 CSV，包含地点快照与处置记录 | `tests/test_incidents_assistant.py`、`tests/test_merchant_console.py` |
| TC-INC-021 | 通知失败持续补发 | 失败次数不再封顶；重试等待随失败次数指数增长；显式次数上限仍可限制 | `tests/test_incident_notifications.py`、`tests/test_storage.py` |
| TC-INC-022 | 历史险情检索 | 搜索可命中快照之外的历史记录，并匹配地点快照名称 | `tests/test_merchant_console.py` |
| TC-INC-023 | 已关闭险情停止补发 | 已解决或误报的险情不再自动重试通知，失败记录仍可见并可手动重试 | `tests/test_incident_notifications.py` |
| TC-INC-024 | 场所名称快照 | 险情保存事发时的场所名称，场所撤并后导出报告仍显示原门店 | `tests/test_incidents_assistant.py` |
| TC-INC-025 | 列表与导出口径一致 | 险情列表与导出文件显示同一处事发地点，区域改名后不改变历史记录 | `tests/test_merchant_console.py` |
| TC-DEV-055 | 国标通道号规范化 | 告警通道号带 `:ss` 后缀时仍归属到正确的摄像头与区域 | `tests/test_merchant_console.py` |
| TC-INC-026 | 问询回答可操作 | 离线与险情回答限制条数、给出总数，并指向可处理的页面 | `tests/test_incidents_assistant.py` |
| TC-DEV-056 | 模拟器跨进程接入 | 按文档以子进程启动模拟器后，平台可完成 ONVIF 认证、发现通道并取得码流地址 | `tests/test_camera_simulator.py` |
| TC-DEV-057 | 国标跨进程接入 | 子进程模拟器按文档注册到真实 GB 平台，平台收到目录与告警事件 | `tests/test_camera_simulator.py` |
| TC-CFG-033 | 配置并发写入 | 多线程同时保存配置时互不覆盖，且不残留临时文件 | `tests/test_config.py` |
| TC-INC-027 | 搜索结果一致性 | 数据库预筛后仍按区域、摄像头与快照名称匹配，结果与全量扫描一致 | `tests/test_merchant_console.py` |
| TC-DEP-003 | 启动失败指引 | 数据损坏、文件被占用和通用失败分别给出可操作的中文指引 | `tests/test_merchant_console.py` |
| TC-UI-059 | 场所维度筛选 | 实时画面可按场所筛选，区域筛选随之联动到该场所 | `tests/test_merchant_console.py` |
| TC-INC-028 | 处置说明留痕 | 处置备注随状态变更写入审计并显示在处置记录中 | `tests/test_merchant_console.py` |
| TC-DEV-058 | 国标告警端到端 | 真实 GB 平台收到的告警进入险情中心，等级与来源正确 | `tests/test_merchant_console.py` |
| TC-DEV-059 | ONVIF 告警下发 | 模拟器可下发 ONVIF 告警，平台完成订阅并收到事件 | `tests/test_camera_simulator.py` |
| TC-DEV-060 | 退出释放订阅资源 | 退出后订阅线程不再持有数据库连接，订阅失败会提示原因 | `tests/test_merchant_console.py` |
| TC-DEV-061 | ONVIF 生产链路 | 已注册设备订阅后，模拟器告警真实送达平台且退出不报错 | `tests/test_device_integration.py` |
| TC-INC-029 | 险情列表上限 | 列表最多渲染 200 条并提示总数与剩余条数 | `tests/test_merchant_console.py` |
| TC-UI-060 | 工作台提示通知配置错误 | 通知配置无效时工作台概览显示原因与修正入口 | `tests/test_merchant_console.py` |
| TC-INC-030 | ONVIF 通道号后缀 | 告警通道号带 /Profile_x 后缀时仍归属到正确摄像头 | `tests/test_merchant_console.py` |
| TC-UI-061 | 国标服务状态分级 | 无设备注册、已注册未上报目录、待导入和全部导入分别显示不同状态与提示 | `tests/test_merchant_console.py` |
| TC-UI-062 | 国标导入去重视图 | 只有国标协议的已导入通道计入导入数量，ONVIF 设备不参与统计 | `tests/test_merchant_console.py` |
| TC-NET-001 | HTTP 错误响应释放 | 文字问询与通知在收到 HTTP 错误时关闭响应，不泄漏套接字 | `tests/test_net.py`、`tests/test_incidents_assistant.py` |
| TC-OPS-001 | 机器繁忙不误判 | 整机 CPU 满载时健康检查仍返回成功并给出说明，只有磁盘或内存不足才失败 | `tests/test_main.py`、`tests/test_system_metrics.py` |
| TC-INC-031 | 区域别名匹配 | 上报险情时说“仓库”也能命中“成品仓库”，并写入区域快照 | `tests/test_incidents_assistant.py` |
| TC-INC-032 | 区域歧义不猜测 | 两个区域同样匹配时险情不带区域，并提示在险情中心补充位置 | `tests/test_incidents_assistant.py` |
| TC-INC-033 | 摄像头短名匹配 | 说“东大门”可命中“东大门高清摄像头”，并继承其区域 | `tests/test_incidents_assistant.py` |
| TC-INC-034 | 险情补充位置 | 详情页可为无区域险情选择区域，刷新快照并写入处置记录 | `tests/test_merchant_console.py`、`tests/test_incidents_assistant.py` |
| TC-UI-063 | 筛选数量口径 | 实时画面、设备页与险情中心在筛选时同时显示命中数与总数 | `tests/test_merchant_console.py` |
| TC-DEV-062 | 订阅异常可见 | 告警订阅断开的摄像头在卡片上直接显示异常与原因 | `tests/test_device_integration.py` |
| TC-OPS-002 | 聊天记录清理 | 超过保留期的问询记录随定期清理删除，近期记录保留 | `tests/test_storage.py` |
| TC-BB-001 | 客户录像可播放 | 客户提供的真实录像经模拟器可取到非纯色画面 | `tests/test_blackbox_real_media.py` |
| TC-BB-002 | 真实录像接入 | `main.py probe-rtsp` 对真实录像返回 ok 且尺寸正确 | `tests/test_blackbox_real_media.py` |
| TC-BB-003 | 画面保真度 | 交付帧的水平梯度不低于源文件的 75%，丢包会立即失败 | `tests/test_blackbox_real_media.py` |
| TC-BB-004 | 出厂配置预检 | 真实入口 health 退出码为 0 且无 error 检查项 | `tests/test_blackbox_real_media.py` |
| TC-BB-005 | 录像接入后拓扑 | 客户录像接入后摄像头与设备数量正确可读 | `tests/test_blackbox_real_media.py` |
| TC-DOC-001 | 交付证据齐备 | 六个页面截图与控制台巡览录像存在且非空 | `tests/test_docs.py` |

## 7. 回归测试

| ID | 用例 | 预期结果 | 自动化 |
| --- | --- | --- | --- |
| TC-REG-001 | 全量 pytest | 全部测试通过 | `python -m pytest -q` |
| TC-REG-002 | ResourceWarning | 以 ResourceWarning 为错误运行仍通过 | `-W error::ResourceWarning` |
| TC-REG-003 | 真实入口帮助 | `python src/main.py --help` 正常且只列出当前命令 | `test_acceptance_blackbox.py` |
| TC-REG-004 | 编译检查 | 所有 Python 文件可编译 | `python -m compileall -q src tests tools` |
| TC-REG-005 | Ruff 静态检查 | 无 E/F/I/UP/B/SIM/RUF 错误 | `ruff check src tests tools` |
| TC-REG-006 | Ruff 格式检查 | 源码和测试格式一致 | `ruff format --check src tests tools` |
| TC-REG-007 | 文档链接 | 交付文档链接全部可达 | `tests/test_docs.py` |
| TC-REG-008 | 新部署路径 | 数据库父目录尚不存在时健康检查仍可采样磁盘 | `tests/test_system_metrics.py` |
| TC-REG-009 | 测试窗口释放 | UI 测试逐项关闭并销毁顶层窗口，进程正常退出 | `tests/test_merchant_console.py` |
