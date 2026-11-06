"""Command-line entry point for the multi-camera simulator."""

from __future__ import annotations

import argparse
import json
import signal
import time
from math import isfinite
from pathlib import Path
from threading import Event

from factoryguard.media.ffmpeg import require_ffmpeg

from .service import SUPPORTED_PROTOCOLS, CameraSimulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run multiple ONVIF and GB/T 28181 virtual cameras"
    )
    parser.add_argument("--count", type=int, default=4, help="number of virtual cameras")
    parser.add_argument(
        "--protocols",
        default="onvif,gb28181",
        help="comma-separated protocols: onvif,gb28181",
    )
    parser.add_argument("--rtsp-host", default="127.0.0.1")
    parser.add_argument("--rtsp-port", type=int, default=8554)
    parser.add_argument("--onvif-host", default="127.0.0.1")
    parser.add_argument("--onvif-port", type=int, default=8000)
    parser.add_argument(
        "--sip-host", default="127.0.0.1", help="FactoryGuard GB28181 platform host"
    )
    parser.add_argument(
        "--sip-port", type=int, default=5060, help="FactoryGuard GB28181 platform port"
    )
    parser.add_argument("--platform-id", default="340200000000000000")
    parser.add_argument("--domain", default="3402000000")
    parser.add_argument("--password", default="", help="GB28181 registration password (plain text)")
    parser.add_argument(
        "--wait", type=float, default=10.0, help="GB28181 registration wait timeout"
    )
    parser.add_argument(
        "--duration", type=float, default=0.0, help="run seconds; 0 means until Ctrl+C"
    )
    parser.add_argument(
        "--send-alarm", action="store_true", help="send one GB28181 alarm after startup"
    )
    parser.add_argument(
        "--media",
        action="append",
        default=[],
        help="video file to use as the camera source; repeat for several cameras",
    )
    parser.add_argument(
        "--stop-file",
        default="",
        help="exit cleanly when this file appears",
    )
    parser.add_argument("--json", action="store_true", help="print endpoint snapshot as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    protocols = tuple(value.strip() for value in str(args.protocols).split(",") if value.strip())
    invalid = [value for value in protocols if value not in SUPPORTED_PROTOCOLS]
    if invalid:
        parser.error(f"unsupported protocols: {', '.join(invalid)}")
    if args.count < 1:
        parser.error("count must be positive")
    if args.rtsp_port < 0 or args.onvif_port < 0 or not 1 <= args.sip_port <= 65535:
        parser.error("ports must be non-negative; SIP port must be between 1 and 65535")
    if not isfinite(args.wait) or args.wait < 0:
        parser.error("wait must be a non-negative finite number")
    if not isfinite(args.duration) or args.duration < 0:
        parser.error("duration must be a non-negative finite number")
    try:
        ffmpeg = require_ffmpeg()
        simulator = CameraSimulator(
            count=args.count,
            protocols=protocols,
            media_sources=tuple(args.media),
            rtsp_host=args.rtsp_host,
            rtsp_port=args.rtsp_port,
            onvif_host=args.onvif_host,
            onvif_port=args.onvif_port,
            platform_host=args.sip_host,
            platform_port=args.sip_port,
            platform_id=args.platform_id,
            domain=args.domain,
            password=args.password,
            ffmpeg=ffmpeg,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    previous = signal.signal(signal.SIGINT, request_stop)
    # CTRL_BREAK_EVENT raises SIGBREAK on Windows.  Handling it lets the
    # simulator release its FFmpeg helpers instead of leaving them behind.
    previous_break = None
    if hasattr(signal, "SIGBREAK"):
        previous_break = signal.signal(signal.SIGBREAK, request_stop)
    try:
        simulator.start()
        ready = simulator.wait_until_ready(timeout=args.wait) if "gb28181" in protocols else True
        snapshot = simulator.snapshot()
        if args.json:
            print(json.dumps({"ready": ready, "cameras": snapshot}, ensure_ascii=False, indent=2))
        else:
            print(f"Camera simulator ready={ready} · ffmpeg={ffmpeg.executable}")
            for camera in snapshot:
                endpoint = camera["onvif_endpoint"] or camera["device_id"]
                print(
                    f"{camera['index']:02d}  {camera['protocol']:<7}  {camera['name']}  "
                    f"id={endpoint}  rtsp={camera['rtsp_url']}"
                )
            print("Press Ctrl+C to stop.")
        if args.send_alarm and "gb28181" in protocols:
            print(f"sent_alarms={simulator.send_alarm()}")
        deadline = time.monotonic() + args.duration if args.duration > 0 else None
        stop_file = Path(args.stop_file) if args.stop_file else None
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            # A stop file lets the launcher request a graceful shutdown so
            # the simulator can release its FFmpeg helpers before exiting.
            if stop_file is not None and stop_file.exists():
                break
            time.sleep(0.2)
    finally:
        signal.signal(signal.SIGINT, previous)
        if previous_break is not None and hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, previous_break)
        simulator.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
