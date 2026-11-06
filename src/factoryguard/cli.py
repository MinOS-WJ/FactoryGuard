from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from .config import MerchantConfig
from .media.ffmpeg import find_ffmpeg, require_ffmpeg
from .media.rtsp import RTSPCapture
from .notifications.adapters import (
    EmailAdapter,
    SmsHttpAdapter,
    parse_phone_recipients,
    parse_recipients,
)
from .notifications.channels import channel_catalog
from .notifications.dispatcher import OutboxDispatcher
from .storage.retention import MediaRetentionManager
from .storage.sqlite import SQLiteStore
from .system_metrics import ResourceSampler, classify_resource_state, format_bytes


def _redact_url(value: str) -> str:
    return re.sub(r"((?:rtsp|rtsps|https?)://)[^/@\s]+@", r"\1***:***@", value, flags=re.IGNORECASE)


def _redact_text(value: object) -> str:
    return _redact_url(str(value))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    # Launching the project entry point without arguments should be useful for
    # desktop users.  Keep the explicit subcommands available while making
    # ``python src/main.py`` is equivalent to ``python src/main.py gui``.
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["gui"]

    parser = argparse.ArgumentParser(
        prog="factoryguard",
        description="FactoryGuard 智守：多摄像头、多区域管理平台与现场值守问询。直接运行 python src/main.py 即可打开商户工作台。",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{probe-rtsp,health,channels,autostart,gui,tray,dispatch-outbox,cleanup-media}",
    )
    probe = subparsers.add_parser(
        "probe-rtsp", help="check camera stream connectivity and read one frame"
    )
    probe.add_argument(
        "--url", default="rtsp://localhost/live", help="RTSP source to probe for one frame"
    )
    probe.add_argument("--width", type=int, default=1280, help="processing frame width")
    probe.add_argument("--height", type=int, default=720, help="processing frame height")
    probe.add_argument(
        "--timeout", type=float, default=10.0, help="connection and source probe timeout in seconds"
    )
    dispatch = subparsers.add_parser(
        "dispatch-outbox", help="deliver pending Webhook, SMTP and SMS notifications"
    )
    dispatch.add_argument("--db", default="data/factoryguard.db")
    dispatch.add_argument("--webhook-url", default=os.environ.get("FACTORYGUARD_WEBHOOK_URL", ""))
    dispatch.add_argument(
        "--webhook-secret", default=os.environ.get("FACTORYGUARD_WEBHOOK_SECRET", "")
    )
    dispatch.add_argument("--email-host", default=os.environ.get("FACTORYGUARD_EMAIL_HOST", ""))
    dispatch.add_argument(
        "--email-port", type=int, default=_env_int("FACTORYGUARD_EMAIL_PORT", 587)
    )
    dispatch.add_argument(
        "--email-username", default=os.environ.get("FACTORYGUARD_EMAIL_USERNAME", "")
    )
    dispatch.add_argument(
        "--email-password", default=os.environ.get("FACTORYGUARD_EMAIL_PASSWORD", "")
    )
    dispatch.add_argument("--email-sender", default=os.environ.get("FACTORYGUARD_EMAIL_SENDER", ""))
    dispatch.add_argument(
        "--email-recipients", default=os.environ.get("FACTORYGUARD_EMAIL_RECIPIENTS", "")
    )
    dispatch.add_argument(
        "--email-security",
        choices=("starttls", "ssl", "none"),
        default=os.environ.get("FACTORYGUARD_EMAIL_SECURITY", "starttls"),
    )
    dispatch.add_argument(
        "--sms-provider", default=os.environ.get("FACTORYGUARD_SMS_PROVIDER", "custom_http")
    )
    dispatch.add_argument("--sms-endpoint", default=os.environ.get("FACTORYGUARD_SMS_ENDPOINT", ""))
    dispatch.add_argument("--sms-token", default=os.environ.get("FACTORYGUARD_SMS_TOKEN", ""))
    dispatch.add_argument("--sms-sender", default=os.environ.get("FACTORYGUARD_SMS_SENDER", ""))
    dispatch.add_argument(
        "--sms-template-id", default=os.environ.get("FACTORYGUARD_SMS_TEMPLATE_ID", "")
    )
    dispatch.add_argument(
        "--sms-recipients", default=os.environ.get("FACTORYGUARD_SMS_RECIPIENTS", "")
    )
    dispatch.add_argument("--limit", type=int, default=100)
    dispatch.add_argument("--watch", action="store_true", help="keep polling until interrupted")
    dispatch.add_argument("--interval", type=float, default=5.0, help="seconds between watch polls")
    cleanup = subparsers.add_parser("cleanup-media", help="remove expired local event media")
    cleanup.add_argument(
        "--db",
        default="data/factoryguard.db",
        help="SQLite database containing event media references",
    )
    cleanup.add_argument(
        "--media-dir", default="events-media", help="local event media directory to clean"
    )
    cleanup.add_argument(
        "--retention-days",
        type=float,
        default=30.0,
        help="minimum age in days before media is eligible",
    )
    cleanup.add_argument(
        "--dry-run", action="store_true", help="report eligible media without deleting files"
    )
    health = subparsers.add_parser(
        "health", help="run local deployment and runtime preflight checks"
    )
    health.add_argument("--db", default="data/factoryguard.db", help="SQLite database to inspect")
    health.add_argument(
        "--media-dir", default="events-media", help="local event media directory to verify"
    )
    health.add_argument("--rtsp", default="", help="optional RTSP URL to probe with one frame")
    health.add_argument(
        "--rtsp-timeout", type=float, default=10.0, help="RTSP probe timeout in seconds"
    )
    health.add_argument(
        "--strict", action="store_true", help="return failure for warnings as well as errors"
    )
    subparsers.add_parser("channels", help="list notification channels and credential requirements")
    autostart = subparsers.add_parser(
        "autostart", help="inspect or change the Windows autostart shortcut"
    )
    autostart.add_argument(
        "--enable",
        action="store_true",
        help="start the console in the background when the user logs in",
    )
    autostart.add_argument(
        "--disable",
        action="store_true",
        help="remove the autostart shortcut",
    )
    autostart.add_argument("--python", default="", help="Python interpreter used by the shortcut")
    autostart.add_argument(
        "--entrypoint", default="", help="program entry point used by the shortcut"
    )
    for name, help_text in (
        ("gui", "open the merchant console"),
        ("tray", "open the merchant console in background mode"),
    ):
        desktop = subparsers.add_parser(name, help=help_text)
        desktop.add_argument("--db", default="data/factoryguard.db")
        desktop.add_argument(
            "--config",
            default=os.environ.get("FACTORYGUARD_CONFIG", "config/factoryguard.json"),
            help="JSON file for software settings and credentials",
        )
    args = parser.parse_args(argv)
    if args.command == "probe-rtsp":
        if args.width < 1 or args.height < 1 or not isfinite(args.timeout) or args.timeout <= 0:
            parser.error("probe dimensions and timeout must be positive")
        try:
            ffmpeg = require_ffmpeg()
            source_resolution = ffmpeg.probe_resolution(args.url, timeout=args.timeout)
            frame = RTSPCapture(
                ffmpeg,
                args.url,
                width=args.width,
                height=args.height,
            ).capture_one(timeout=args.timeout)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            parser.error(_redact_text(exc))
        frame_height, frame_width = frame.image.shape[:2]
        print(
            json.dumps(
                {
                    "url": _redact_url(args.url),
                    "ffmpeg": str(ffmpeg.executable),
                    "width": frame_width,
                    "height": frame_height,
                    "source_width": source_resolution[0] if source_resolution else None,
                    "source_height": source_resolution[1] if source_resolution else None,
                    "frame_no": frame.frame_no,
                    "status": "ok",
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "dispatch-outbox":
        if args.limit < 1 or not isfinite(args.interval) or args.interval <= 0:
            parser.error("--limit must be positive and --interval must be finite and positive")
        store = SQLiteStore(args.db)
        try:
            try:
                email_recipients = (
                    parse_recipients(args.email_recipients) if args.email_recipients.strip() else ()
                )
            except ValueError as exc:
                parser.error(_redact_text(exc))
            webhook_url = args.webhook_url.strip()
            webhook_secret = args.webhook_secret
            email_configured = bool(
                args.email_host.strip()
                or args.email_username.strip()
                or args.email_sender.strip()
                or email_recipients
            )
            adapters = {}
            if email_configured:
                try:
                    adapters["email"] = EmailAdapter(
                        host=args.email_host,
                        port=args.email_port,
                        username=args.email_username,
                        password=args.email_password,
                        sender=args.email_sender,
                        recipients=email_recipients,
                        use_ssl=args.email_security == "ssl",
                        starttls=args.email_security == "starttls",
                    )
                except ValueError as exc:
                    parser.error(_redact_text(f"invalid SMTP configuration: {exc}"))
            try:
                sms_recipients = (
                    parse_phone_recipients(args.sms_recipients)
                    if args.sms_recipients.strip()
                    else ()
                )
            except ValueError as exc:
                parser.error(_redact_text(exc))
            sms_configured = bool(
                args.sms_endpoint.strip()
                or args.sms_token
                or args.sms_sender.strip()
                or args.sms_template_id.strip()
                or sms_recipients
            )
            if sms_configured:
                try:
                    adapters["sms"] = SmsHttpAdapter(
                        endpoint=args.sms_endpoint,
                        recipients=sms_recipients,
                        provider=args.sms_provider,
                        token=args.sms_token,
                        sender=args.sms_sender,
                        template_id=args.sms_template_id,
                    )
                except ValueError as exc:
                    parser.error(_redact_text(f"invalid SMS configuration: {exc}"))
            if bool(webhook_url) != bool(webhook_secret):
                parser.error("--webhook-url and --webhook-secret must be provided together")
            if webhook_url and not webhook_url.lower().startswith("https://"):
                parser.error("--webhook-url must use HTTPS")
            if not webhook_url and not adapters:
                parser.error("configure at least one Webhook, SMTP or SMS delivery channel")
            dispatcher = OutboxDispatcher(
                webhook_url=webhook_url,
                webhook_secret=webhook_secret,
                adapters=adapters,
            )
            if args.watch:
                try:
                    while True:
                        report = dispatcher.dispatch_once(store, limit=args.limit)
                        print(json.dumps(asdict(report), ensure_ascii=False), flush=True)
                        time.sleep(args.interval)
                except KeyboardInterrupt:
                    return 0
            else:
                report = dispatcher.dispatch_once(store, limit=args.limit)
        finally:
            store.close()
        if not args.watch:
            print(json.dumps(asdict(report), ensure_ascii=False))
    elif args.command == "cleanup-media":
        if not isfinite(args.retention_days) or args.retention_days < 0:
            parser.error("--retention-days must be finite and non-negative")
        store = SQLiteStore(args.db)
        try:
            media_root = args.media_dir
            report = MediaRetentionManager(media_root=media_root).cleanup(
                store,
                retention_days=args.retention_days,
                dry_run=args.dry_run,
            )
        finally:
            store.close()
        print(json.dumps(asdict(report), ensure_ascii=False))

    elif args.command == "health":
        if not isfinite(args.rtsp_timeout) or args.rtsp_timeout <= 0:
            parser.error("rtsp-timeout must be positive")
        report = _health_report(args.db, args.media_dir, args.rtsp, args.rtsp_timeout)
        print(json.dumps(report, ensure_ascii=False))
        # Machine load depends on other programs, so it never blocks a
        # deployment check.  Strict mode is about our own setup.
        has_warning = any(
            item["state"] == "warn" and item["name"] != "机器负载" for item in report["checks"]
        )
        return 1 if not report["ok"] or (args.strict and has_warning) else 0
    elif args.command == "channels":
        print(
            json.dumps(
                {"channels": [descriptor.as_dict() for descriptor in channel_catalog()]},
                ensure_ascii=False,
            )
        )
    elif args.command == "autostart":
        from .app import autostart as autostart_module

        if args.enable and args.disable:
            parser.error("--enable and --disable cannot be combined")
        try:
            if args.enable:
                status = autostart_module.enable(
                    python_executable=args.python or None,
                    entrypoint=args.entrypoint or None,
                )
            elif args.disable:
                status = autostart_module.disable()
            else:
                status = autostart_module.describe()
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(_redact_text(exc))
        print(json.dumps(status.as_dict(), ensure_ascii=False))
    elif args.command in {"gui", "tray"}:
        try:
            from .ui.merchant_console import launch

            return launch(
                config=MerchantConfig(db=args.db, config_path=args.config),
                start_hidden=args.command == "tray",
                argv=["factoryguard", args.command],
            )
        except RuntimeError as exc:
            parser.error(_redact_text(exc))
    return 0


def _resource_hint(resource: object) -> str:
    """Explain a machine-load warning in plain language for the operator."""
    cpu = float(getattr(resource, "cpu_percent", 0.0) or 0.0)
    memory = float(getattr(resource, "memory_used_percent", 0.0) or 0.0)
    if memory >= 85:
        return f"内存已使用 {memory:.0f}%，请关闭占用内存较多的程序后重试"
    if cpu >= 75:
        return f"处理器占用 {cpu:.0f}%，通常由其他程序引起，平台仍可正常运行"
    return "机器资源偏紧张，请留意磁盘空间和内存占用"


def _health_report(
    db: str,
    media_dir: str,
    rtsp_url: str = "",
    rtsp_timeout: float = 10.0,
) -> dict[str, object]:
    """Return a non-destructive deployment report for scripts and operators."""
    inventory = {
        "sites": 0,
        "areas": 0,
        "cameras": 0,
        "streams": 0,
        "devices": 0,
    }
    checks: list[dict[str, str]] = []

    def add(name: str, state: str, detail: str) -> None:
        checks.append({"name": name, "state": state, "detail": detail})

    if db == ":memory:":
        add("SQLite 数据库", "warn", "内存数据库不会跨进程保留数据")
    else:
        database = Path(db).expanduser().resolve()
        if database.exists():
            store = SQLiteStore(database)
            try:
                integrity = store.integrity_check()
                topology = store.load_topology()
                inventory.update(
                    {
                        "sites": len(topology.sites),
                        "areas": len(topology.areas),
                        "cameras": len(topology.cameras),
                        "streams": len(topology.streams),
                        "devices": len(topology.devices),
                    }
                )
            except (OSError, RuntimeError, sqlite3.Error, ValueError):
                integrity = "error"
            finally:
                store.close()
            add("SQLite 数据库", "ok" if integrity == "ok" else "error", integrity)
        else:
            add("SQLite 数据库", "warn", "数据库尚未创建，首次运行会自动初始化")

    camera_count = int(inventory["cameras"])
    stream_count = int(inventory["streams"])
    if camera_count == 0:
        add("摄像头接入", "warn", "尚未添加摄像头")
    elif stream_count == 0:
        add("摄像头接入", "warn", f"{camera_count} 台摄像头尚未提供码流")
    else:
        add("摄像头接入", "ok", f"{camera_count} 台摄像头 · {stream_count} 路码流")

    ffmpeg = find_ffmpeg()
    add(
        "FFmpeg",
        "ok" if ffmpeg is not None else "error",
        str(ffmpeg.executable) if ffmpeg else "未找到 FFmpeg",
    )
    if rtsp_url:
        if ffmpeg is None:
            add("RTSP 探测", "error", "FFmpeg unavailable")
        else:
            try:
                source_resolution = ffmpeg.probe_resolution(rtsp_url, timeout=rtsp_timeout)
                frame = RTSPCapture(ffmpeg, rtsp_url, width=1280, height=720, fps=1.0).capture_one(
                    timeout=rtsp_timeout
                )
                source_text = (
                    f"{source_resolution[0]}x{source_resolution[1]} source"
                    if source_resolution
                    else f"{frame.image.shape[1]}x{frame.image.shape[0]} output"
                )
                add("RTSP 探测", "ok", source_text)
            except (OSError, RuntimeError, ValueError) as exc:
                add("RTSP 探测", "error", _redact_text(exc))
    media_root = Path(media_dir).expanduser()
    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root
    try:
        media_root.mkdir(parents=True, exist_ok=True)
        probe = media_root / ".factoryguard-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        add("媒体目录", "ok", str(media_root.resolve()))
    except OSError as exc:
        add("媒体目录", "error", str(exc))

    root = Path(db).expanduser().resolve().parent if db != ":memory:" else Path.cwd()
    resource = ResourceSampler().sample(root)
    free_gb = resource.disk_free_bytes / (1024**3)
    ratio = resource.disk_free_bytes / max(resource.disk_total_bytes, 1)
    disk_state = "error" if ratio < 0.05 else "warn" if ratio < 0.15 else "ok"
    add("磁盘空间", disk_state, f"{free_gb:.2f} GB 可用")
    resource_state = classify_resource_state(resource)
    if resource_state != "ok":
        add(
            "机器负载",
            resource_state,
            _resource_hint(resource),
        )
    return {
        "ok": all(item["state"] != "error" for item in checks) and resource_state != "error",
        "database": db,
        "generated_at": datetime.now(UTC).isoformat(),
        "resource_state": resource_state,
        "resources": {
            "cpu_percent": round(resource.cpu_percent, 1),
            "memory_used_percent": round(resource.memory_used_percent, 1),
            "process_memory": format_bytes(resource.process_memory_bytes),
            "process_cpu_percent": round(resource.process_cpu_percent, 1),
            "disk_free": format_bytes(resource.disk_free_bytes),
            "logical_cpus": resource.logical_cpu_count,
            "threads": resource.thread_count,
        },
        "inventory": inventory,
        "checks": checks,
    }


if __name__ == "__main__":
    raise SystemExit(main())
