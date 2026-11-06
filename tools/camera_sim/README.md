# 多摄像头协议模拟器

用于在没有真实摄像头的情况下验证 FactoryGuard 的 ONVIF、GB/T 28181 和 RTSP 接入链路。

## 能力

- 一次启动多台虚拟摄像头；
- 每台摄像头生成独立颜色的测试画面；
- 本地 RTSP 服务支持多个同时观看的客户端；
- ONVIF HTTP 设备服务、设备信息、能力、Profile、通道和码流地址；
- ONVIF WS-Discovery 响应（端口 3702 可用时）；
- GB/T 28181 注册、Digest 认证、目录、Keepalive、告警；
- GB/T 28181 INVITE 后通过 RTP/MPEG-TS 推送实时测试画面；
- JSON 输出摄像头端点，便于自动化测试。

## 启动

默认启动 4 台摄像头，ONVIF 和 GB/T 28181 交替分配：

```powershell
python -m tools.camera_sim
```

指定数量和协议：

```powershell
python -m tools.camera_sim --count 8 --protocols onvif,gb28181
```

仅启动 ONVIF：

```powershell
python -m tools.camera_sim --count 3 --protocols onvif
```

仅启动国标设备，并连接本机 FactoryGuard：

```powershell
python -m tools.camera_sim --count 3 --protocols gb28181 --sip-host 127.0.0.1 --sip-port 5060 --password 你的国标密码
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--count` | 摄像头数量 |
| `--protocols` | `onvif`、`gb28181`，逗号分隔 |
| `--rtsp-port` | 本地 RTSP 服务端口，默认 8554；设为 0 自动分配 |
| `--onvif-port` | ONVIF HTTP 服务端口，默认 8000；设为 0 自动分配 |
| `--sip-host` | FactoryGuard 国标平台地址 |
| `--sip-port` | FactoryGuard 国标平台端口 |
| `--platform-id` | 国标平台编号 |
| `--domain` | SIP 域 |
| `--password` | 国标注册密码 |
| `--wait` | 等待国标注册完成的秒数 |
| `--send-alarm` | 注册完成后发送一次告警 |
| `--duration` | 运行时长，0 表示持续到 Ctrl+C |
| `--json` | 以 JSON 输出摄像头端点 |
| `--media` | 用真实录像文件作为摄像头画面，可重复指定并按顺序轮换 |

## 用真实录像当信号源

现场联调时可以直接把客户自己的监控录像当成摄像头信号：

```powershell
python -m tools.camera_sim --count 2 --protocols onvif `
    --media "D:\录像\L107.mp4" --media "D:\录像\L108.mp4"
```

录像会循环播放，RTP 接收缓冲区已提升到 8 MB，高清素材不会出现竖条纹。

## 输出示例

```text
Camera simulator ready=True · ffmpeg=...\ffmpeg.exe
01  onvif   模拟摄像头 01  id=http://127.0.0.1:8000/onvif/sim-cam-01/device  rtsp=rtsp://127.0.0.1:8554/sim-cam-01
02  gb28181 模拟摄像头 02  id=34020000001320000002  rtsp=rtsp://127.0.0.1:8554/sim-cam-02
```

JSON 模式适合测试脚本读取端口和设备编号：

```powershell
python -m tools.camera_sim --count 2 --protocols onvif --json
```

## 接入 FactoryGuard

### ONVIF

1. 启动模拟器；
2. 在“区域与设备”选择 ONVIF；
3. 填写输出中的 `onvif_endpoint`；
4. 账号密码可留空；
5. 保存后即可获取通道和 RTSP 码流。

### GB/T 28181

1. 在 FactoryGuard 设置页启用国标服务；
2. 使用相同的平台编号、SIP 域、端口和密码启动模拟器；
3. 等待输出 `ready=True`；
4. 在设置页选择“导入已注册设备”；
5. 打开实时画面验证点播。

## 自动化验证

```powershell
python -m pytest -q tests/test_camera_simulator.py -vv
```

覆盖 ONVIF 探测与通道、RTSP 首帧、GB/T 28181 注册/目录/告警和 INVITE 后 RTP 首帧。完整回归仍应对真实协议栈和现场设备执行联调。

## 限制

- ONVIF 模拟器当前提供 Profile S 视频能力，不模拟录像检索和 PTZ；
- ONVIF WS-Discovery 需要 UDP 3702 未被占用；
- 国标模拟器每个设备当前提供一个通道；
- 录像回放、云台控制和厂商私有扩展不在模拟范围内；
- 仅用于本地测试，不应暴露到生产网络。
