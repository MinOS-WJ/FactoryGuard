# 现场启动入口

`deploy/start.ps1` 用于在 Windows 现场机器启动 FactoryGuard，不要求开发者记住完整命令。

先激活已有 Conda 环境：

```powershell
conda activate factoryguard
```

运行部署自检：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Check -Url rtsp://localhost/live
```

自检会检查 Python 依赖、数据库、FFmpeg、ONNX 模型、媒体目录，并对 RTSP 地址读取一帧。关键检查失败时脚本返回非零退出码，可用于部署自动化；默认警告不会阻断启动，增加 `-StrictCheck` 后警告也会返回非零退出码。

启动桌面控制台：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode gui -Url rtsp://localhost/live
```

仅启动托盘模式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode tray -Url rtsp://localhost/live
```

可选参数：

```text
-Mode       gui 或 tray，默认 gui
-Url        RTSP 地址，默认 rtsp://localhost/live
-Database   SQLite 路径，默认 data/factoryguard.db
-MediaDir   事件媒体目录，默认 events-media
-Model     ONNX 模型路径，默认 models/yolo11n.onnx
-Python     Python 解释器路径；也可设置 FACTORYGUARD_PYTHON
-Check      只运行健康探测，不启动界面
-StrictCheck  健康探测并启用严格模式，警告也返回非零退出码
```