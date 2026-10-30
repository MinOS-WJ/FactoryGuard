# 外部测试数据注入工具

`tools/seed_demo_data.py` 是开发/验收用的外部工具，不属于 FactoryGuard 软件本体。软件 GUI 和 CLI 不会主动写入演示数据。

## 用途

向指定 SQLite 数据库写入一套用于查看结构化规则编辑器的测试拓扑和草稿规则：

- 演示厂区
- 东门、成品仓库
- 两个摄像头、两条推流
- 区域入侵
- 过线计数
- 区域人数
- AND/OR/NOT 组合条件示例

所有规则默认是 `draft`，不会进入生产运行时。

## 使用方法

在项目根目录执行：

```powershell
python tools/seed_demo_data.py --db data/factoryguard.db
```

然后用同一个数据库启动软件：

```powershell
python src/main.py gui --db data/factoryguard.db
```

进入“拓扑与设备”页面：

1. 选择演示推流。
2. 点击“新增规则”或“编辑规则”。
3. 也可以双击规则打开结构化规则编辑器。
4. 在编辑器中查看几何标定、条件参数、动作配置和规则试运行。

## 参数

```text
--db      SQLite 数据库路径，默认 data/factoryguard.db
--actor   写入审计记录的操作人，默认 external-demo
```

示例：

```powershell
python tools/seed_demo_data.py --db data/factoryguard.db --actor tester
```

## 数据覆盖行为

- 会创建/更新演示拓扑实体。
- 会创建/更新 4 条固定 ID 的演示规则。
- 不会删除数据库中的其他站点、规则、事件或审计记录。
- 如果要完全重新开始，请删除目标测试数据库文件后重新执行，或使用外部数据库工具清空指定表。
- 不要把该工具用于生产数据库。

## 验证注入结果

可以用软件的“拓扑与设备”页面查看实体和规则，也可以检查数据库中是否存在：

- `demo-site`
- `demo-area-gate`
- `demo-area-warehouse`
- `demo-stream-gate-main`
- `demo-stream-warehouse-main`
- `demo-rule-intrusion`
- `demo-rule-line-count`
- `demo-rule-occupancy`
- `demo-rule-advanced`





## 设备协议诊断

`device_probe.py` 只做现场诊断，不写入 FactoryGuard 数据库，也不生成演示设备。

- `discover-onvif`：向局域网发送 WS-Discovery Probe，输出设备 XAddr、Scopes 和 Types。
- `probe-rtsp`：使用现有 FFmpeg 适配器读取一路首帧，检查 RTSP 取流。
- `probe-onvif`：执行 ONVIF GetDeviceInformation、GetServices，显示认证结果和已探测能力。
- `sip-digest`：按 GB/T 28181 REGISTER 语义生成 Digest Authorization，便于核对 SIP 设备配置。

密码可通过 `--password-env` 从环境变量读取。输出中的 URL、口令和令牌不写入日志；命令行传入的明文密码可能出现在本机进程列表中，现场排障优先使用环境变量。
