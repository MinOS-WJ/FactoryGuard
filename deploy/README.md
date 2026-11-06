# 现场启动入口

完整部署与运维文档见 [部署手册](../docs/operations/01-deployment.md) 和 [维护手册](../docs/operations/03-maintenance.md)。

`deploy/start.ps1` 用于在 Windows 现场机器启动 FactoryGuard，不要求开发者记住完整命令。

先激活已有 Conda 环境：

```powershell
conda activate factoryguard
```

运行部署自检：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Check -Url rtsp://localhost/live
```

自检会检查 Python 依赖、数据库、FFmpeg、媒体目录，并对 RTSP 地址读取一帧。关键检查失败时脚本返回非零退出码，可用于部署自动化；默认警告不会阻断启动，增加 `-StrictCheck` 后警告也会返回非零退出码。

启动桌面控制台：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode gui
```

仅启动托盘模式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode tray
```

可选参数：

```text
-Mode       gui 或 tray，默认 gui
-Url        可选 RTSP 地址，仅用于 -Check 健康探测
-Database   SQLite 路径，默认 data/factoryguard.db
-Config     JSON 软件配置路径，默认 config/factoryguard.json
-MediaDir   事件媒体目录，默认 events-media
-Python     Python 解释器路径；也可设置 FACTORYGUARD_PYTHON
-Check      只运行健康探测，不启动界面
-StrictCheck  健康探测并启用严格模式，警告也返回非零退出码
```

## PyInstaller 单目录打包

仓库根目录的 `FactoryGuard.spec` 负责单目录（one-folder）打包，产物写入 `build/FactoryGuard/`。
打包**不做精简**：Qt 资源、完整 FFmpeg 工具链、配置样例和交付文档都会一起复制进去。

先安装打包依赖：

```powershell
conda activate factoryguard
python -m pip install pyinstaller
```

执行打包：

```powershell
python -m PyInstaller FactoryGuard.spec --noconfirm --distpath build --workpath build/_pyinstaller
```

产物结构：

```text
build/FactoryGuard/
├─ FactoryGuard.exe
└─ _internal/
   ├─ factoryguard/          程序代码
   ├─ PySide6/               Qt 运行时
   ├─ src/third_party/ffmpeg-9.0.2-full_build/bin/ffmpeg.exe
   ├─ config/factoryguard.example.json
   └─ docs/                  交付与运维文档
```

打包后自检：

```powershell
build\FactoryGuard\FactoryGuard.exe --help
build\FactoryGuard\FactoryGuard.exe health --db data\factoryguard.db --media-dir events-media
```

`health` 输出中的 `FFmpeg` 路径应指向 `_internal/src/third_party/...`，说明打包内置的
FFmpeg 已被正确找到。数据目录与配置目录默认相对**当前工作目录**生成，因此建议从
`build/FactoryGuard/` 目录内启动，或在快捷方式中把工作目录设为该目录。

说明：

- 打包版保留了控制台窗口（`console=True`），以便 `health` / `probe-rtsp` / `autostart`
  等命令继续输出结果；如果只需要无窗口的图形版，可把 spec 里的 `console` 改为 `False` 后重打包。
- `build/` 已在 `.gitignore` 中排除，重新打包不会污染仓库。
- 开机自启动在打包版中直接指向 `FactoryGuard.exe`，并把快捷方式工作目录设为可执行文件所在目录。
