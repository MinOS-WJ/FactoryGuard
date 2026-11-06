# FactoryGuard 工具说明

这里的脚本用于演示、现场诊断和兼容性验证，不属于商户工作台主流程。

## 商户工作台演示数据

`seed_merchant_demo.py` 会写入一套适合展示新商户控制台的数据：

- 一个综合园区；
- 东门、成品仓库、装配车间、访客停车场四个区域；
- 六台 ONVIF / GB/T 28181 / RTSP 摄像头；
- 在线、离线、待检测三种设备状态；
- 高危、关注、提示三个等级的险情记录。

```powershell
python tools/seed_merchant_demo.py --db data/factoryguard.db
python src/main.py
```

该脚本不会删除数据库中的其他记录。不要在正式生产库中反复注入演示数据。

## 设备协议诊断

`device_probe.py` 只做现场诊断，不写入 FactoryGuard 数据库，也不生成演示设备。

- `discover-onvif`：向局域网发送 WS-Discovery Probe，输出设备 XAddr、Scopes 和 Types。
- `probe-rtsp`：使用现有 FFmpeg 适配器读取一路首帧，检查 RTSP 取流。
- `probe-onvif`：执行 ONVIF GetDeviceInformation、GetServices，显示认证结果和已探测能力。
- `sip-digest`：按 GB/T 28181 REGISTER 语义生成 Digest Authorization，便于核对 SIP 设备配置。

密码可通过 `--password-env` 从环境变量读取。输出中的 URL、口令和令牌不写入日志；命令行传入的明文密码可能出现在本机进程列表中，现场排障优先使用环境变量。

## 多摄像头模拟器

`camera_sim/` 提供多路 ONVIF、GB/T 28181 和 RTSP 虚拟摄像头，可在无真实设备时测试接入、通道发现、注册、告警和实时点播。

```powershell
python -m tools.camera_sim --count 4 --protocols onvif,gb28181
```

验证模拟器链路：

```powershell
python -m pytest -q tests/test_camera_simulator.py -vv
```

### 用真实录像作为摄像头信号源

模拟器默认使用 FFmpeg 生成的测试图案。现场联调时可以直接把客户自己的监控录像
当作摄像头信号，用 `--media` 指定文件路径（可重复指定，按摄像头顺序轮换）：

```powershell
python -m tools.camera_sim --count 3 --protocols onvif,gb28181 `
    --media "D:\录像\L107.mp4" --media "D:\录像\L108.mp4"
```

录像会被循环播放，RTP 通道已把接收缓冲区提升到 8 MB，高清素材不会出现竖条纹。

## 交付证据采集

`capture_evidence.py` 会启动真实商户控制台，逐页截图并合成一段巡览录像：

```powershell
python -m tools.capture_evidence --db data/evidence.db --config config/evidence.json --out docs/testing/evidence
```

`record_footage.py` 用客户真实录像录制平台实时画面，用于交付答辩：

```powershell
python -m tools.record_footage --out docs/testing/evidence --fps 6 --seconds 14
```

两个脚本都会写入 `docs/testing/evidence/`，并生成 `manifest.json` 记录清单。

当前边界：ONVIF 仅 Profile S，国标每设备单通道，不模拟 PTZ、录像回放和平台级联。详细用法见 [camera_sim/README.md](camera_sim/README.md)。
