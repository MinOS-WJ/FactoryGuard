from __future__ import annotations

"""Field diagnostics for RTSP, ONVIF and GB/T 28181 devices.

Examples:
    python tools/device_probe.py discover-onvif --timeout 3
    python tools/device_probe.py probe-rtsp rtsp://192.168.1.64/live
    python tools/device_probe.py probe-onvif http://192.168.1.64/onvif/device_service --username admin --password-env CAMERA_PASSWORD
    python tools/device_probe.py sip-digest --username 340200000000000001 --password-env GB_PASSWORD --realm 3402000000 --uri sip:340200000000000001@3402000000
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factoryguard.devices.base import DeviceCredentials  # noqa: E402
from factoryguard.devices.gb28181.sip import digest_response  # noqa: E402
from factoryguard.devices.onvif import OnvifAdapter, OnvifDiscovery  # noqa: E402
from factoryguard.devices.rtsp import RtspAdapter  # noqa: E402
from factoryguard.domain.models import DeviceProtocol, VideoDevice  # noqa: E402


def _password(args: argparse.Namespace) -> str:
    if args.password_env:
        return os.environ.get(args.password_env, "")
    return args.password or ""


def _device(protocol: DeviceProtocol, endpoint: str) -> VideoDevice:
    return VideoDevice(
        id="probe-device",
        site_id="probe-site",
        area_id="probe-area",
        name="现场设备",
        protocol=protocol,
        endpoint=endpoint,
    )


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="device_probe", description="FactoryGuard 现场设备诊断工具")
    commands = parser.add_subparsers(dest="command", required=True)

    discover = commands.add_parser("discover-onvif", help="发送 WS-Discovery 并列出发现的 ONVIF 设备")
    discover.add_argument("--timeout", type=float, default=3.0)

    rtsp = commands.add_parser("probe-rtsp", help="读取一路 RTSP 首帧并检查分辨率")
    rtsp.add_argument("url")
    rtsp.add_argument("--timeout", type=float, default=8.0)

    onvif = commands.add_parser("probe-onvif", help="检查 ONVIF 认证、设备信息和能力")
    onvif.add_argument("endpoint")
    onvif.add_argument("--username", default="")
    onvif.add_argument("--password", default="")
    onvif.add_argument("--password-env", default="")
    onvif.add_argument("--timeout", type=float, default=6.0)

    digest = commands.add_parser("sip-digest", help="生成 GB/T 28181 REGISTER Digest 响应")
    digest.add_argument("--username", required=True)
    digest.add_argument("--password", default="")
    digest.add_argument("--password-env", default="")
    digest.add_argument("--realm", required=True)
    digest.add_argument("--nonce", required=True)
    digest.add_argument("--uri", required=True)
    digest.add_argument("--method", default="REGISTER")

    args = parser.parse_args(argv)
    if args.command == "discover-onvif":
        found = OnvifDiscovery().discover(timeout=args.timeout)
        _print([item.__dict__ for item in found])
        return 0
    if args.command == "probe-rtsp":
        adapter = RtspAdapter(_device(DeviceProtocol.RTSP, args.url))
        try:
            _print(adapter.probe(timeout=args.timeout).__dict__)
        finally:
            adapter.close()
        return 0
    if args.command == "probe-onvif":
        credentials = DeviceCredentials(username=args.username, password=_password(args))
        adapter = OnvifAdapter(_device(DeviceProtocol.ONVIF, args.endpoint), credentials)
        try:
            result = adapter.probe(timeout=args.timeout)
            _print(
                {
                    "ok": result.ok,
                    "detail": result.detail,
                    "latency_ms": result.latency_ms,
                    "info": result.info.as_dict() if result.info else None,
                    "capabilities": result.capabilities.as_dict(),
                    "diagnostics": dict(result.diagnostics),
                }
            )
            return 0 if result.ok else 2
        finally:
            adapter.close()
    response = digest_response(
        username=args.username,
        password=_password(args),
        realm=args.realm,
        nonce=args.nonce,
        method=args.method,
        uri=args.uri,
    )
    _print({"authorization": response})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
