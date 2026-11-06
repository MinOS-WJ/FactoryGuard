# 部署手册

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11 x64 |
| Python | 3.11+，当前验证版本 3.14.6 |
| 桌面框架 | PySide6 6.12.0 |
| 视频工具 | FFmpeg，仓库内包含时自动发现 |
| 网络 | 摄像头/NVR 与主机在同一可路由局域网 |

## 2. 依赖安装

```powershell
conda activate factoryguard
python -m pip install -r requirements.txt
```

验证依赖：

```powershell
python -c "import PySide6, numpy; print('ok')"
```

## 3. 目录准备

```text
FactoryGuard/
├── config/factoryguard.json        # 首次保存时创建，不提交仓库
├── data/factoryguard.db            # 业务数据
├── events-media/                   # 事件媒体，按需创建
└── src/
```

复制配置模板：

```powershell
Copy-Item config\factoryguard.example.json config\factoryguard.json
```

## 4. 配置

编辑 `config/factoryguard.json`：

- `llm.enabled`：是否启用大模型增强；
- `llm.endpoint`：Chat Completions 兼容地址；
- `llm.model`：模型 ID；
- `llm.api_key`：API Key，明文；
- `gb28181.enabled`：是否启用国标服务；
- `gb28181.platform_id/domain/port/password`：国标参数。

也可在软件“系统设置”中配置和保存。

## 5. 启动

```powershell
python src/main.py
```

后台启动：

```powershell
python src/main.py tray
```

指定配置：

```powershell
python src/main.py gui --config config\factoryguard.local.json
```

## 6. 使用部署脚本

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode gui
```

后台模式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode tray
```

参数包括 `-Database`、`-Config`、`-MediaDir`、`-Python`、`-Check` 和 `-StrictCheck`。

## 7. 开机自动值守

启用后，Windows 登录时会自动在后台启动控制台并保持值守。

在软件“系统设置 → 本地运行环境”中勾选“开机后自动在后台值守”，或使用命令行：

~~~powershell
python src/main.py autostart            # 查询当前状态
python src/main.py autostart --enable   # 启用
python src/main.py autostart --disable  # 停用
~~~

说明：

- 使用当前用户的“启动”文件夹快捷方式，不需要管理员权限，也不修改注册表；
- 快捷方式以托盘模式启动，控制台不会弹出窗口；
- 停用时直接删除该快捷方式即可，不影响已有配置和数据。

## 8. 防火墙和网络

- 允许本机访问摄像头 RTSP/HTTP 端口；
- 国标模式需允许配置的 SIP UDP/TCP 端口；
- 不将摄像头和 NVR 管理端口直接暴露到公网；
- 建议将视频设备放在独立 VLAN。

## 9. 启动检查

1. 启动画面完成并进入工作台；
2. 系统设置显示正确配置文件路径；
3. 关闭窗口后托盘图标仍在；
4. 双击托盘可重新打开；
5. 托盘退出后进程消失；
6. 刷新状态后摄像头在线/离线正确变化。

## 10. 故障排查

| 现象 | 检查 |
| --- | --- |
| 启动提示配置文件错误 | 校验 JSON 语法和路径 |
| 摄像头无法连接 | 检查地址、账号、端口、编码和网络 |
| 国标服务启动失败 | 检查 SIP 端口占用和平台编号 |
| 没有托盘图标 | 检查 Windows 通知区域设置 |
| 大模型连接失败 | 检查 endpoint、model、key 和网络代理 |
