from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from .app.pipeline import DetectionPipeline
from .domain.models import Frame, Rule, RuleType
from .domain.rules import RuleEvaluator
from .inference.yolo_onnx import YoloOnnxEngine
from .inference.server import CentralInferenceServer
from .media.ffmpeg import find_ffmpeg, require_ffmpeg
from .media.rtsp import RTSPCapture
from .notifications.adapters import EmailAdapter, SmsHttpAdapter, parse_phone_recipients, parse_recipients
from .notifications.dispatcher import OutboxDispatcher
from .notifications.channels import channel_catalog
from .simulation.runner import run_demo
from .storage.sqlite import SQLiteStore
from .storage.retention import MediaRetentionManager


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

    parser = argparse.ArgumentParser(prog="factoryguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inference_server = subparsers.add_parser("inference-server", help="run one shared central inference service")
    inference_server.add_argument("--host", default="0.0.0.0")
    inference_server.add_argument("--port", type=int, default=8765)
    inference_server.add_argument("--model", default="models/yolo11n.onnx")
    inference_server.add_argument("--confidence", type=float, default=0.25)
    inference_server.add_argument(
        "--api-key",
        default=os.environ.get("FACTORYGUARD_INFERENCE_API_KEY", ""),
        help="bearer token for clients; prefer FACTORYGUARD_INFERENCE_API_KEY",
    )
    simulate = subparsers.add_parser("simulate", help="run the CPU-only end-to-end demo")
    simulate.add_argument("--db", default=":memory:", help="SQLite path, defaults to in-memory")
    simulate.add_argument("--frames", type=int, default=6)
    detect = subparsers.add_parser("detect-rtsp", help="capture RTSP frames and run CPU YOLO ONNX")
    detect.add_argument("--url", default="rtsp://localhost/live")
    detect.add_argument("--model", default="models/yolo11n.onnx")
    detect.add_argument("--width", type=int, default=1280)
    detect.add_argument("--height", type=int, default=720)
    detect.add_argument("--frames", type=int, default=1)
    detect.add_argument("--confidence", type=float, default=0.25)
    probe = subparsers.add_parser("probe-rtsp", help="check RTSP connectivity and read one frame")
    probe.add_argument("--url", default="rtsp://localhost/live")
    probe.add_argument("--width", type=int, default=1280)
    probe.add_argument("--height", type=int, default=720)
    probe.add_argument("--timeout", type=float, default=10.0)
    monitor = subparsers.add_parser("monitor-rtsp", help="run RTSP -> YOLO -> rules -> SQLite for finite frames")
    monitor.add_argument("--url", default="rtsp://localhost/live")
    monitor.add_argument("--model", default="models/yolo11n.onnx")
    monitor.add_argument("--db", default=":memory:")
    monitor.add_argument("--width", type=int, default=1280)
    monitor.add_argument("--height", type=int, default=720)
    monitor.add_argument("--frames", type=int, default=3)
    monitor.add_argument("--confidence", type=float, default=0.25)
    monitor.add_argument("--confirm-frames", type=int, default=3)
    monitor.add_argument("--stream-id", help="logical VideoStream identity for the legacy single-stream form")
    monitor.add_argument(
        "--stream",
        dest="streams",
        action="append",
        default=[],
        metavar="STREAM_ID=URL",
        help="add a stream; repeat for multiple streams",
    )
    monitor.add_argument("--topology-db", help="SQLite database containing Site/Area/Camera/VideoStream entities")
    monitor.add_argument("--site-id")
    monitor.add_argument("--area-id")
    monitor.add_argument("--camera-id")
    dispatch = subparsers.add_parser("dispatch-outbox", help="deliver pending Webhook and SMTP notifications")
    dispatch.add_argument("--db", default="data/factoryguard.db")
    dispatch.add_argument("--webhook-url", default=os.environ.get("FACTORYGUARD_WEBHOOK_URL", ""))
    dispatch.add_argument("--webhook-secret", default=os.environ.get("FACTORYGUARD_WEBHOOK_SECRET", ""))
    dispatch.add_argument("--email-host", default=os.environ.get("FACTORYGUARD_EMAIL_HOST", ""))
    dispatch.add_argument("--email-port", type=int, default=_env_int("FACTORYGUARD_EMAIL_PORT", 587))
    dispatch.add_argument("--email-username", default=os.environ.get("FACTORYGUARD_EMAIL_USERNAME", ""))
    dispatch.add_argument("--email-password", default=os.environ.get("FACTORYGUARD_EMAIL_PASSWORD", ""))
    dispatch.add_argument("--email-sender", default=os.environ.get("FACTORYGUARD_EMAIL_SENDER", ""))
    dispatch.add_argument("--email-recipients", default=os.environ.get("FACTORYGUARD_EMAIL_RECIPIENTS", ""))
    dispatch.add_argument(
        "--email-security",
        choices=("starttls", "ssl", "none"),
        default=os.environ.get("FACTORYGUARD_EMAIL_SECURITY", "starttls"),
    )
    dispatch.add_argument("--sms-provider", default=os.environ.get("FACTORYGUARD_SMS_PROVIDER", "custom_http"))
    dispatch.add_argument("--sms-endpoint", default=os.environ.get("FACTORYGUARD_SMS_ENDPOINT", ""))
    dispatch.add_argument("--sms-token", default=os.environ.get("FACTORYGUARD_SMS_TOKEN", ""))
    dispatch.add_argument("--sms-sender", default=os.environ.get("FACTORYGUARD_SMS_SENDER", ""))
    dispatch.add_argument("--sms-template-id", default=os.environ.get("FACTORYGUARD_SMS_TEMPLATE_ID", ""))
    dispatch.add_argument("--sms-recipients", default=os.environ.get("FACTORYGUARD_SMS_RECIPIENTS", ""))
    dispatch.add_argument("--limit", type=int, default=100)
    dispatch.add_argument("--watch", action="store_true", help="keep polling until interrupted")
    dispatch.add_argument("--interval", type=float, default=5.0, help="seconds between watch polls")
    cleanup = subparsers.add_parser("cleanup-media", help="remove expired local event media")
    cleanup.add_argument("--db", default="data/factoryguard.db")
    cleanup.add_argument("--media-dir", default="events-media")
    cleanup.add_argument("--retention-days", type=float, default=30.0)
    cleanup.add_argument("--dry-run", action="store_true")
    health = subparsers.add_parser("health", help="run local deployment and runtime preflight checks")
    health.add_argument("--db", default="data/factoryguard.db")
    health.add_argument("--model", default="models/yolo11n.onnx")
    health.add_argument("--media-dir", default="events-media")
    subparsers.add_parser("channels", help="list notification channels and credential requirements")
    for name, help_text in (("gui", "open the Qt control panel"), ("tray", "run the Qt control panel hidden in the system tray")):
        desktop = subparsers.add_parser(name, help=help_text)
        desktop.add_argument("--url", default="rtsp://localhost/live")
        desktop.add_argument("--model", default="models/yolo11n.onnx")
        desktop.add_argument("--db", default="data/factoryguard.db")
        desktop.add_argument("--width", type=int, default=1280)
        desktop.add_argument("--height", type=int, default=720)
        desktop.add_argument("--confidence", type=float, default=0.25)
        desktop.add_argument("--confirm-frames", type=int, default=3)
        desktop.add_argument("--fps", type=float, default=2.0)
        desktop.add_argument("--inference-mode", choices=("central", "local"), default="central")
        desktop.add_argument(
            "--inference-url",
            default=os.environ.get("FACTORYGUARD_INFERENCE_URL", "http://127.0.0.1:8765"),
        )
        desktop.add_argument(
            "--inference-api-key",
            default=os.environ.get("FACTORYGUARD_INFERENCE_API_KEY", ""),
            help="central inference bearer token; prefer FACTORYGUARD_INFERENCE_API_KEY",
        )
        desktop.add_argument("--inference-timeout", type=float, default=10.0)
        desktop.add_argument(
            "--webhook-url",
            default=os.environ.get("FACTORYGUARD_WEBHOOK_URL", ""),
            help="optional HTTPS webhook URL for background outbox delivery",
        )
        desktop.add_argument(
            "--webhook-secret",
            default=os.environ.get("FACTORYGUARD_WEBHOOK_SECRET", ""),
            help="webhook signing secret; prefer FACTORYGUARD_WEBHOOK_SECRET",
        )
        desktop.add_argument("--email-host", default=os.environ.get("FACTORYGUARD_EMAIL_HOST", ""))
        desktop.add_argument("--email-port", type=int, default=_env_int("FACTORYGUARD_EMAIL_PORT", 587))
        desktop.add_argument(
            "--email-username", default=os.environ.get("FACTORYGUARD_EMAIL_USERNAME", "")
        )
        desktop.add_argument(
            "--email-password",
            default=os.environ.get("FACTORYGUARD_EMAIL_PASSWORD", ""),
            help="SMTP password; prefer FACTORYGUARD_EMAIL_PASSWORD",
        )
        desktop.add_argument("--email-sender", default=os.environ.get("FACTORYGUARD_EMAIL_SENDER", ""))
        desktop.add_argument(
            "--email-recipients", default=os.environ.get("FACTORYGUARD_EMAIL_RECIPIENTS", "")
        )
        desktop.add_argument(
            "--email-security",
            choices=("starttls", "ssl", "none"),
            default=os.environ.get("FACTORYGUARD_EMAIL_SECURITY", "starttls"),
        )
        desktop.add_argument(
            "--notification-interval",
            type=float,
            default=5.0,
            help="seconds between background outbox polls",
        )
        desktop.add_argument(
            "--notification-batch",
            type=int,
            default=50,
            help="maximum notifications delivered per poll",
        )
    args = parser.parse_args(argv)
    if args.command == "inference-server":
        if not 1 <= args.port <= 65535 or not 0 < args.confidence <= 1:
            parser.error("inference server port and confidence are invalid")
        engine = YoloOnnxEngine(args.model, confidence=args.confidence)
        health = engine.health_check()
        if health.state != "ready":
            parser.error(health.detail or "inference model is not ready")
        server = CentralInferenceServer(engine, host=args.host, port=args.port, api_key=args.api_key)
        print(json.dumps({"status": "listening", "url": f"http://{args.host}:{args.port}"}, ensure_ascii=False), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            server.stop()
    elif args.command == "simulate":
        if args.frames < 1:
            parser.error("--frames must be positive")
        print(json.dumps(asdict(run_demo(args.db, frames=args.frames)), ensure_ascii=False))
    elif args.command == "detect-rtsp":
        if args.frames < 1 or args.width < 1 or args.height < 1 or not 0 < args.confidence <= 1:
            parser.error("frames, width, height must be positive and confidence must be between 0 and 1")
        ffmpeg = require_ffmpeg()
        engine = YoloOnnxEngine(args.model, confidence=args.confidence)
        if engine.health_check().state != "ready":
            parser.error(engine.health_check().detail or "ONNX model is not ready")
        capture = RTSPCapture(
            ffmpeg,
            args.url,
            width=args.width,
            height=args.height,
        )
        frames = []
        for raw_frame in capture.capture(args.frames):
            frames.append({"frame_no": raw_frame.frame_no, "detections": [asdict(d) for d in engine.infer_image(raw_frame.image)]})
        print(
            json.dumps(
                {
                    "url": args.url,
                    "ffmpeg": str(ffmpeg.executable),
                    "model": str(args.model),
                    "provider": engine.health_check().provider,
                    "frames": frames,
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "probe-rtsp":
        if args.width < 1 or args.height < 1 or not isfinite(args.timeout) or args.timeout <= 0:
            parser.error("probe dimensions and timeout must be positive")
        try:
            ffmpeg = require_ffmpeg()
            frame = RTSPCapture(
                ffmpeg,
                args.url,
                width=args.width,
                height=args.height,
            ).capture_one(timeout=args.timeout)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        frame_height, frame_width = frame.image.shape[:2]
        print(
            json.dumps(
                {
                    "url": args.url,
                    "ffmpeg": str(ffmpeg.executable),
                    "width": frame_width,
                    "height": frame_height,
                    "frame_no": frame.frame_no,
                    "status": "ok",
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "monitor-rtsp":
        if (
            args.frames < 1
            or args.width < 1
            or args.height < 1
            or args.confirm_frames < 1
            or not 0 < args.confidence <= 1
        ):
            parser.error("frames, dimensions and confirm-frames must be positive; confidence must be between 0 and 1")
        ffmpeg = require_ffmpeg()
        topology_store: SQLiteStore | None = None
        topology = None
        try:
            if args.topology_db:
                topology_store = SQLiteStore(args.topology_db)
                topology = topology_store.load_topology()

            stream_specs: list[dict[str, object]] = []
            if args.streams:
                for raw_spec in args.streams:
                    stream_id, separator, url = raw_spec.partition("=")
                    if not separator or not stream_id.strip() or not url.strip():
                        parser.error("--stream must use STREAM_ID=URL")
                    stream_specs.append({"stream_id": stream_id.strip(), "url": url.strip()})
            elif topology is not None and topology.streams:
                for stream in topology.streams:
                    if not stream.enabled:
                        continue
                    path = topology.stream_path(stream.id)
                    stream_specs.append(
                        {
                            "stream_id": stream.id,
                            "url": stream.url,
                            "width": stream.width,
                            "height": stream.height,
                            "model": stream.model or args.model,
                            "confidence": stream.confidence,
                            "site_id": path.site.id,
                            "area_id": path.area.id,
                            "camera_id": path.camera.id,
                        }
                    )
                if not stream_specs:
                    stream_specs.append({"stream_id": args.stream_id or "CH01", "url": args.url})
            else:
                stream_specs.append({"stream_id": args.stream_id or "CH01", "url": args.url})

            engines: list[tuple[dict[str, object], YoloOnnxEngine, Rule, RTSPCapture]] = []
            for spec in stream_specs:
                width = int(spec.get("width", args.width))
                height = int(spec.get("height", args.height))
                model = str(spec.get("model", args.model))
                confidence = float(spec.get("confidence", args.confidence))
                if width < 1 or height < 1 or not 0 < confidence <= 1:
                    parser.error("stream dimensions must be positive and confidence must be between 0 and 1")
                stream_id = str(spec["stream_id"])
                engine = YoloOnnxEngine(model, confidence=confidence)
                health = engine.health_check()
                if health.state != "ready":
                    parser.error(health.detail or f"ONNX model is not ready for {stream_id}")
                rule = Rule(
                    id=f"cli-full-frame-{stream_id}",
                    stream_id=stream_id,
                    name="full-frame person detection",
                    type=RuleType.POLYGON,
                    geometry=((0, 0), (width, 0), (width, height), (0, height)),
                    confirm_frames=args.confirm_frames,
                    site_id=spec.get("site_id"),
                    area_id=spec.get("area_id"),
                    camera_id=spec.get("camera_id"),
                )
                capture = RTSPCapture(ffmpeg, str(spec["url"]), width=width, height=height)
                spec["width"], spec["height"], spec["model"], spec["confidence"] = width, height, model, confidence
                engines.append((spec, engine, rule, capture))

            store = SQLiteStore(args.db)
            stream_outputs: list[dict[str, object]] = []
            try:
                active_rules: list[Rule] = []
                for spec, _engine, default_rule, _capture in engines:
                    stream_id = str(spec["stream_id"])
                    persisted = store.load_rules(stream_id=stream_id)
                    enabled_rules = [rule for rule in persisted if rule.enabled]
                    if persisted:
                        active_rules.extend(enabled_rules)
                    else:
                        store.save_rule(default_rule)
                        active_rules.append(default_rule)
                armed_by_stream = {
                    str(spec["stream_id"]): store.get_stream_armed(str(spec["stream_id"]), default=True)
                    for spec, _engine, _rule, _capture in engines
                }
                schedules_by_stream = {
                    str(spec["stream_id"]): store.get_arming_schedule(str(spec["stream_id"]))
                    for spec, _engine, _rule, _capture in engines
                }
                pipeline = DetectionPipeline(RuleEvaluator(active_rules), store)
                for spec, engine, rule, capture in engines:
                    stream_id = str(spec["stream_id"])
                    width = int(spec["width"])
                    height = int(spec["height"])
                    frame_results: list[dict[str, object]] = []
                    for raw_frame in capture.capture(args.frames):
                        now = datetime.now(UTC)
                        frame = Frame(
                            stream_id,
                            raw_frame.frame_no,
                            now,
                            width,
                            height,
                            nvr_time=now,
                            site_id=spec.get("site_id"),
                            area_id=spec.get("area_id"),
                            camera_id=spec.get("camera_id"),
                        )
                        result = pipeline.process(
                            frame,
                            engine.infer_image(raw_frame.image),
                            armed=armed_by_stream.get(stream_id, True)
                            and (
                                schedules_by_stream[stream_id] is None
                                or schedules_by_stream[stream_id].is_armed(now)
                            ),
                        )
                        frame_results.append(
                            {
                                "frame_no": result.frame_no,
                                "detections": result.detections,
                                "events": [_event_json(event) for event in result.events],
                            }
                        )
                    stream_outputs.append(
                        {
                            "stream_id": stream_id,
                            "url": str(spec["url"]),
                            "model": str(spec["model"]),
                            "entity": {
                                "site_id": spec.get("site_id"),
                                "area_id": spec.get("area_id"),
                                "camera_id": spec.get("camera_id"),
                                "stream_id": stream_id,
                            },
                            "frames": frame_results,
                        }
                    )
                output = {
                    "url": stream_outputs[0]["url"] if len(stream_outputs) == 1 else None,
                    "model": stream_outputs[0]["model"] if len(stream_outputs) == 1 else None,
                    "provider": engines[0][1].health_check().provider if len(engines) == 1 else None,
                    "frames": stream_outputs[0]["frames"] if len(stream_outputs) == 1 else [],
                    "streams": stream_outputs,
                    "events": len(store.list_events()),
                    "pending_notifications": len(store.list_pending_outbox()),
                    "integrity": store.integrity_check(),
                    "db": args.db,
                }
            finally:
                store.close()
        finally:
            if topology_store is not None:
                topology_store.close()
        print(json.dumps(output, ensure_ascii=False))
    elif args.command == "dispatch-outbox":
        if args.limit < 1 or not isfinite(args.interval) or args.interval <= 0:
            parser.error("--limit must be positive and --interval must be finite and positive")
        store = SQLiteStore(args.db)
        try:
            try:
                email_recipients = (
                    parse_recipients(args.email_recipients)
                    if args.email_recipients.strip()
                    else ()
                )
            except ValueError as exc:
                parser.error(str(exc))
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
                    parser.error(f"invalid SMTP configuration: {exc}")
            try:
                sms_recipients = (
                    parse_phone_recipients(args.sms_recipients)
                    if args.sms_recipients.strip()
                    else ()
                )
            except ValueError as exc:
                parser.error(str(exc))
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
                    parser.error(f"invalid SMS configuration: {exc}")
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
            if not os.path.isabs(media_root) and args.db != ":memory:":
                media_root = os.path.join(os.path.dirname(os.path.abspath(args.db)), media_root)
            report = MediaRetentionManager(media_root=media_root).cleanup(
                store,
                retention_days=args.retention_days,
                dry_run=args.dry_run,
            )
        finally:
            store.close()
        print(json.dumps(asdict(report), ensure_ascii=False))
    elif args.command == "health":
        print(json.dumps(_health_report(args.db, args.model, args.media_dir), ensure_ascii=False))
    elif args.command == "channels":
        print(
            json.dumps(
                {"channels": [descriptor.as_dict() for descriptor in channel_catalog()]},
                ensure_ascii=False,
            )
        )
    elif args.command in {"gui", "tray"}:
        if (
            args.width < 1
            or args.height < 1
            or args.confirm_frames < 1
            or not 0 < args.confidence <= 1
            or not isfinite(args.fps)
            or args.fps <= 0
            or not isfinite(args.inference_timeout)
            or args.inference_timeout <= 0
            or not isfinite(args.notification_interval)
            or args.notification_interval <= 0
            or args.notification_batch < 1
        ):
            parser.error("invalid GUI monitor or notification parameters")
        try:
            from .ui.main_window import launch
            from .ui.worker import MonitorConfig

            try:
                email_recipients = (
                    parse_recipients(args.email_recipients)
                    if args.email_recipients.strip()
                    else ()
                )
            except ValueError as exc:
                parser.error(str(exc))
            webhook_url = args.webhook_url.strip()
            if bool(webhook_url) != bool(args.webhook_secret):
                parser.error("--webhook-url and --webhook-secret must be provided together")
            if webhook_url and not webhook_url.lower().startswith("https://"):
                parser.error("--webhook-url must use HTTPS")
            email_configured = bool(
                args.email_host.strip()
                or args.email_username.strip()
                or args.email_sender.strip()
                or email_recipients
            )
            channels = []
            if webhook_url:
                channels.append("webhook")
            if email_configured:
                channels.append("email")
            return launch(
                config=MonitorConfig(
                    url=args.url,
                    model=args.model,
                    db=args.db,
                    width=args.width,
                    height=args.height,
                    confidence=args.confidence,
                    confirm_frames=args.confirm_frames,
                    fps=args.fps,
                    inference_mode=args.inference_mode,
                    central_inference_url=args.inference_url,
                    central_inference_api_key=args.inference_api_key,
                    central_inference_timeout=args.inference_timeout,
                    webhook_url=webhook_url,
                    webhook_secret=args.webhook_secret,
                    email_host=args.email_host,
                    email_port=args.email_port,
                    email_username=args.email_username,
                    email_password=args.email_password,
                    email_sender=args.email_sender,
                    email_recipients=email_recipients,
                    email_use_ssl=args.email_security == "ssl",
                    email_starttls=args.email_security == "starttls",
                    notification_channels=tuple(channels) or ("webhook",),
                    notification_interval=args.notification_interval,
                    notification_batch=args.notification_batch,
                ),
                start_hidden=args.command == "tray",
                argv=["factoryguard", args.command],
            )
        except RuntimeError as exc:
            parser.error(str(exc))
    return 0


def _event_json(event) -> dict:
    return {
        "id": event.id,
        "dedup_key": event.dedup_key,
        "rule_id": event.rule_id,
        "channel_id": event.channel_id,
        "stream_id": event.stream_id or event.channel_id,
        "site_id": event.site_id,
        "area_id": event.area_id,
        "camera_id": event.camera_id,
        "entity": event.entity_ids,
        "level": event.level,
        "occurred_at": event.occurred_at.isoformat(),
        "target": dict(event.target),
        "status": event.status.value,
        "clock_delta_ms": event.clock_delta_ms,
    }


def _health_report(db: str, model: str, media_dir: str) -> dict[str, object]:
    """Return a non-destructive deployment report for scripts and operators."""
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
            finally:
                store.close()
            add("SQLite 数据库", "ok" if integrity == "ok" else "error", integrity)
        else:
            add("SQLite 数据库", "warn", "数据库尚未创建，首次运行会自动初始化")

    ffmpeg = find_ffmpeg()
    add("FFmpeg", "ok" if ffmpeg is not None else "error", str(ffmpeg.executable) if ffmpeg else "未找到 FFmpeg")
    model_path = Path(model).expanduser().resolve()
    add("ONNX 模型", "ok" if model_path.is_file() else "error", str(model_path))

    media_root = Path(media_dir).expanduser()
    if not media_root.is_absolute() and db != ":memory:":
        media_root = Path(db).expanduser().resolve().parent / media_root
    try:
        media_root.mkdir(parents=True, exist_ok=True)
        probe = media_root / ".factoryguard-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        add("媒体目录", "ok", str(media_root.resolve()))
    except OSError as exc:
        add("媒体目录", "error", str(exc))

    root = Path(db).expanduser().resolve().parent if db != ":memory:" else Path.cwd()
    usage = shutil.disk_usage(root)
    free_gb = usage.free / (1024**3)
    ratio = usage.free / max(usage.total, 1)
    disk_state = "error" if ratio < 0.05 else "warn" if ratio < 0.15 else "ok"
    add("磁盘空间", disk_state, f"{free_gb:.2f} GB 可用")
    return {
        "ok": all(item["state"] != "error" for item in checks),
        "database": db,
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": checks,
    }


if __name__ == "__main__":
    raise SystemExit(main())
