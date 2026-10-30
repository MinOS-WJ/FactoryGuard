from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from .app.pipeline import DetectionPipeline
from .domain.models import Frame, Rule, RuleType
from .domain.rules import RuleEvaluator
from .inference.yolo_onnx import YoloOnnxEngine
from .media.ffmpeg import encode_raw_clip, encode_raw_image, find_ffmpeg, require_ffmpeg
from .media.rtsp import RTSPCapture
from .notifications.actions import execute_http_action
from .notifications.adapters import EmailAdapter, SmsHttpAdapter, parse_phone_recipients, parse_recipients
from .notifications.dispatcher import OutboxDispatcher
from .notifications.channels import channel_catalog
from .simulation.runner import run_demo
from .storage.sqlite import SQLiteStore
from .system_metrics import ResourceSampler, classify_resource_state, format_bytes
from .storage.retention import MediaRetentionManager


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

    parser = argparse.ArgumentParser(prog="factoryguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate = subparsers.add_parser("simulate", help="run the CPU-only end-to-end demo")
    simulate.add_argument("--db", default=":memory:", help="SQLite path, defaults to in-memory")
    simulate.add_argument("--frames", type=int, default=6)

    detect = subparsers.add_parser("detect-rtsp", help="capture RTSP frames and run CPU YOLO ONNX")
    detect.add_argument("--url", default="rtsp://localhost/live", help="RTSP source captured for detection")
    detect.add_argument("--model", default="models/yolo11n.onnx", help="ONNX detection model")
    detect.add_argument("--width", type=int, default=1280, help="processing frame width")
    detect.add_argument("--height", type=int, default=720, help="processing frame height")
    detect.add_argument("--frames", type=int, default=1, help="number of frames to detect")
    detect.add_argument("--confidence", type=float, default=0.25, help="minimum detection confidence")
    detect.add_argument("--input-size", type=int, choices=(320, 416, 640, 960), default=640, help="ONNX model input size")
    detect.add_argument("--fps", type=float, default=2.0, help="sampling frame rate")
    probe = subparsers.add_parser("probe-rtsp", help="check RTSP connectivity and read one frame")
    probe.add_argument("--url", default="rtsp://localhost/live", help="RTSP source to probe for one frame")
    probe.add_argument("--width", type=int, default=1280, help="processing frame width")
    probe.add_argument("--height", type=int, default=720, help="processing frame height")
    probe.add_argument("--timeout", type=float, default=10.0, help="connection and source probe timeout in seconds")
    monitor = subparsers.add_parser("monitor-rtsp", help="run RTSP -> YOLO -> rules -> SQLite for finite frames")
    monitor.add_argument("--url", default="rtsp://localhost/live", help="single RTSP source used when no topology is supplied")
    monitor.add_argument("--model", default="models/yolo11n.onnx", help="ONNX model used for streams without a topology model")
    monitor.add_argument("--db", default=":memory:", help="SQLite path used for events, rules and media metadata")
    monitor.add_argument("--width", type=int, default=1280, help="processing frame width when no topology is supplied")
    monitor.add_argument("--height", type=int, default=720, help="processing frame height when no topology is supplied")
    monitor.add_argument("--frames", type=int, default=3, help="number of frames processed per stream")
    monitor.add_argument("--confidence", type=float, default=0.25, help="detector and default rule confidence threshold")
    monitor.add_argument("--confirm-frames", type=int, default=3, help="consecutive frames required before a default event is confirmed")
    monitor.add_argument(
        "--target-labels",
        default="person",
        help="comma-separated labels for the default rule when no persisted rules exist",
    )
    monitor.add_argument("--media-dir", default="events-media", help="local event media directory for default media actions")
    monitor.add_argument("--clip-timeout", type=float, default=30.0, help="seconds allowed for one MP4 clip encode")
    monitor.add_argument("--prebuffer-seconds", type=float, default=3.0, help="seconds of frames kept before an event clip")
    monitor.add_argument("--http-action-url", default="", help="optional HTTP action URL for the default rule")
    monitor.add_argument("--input-size", type=int, choices=(320, 416, 640, 960), default=640, help="ONNX model input size")
    monitor.add_argument("--fps", type=float, default=2.0, help="sampling and clip frame rate")
    monitor.add_argument("--stream-id", help="logical VideoStream identity for the single --url form")
    monitor.add_argument(
        "--stream",
        dest="streams",
        action="append",
        default=[],
        metavar="STREAM_ID=URL",
        help="add a stream; repeat for multiple streams",
    )
    monitor.add_argument("--topology-db", help="SQLite database containing Site/Area/Camera/VideoStream entities")
    monitor.add_argument("--site-id", help="site entity attached to single-stream events")
    monitor.add_argument("--area-id", help="area entity attached to single-stream events")
    monitor.add_argument("--camera-id", help="camera entity attached to single-stream events")
    dispatch = subparsers.add_parser("dispatch-outbox", help="deliver pending Webhook, SMTP and SMS notifications")
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
    cleanup.add_argument("--db", default="data/factoryguard.db", help="SQLite database containing event media references")
    cleanup.add_argument("--media-dir", default="events-media", help="local event media directory to clean")
    cleanup.add_argument("--retention-days", type=float, default=30.0, help="minimum age in days before media is eligible")
    cleanup.add_argument("--dry-run", action="store_true", help="report eligible media without deleting files")
    health = subparsers.add_parser("health", help="run local deployment and runtime preflight checks")
    health.add_argument("--db", default="data/factoryguard.db", help="SQLite database to inspect")
    health.add_argument("--model", default="models/yolo11n.onnx", help="ONNX model expected by the deployment")
    health.add_argument("--media-dir", default="events-media", help="local event media directory to verify")
    health.add_argument("--rtsp", default="", help="optional RTSP URL to probe with one frame")
    health.add_argument("--rtsp-timeout", type=float, default=10.0, help="RTSP probe timeout in seconds")
    health.add_argument("--strict", action="store_true", help="return failure for warnings as well as errors")
    subparsers.add_parser("channels", help="list notification channels and credential requirements")
    for name, help_text in (("gui", "open the Qt control panel"), ("tray", "run the Qt control panel hidden in the system tray")):
        desktop = subparsers.add_parser(name, help=help_text)
        desktop.add_argument("--url", default="rtsp://localhost/live")
        desktop.add_argument("--model", default="models/yolo11n.onnx")
        desktop.add_argument("--db", default="data/factoryguard.db")
        desktop.add_argument("--media-dir", default="events-media", help="local event media directory")
        desktop.add_argument("--width", type=int, default=1280)
        desktop.add_argument("--height", type=int, default=720)
        desktop.add_argument("--confidence", type=float, default=0.25)
        desktop.add_argument("--confirm-frames", type=int, default=3)
        desktop.add_argument("--fps", type=float, default=2.0)
        desktop.add_argument('--input-size', type=int, choices=(320, 416, 640, 960), default=640, help='ONNX model input size')
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
    if args.command == "simulate":
        if args.frames < 1:
            parser.error("--frames must be positive")
        print(json.dumps(asdict(run_demo(args.db, frames=args.frames)), ensure_ascii=False))
    elif args.command == "detect-rtsp":
        if (
            args.frames < 1
            or args.width < 1
            or args.height < 1
            or not 0 < args.confidence <= 1
            or not isfinite(args.fps)
            or args.fps <= 0
        ):
            parser.error("frames, dimensions and fps must be positive; confidence must be between 0 and 1")
        ffmpeg = require_ffmpeg()
        engine = YoloOnnxEngine(args.model, confidence=args.confidence, input_size=args.input_size)
        if engine.health_check().state != "ready":
            parser.error(_redact_text(engine.health_check().detail or "ONNX model is not ready"))
        capture = RTSPCapture(
            ffmpeg,
            args.url,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
        frames = []
        detect_started = time.perf_counter()
        first_frame_at: float | None = None
        for raw_frame in capture.capture(args.frames):
            if first_frame_at is None:
                first_frame_at = time.perf_counter()
            frames.append({"frame_no": raw_frame.frame_no, "detections": [asdict(d) for d in engine.infer_image(raw_frame.image)]})
        detect_elapsed = time.perf_counter() - detect_started
        processing_elapsed = time.perf_counter() - first_frame_at if first_frame_at is not None else 0.0
        print(
            json.dumps(
                {
                    "url": _redact_url(args.url),
                    "ffmpeg": str(ffmpeg.executable),
                    "model": str(args.model),
                    "provider": engine.health_check().provider,
                    "input_size": args.input_size,
                    "fps": args.fps,
                    "measured_fps": round(len(frames) / processing_elapsed, 2) if len(frames) > 1 and processing_elapsed > 0 else None,
                    "elapsed_seconds": round(detect_elapsed, 3),
                    "startup_seconds": round(first_frame_at - detect_started, 3) if first_frame_at is not None else None,
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
    elif args.command == "monitor-rtsp":
        if (
            args.frames < 1
            or args.width < 1
            or args.height < 1
            or args.confirm_frames < 1
            or not 0 < args.confidence <= 1
            or not isfinite(args.fps)
            or args.fps <= 0
        ):
            parser.error("frames, dimensions, fps and confirm-frames must be positive; confidence must be between 0 and 1")
        if not isfinite(args.clip_timeout) or args.clip_timeout <= 0 or not isfinite(args.prebuffer_seconds) or args.prebuffer_seconds <= 0:
            parser.error("clip-timeout and prebuffer-seconds must be positive")
        if args.http_action_url and not args.http_action_url.lower().startswith(("http://", "https://")):
            parser.error("http-action-url must use HTTP or HTTPS")
        target_labels = tuple(
            value.strip().lower()
            for value in str(args.target_labels).split(",")
            if value.strip()
        )
        if not target_labels or len(set(target_labels)) != len(target_labels):
            parser.error("--target-labels must contain unique, non-empty labels")
        ffmpeg = require_ffmpeg()
        topology_store: SQLiteStore | None = None
        topology = None
        try:
            if args.topology_db:
                topology_store = SQLiteStore(args.topology_db)
                topology = topology_store.load_topology()

            entity_spec = {
                "site_id": args.site_id,
                "area_id": args.area_id,
                "camera_id": args.camera_id,
            }
            stream_specs: list[dict[str, object]] = []
            if args.streams:
                for raw_spec in args.streams:
                    stream_id, separator, url = raw_spec.partition("=")
                    if not separator or not stream_id.strip() or not url.strip():
                        parser.error("--stream must use STREAM_ID=URL")
                    stream_specs.append({
                        "stream_id": stream_id.strip(),
                        "url": url.strip(),
                        **entity_spec,
                    })
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
                            "fps": stream.fps,
                            "input_size": stream.input_size,
                            "site_id": path.site.id,
                            "area_id": path.area.id,
                            "camera_id": path.camera.id,
                        }
                    )
                if not stream_specs:
                    stream_specs.append({
                        "stream_id": args.stream_id or "CH01",
                        "url": _redact_url(args.url),
                        **entity_spec,
                    })
            else:
                stream_specs.append({
                    "stream_id": args.stream_id or "CH01",
                    "url": _redact_url(args.url),
                    **entity_spec,
                })

            engines: list[tuple[dict[str, object], YoloOnnxEngine, Rule, RTSPCapture]] = []
            for spec in stream_specs:
                width = int(spec.get("width", args.width))
                height = int(spec.get("height", args.height))
                model = str(spec.get("model", args.model))
                confidence = float(spec.get("confidence", args.confidence))
                if width < 1 or height < 1 or not 0 < confidence <= 1:
                    parser.error("stream dimensions must be positive and confidence must be between 0 and 1")
                stream_id = str(spec["stream_id"])
                engine = YoloOnnxEngine(model, confidence=confidence, input_size=int(spec.get("input_size", args.input_size)))
                health = engine.health_check()
                if health.state != "ready":
                    parser.error(_redact_text(health.detail or f"ONNX model is not ready for {stream_id}"))
                rule = Rule(
                    id=f"cli-full-frame-{stream_id}",
                    stream_id=stream_id,
                    name=f"全画面{'/'.join(target_labels)}检测",
                    type=RuleType.POLYGON,
                    geometry=((0, 0), (width, 0), (width, height), (0, height)),
                    target_labels=frozenset(target_labels),
                    min_confidence=confidence,
                    confirm_frames=args.confirm_frames,
                    actions=("event", "snapshot", "clip", "notify") + (("http",) if args.http_action_url else ()),
                    http_action_url=args.http_action_url,
                    site_id=spec.get("site_id"),
                    area_id=spec.get("area_id"),
                    camera_id=spec.get("camera_id"),
                )
                capture = RTSPCapture(ffmpeg, str(spec["url"]), width=width, height=height, fps=args.fps)
                spec["width"], spec["height"], spec["model"], spec["confidence"] = width, height, model, confidence
                spec["input_size"] = int(spec.get("input_size", args.input_size))
                engines.append((spec, engine, rule, capture))

            store = SQLiteStore(args.db)
            stream_outputs: list[dict[str, object]] = []
            try:
                active_rules: list[Rule] = []
                for spec, _engine, default_rule, _capture in engines:
                    stream_id = str(spec["stream_id"])
                    persisted = store.load_rules(stream_id=stream_id, published_only=True)
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
                rules_by_id = {rule.id: rule for rule in active_rules}
                media_root = Path(args.media_dir).expanduser()
                if not media_root.is_absolute() and args.db != ":memory:":
                    media_root = Path(args.db).expanduser().resolve().parent / media_root
                pipeline = DetectionPipeline(RuleEvaluator(active_rules), store)
                for spec, engine, rule, capture in engines:
                    stream_id = str(spec["stream_id"])
                    width = int(spec["width"])
                    height = int(spec["height"])
                    stream_started = time.perf_counter()
                    first_frame_at: float | None = None
                    stream_rules = [rule for rule in active_rules if rule.stream_id == stream_id]
                    rule_target_labels = sorted({
                        label
                        for rule in stream_rules
                        for label in rule.target_labels
                    })
                    frame_results: list[dict[str, object]] = []
                    frame_buffer = deque(maxlen=max(1, round(args.fps * args.prebuffer_seconds)))
                    for raw_frame in capture.capture(args.frames):
                        if first_frame_at is None:
                            first_frame_at = time.perf_counter()
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
                        frame_buffer.append(raw_frame.image.copy())
                        detections = engine.infer_image(raw_frame.image)
                        result = pipeline.process(
                            frame,
                            detections,
                            armed=armed_by_stream.get(stream_id, True)
                            and (
                                schedules_by_stream[stream_id] is None
                                or schedules_by_stream[stream_id].is_armed(now)
                            ),
                        )
                        output_events = []
                        for event in result.events:
                            rule = rules_by_id.get(event.rule_id)
                            actions = rule.actions if rule is not None else ("event",)
                            snapshot_uri = None
                            clip_uri = None
                            if "snapshot" in actions:
                                try:
                                    stamp = event.occurred_at.strftime("%Y%m%d-%H%M%S")
                                    snapshot_uri = encode_raw_image(
                                        ffmpeg,
                                        raw_frame.image,
                                        media_root / f"{stamp}-{event.id}.png",
                                    )
                                except (OSError, ValueError, RuntimeError):
                                    snapshot_uri = None
                            if "clip" in actions:
                                try:
                                    stamp = event.occurred_at.strftime("%Y%m%d-%H%M%S")
                                    clip_uri = encode_raw_clip(
                                        ffmpeg,
                                        list(frame_buffer),
                                        media_root / f"{stamp}-{event.id}.mp4",
                                        width=width,
                                        height=height,
                                        fps=args.fps,
                                        timeout=args.clip_timeout,
                                    )
                                except (OSError, ValueError, RuntimeError):
                                    clip_uri = None
                            if snapshot_uri is not None or clip_uri is not None:
                                store.update_event_media(
                                    event.id,
                                    snapshot_uri=snapshot_uri,
                                    clip_uri=clip_uri,
                                    clip_status="ready" if clip_uri is not None else "snapshot_only",
                                )
                            event_with_media = replace(
                                event,
                                snapshot_uri=snapshot_uri,
                                clip_uri=clip_uri,
                                clip_status=(
                                        "ready"
                                        if clip_uri is not None
                                        else "snapshot_only"
                                        if snapshot_uri is not None
                                        else "pending"
                                    ),
                            )
                            if "http" in actions and rule is not None and rule.http_action_url:
                                execute_http_action(
                                    event_with_media,
                                    url=rule.http_action_url,
                                    timeout=rule.http_action_timeout,
                                )
                            output_events.append(event_with_media)
                        detected_labels = {detection.label for detection in detections}

                        target_label_match = bool(detected_labels & set(rule_target_labels))

                        confidence_match = any(

                            detection.label in rule.target_labels

                            and detection.confidence >= rule.min_confidence

                            for detection in detections

                            for rule in stream_rules

                        )

                        diagnostic_reason = (

                            "event_generated"

                            if output_events

                            else "no_active_rules"
                            if not stream_rules
                            else "target_label_mismatch"
                            if not target_label_match

                            else "confidence_below_rule"

                            if not confidence_match

                            else "confirmation_or_geometry_pending"

                        )

                        frame_results.append(

                            {

                                "frame_no": result.frame_no,

                                "detections": result.detections,

                                "detection_labels": {

                                    label: sum(1 for detection in detections if detection.label == label)

                                    for label in sorted(detected_labels)

                                },

                                "detection_confidence": {

                                    label: round(

                                        max(

                                            detection.confidence

                                            for detection in detections

                                            if detection.label == label

                                        ),

                                        3,

                                    )

                                    for label in sorted(detected_labels)

                                },

                                "diagnostic_reason": diagnostic_reason,

                                "target_label_match": target_label_match,

                                "confidence_match": confidence_match,

                                "events": [_event_json(event) for event in output_events],

                            }

                        )
                    stream_elapsed = time.perf_counter() - stream_started
                    processing_elapsed = time.perf_counter() - first_frame_at if first_frame_at is not None else 0.0
                    stream_outputs.append(
                        {
                            "stream_id": stream_id,
                            "url": _redact_url(str(spec["url"])),
                            "model": str(spec["model"]),
                            "input_size": int(spec.get("input_size", args.input_size)),
                            "fps": float(spec.get("fps", args.fps)),
                            "measured_fps": round(len(frame_results) / processing_elapsed, 2) if len(frame_results) > 1 and processing_elapsed > 0 else None,
                            "rule_target_labels": rule_target_labels,
                            "rule_parameters": [
                                {
                                    "id": rule.id,
                                    "name": rule.name,
                                    "min_confidence": rule.min_confidence,
                                    "confirm_frames": rule.confirm_frames,
                                    "condition": rule.condition,
                                }
                                for rule in stream_rules
                            ],
                            "elapsed_seconds": round(stream_elapsed, 3),
                            "startup_seconds": round(first_frame_at - stream_started, 3) if first_frame_at is not None else None,
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
                    "input_size": args.input_size,
                    "fps": args.fps,
                    "measured_fps": stream_outputs[0]["measured_fps"] if len(stream_outputs) == 1 else None,
                    "rule_target_labels": stream_outputs[0]["rule_target_labels"] if len(stream_outputs) == 1 else None,
                    "rule_parameters": stream_outputs[0]["rule_parameters"] if len(stream_outputs) == 1 else None,
                    "elapsed_seconds": stream_outputs[0]["elapsed_seconds"] if len(stream_outputs) == 1 else None,
                    "startup_seconds": stream_outputs[0]["startup_seconds"] if len(stream_outputs) == 1 else None,
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
        if not isfinite(args.rtsp_timeout) or args.rtsp_timeout <= 0:
            parser.error("rtsp-timeout must be positive")
        report = _health_report(args.db, args.model, args.media_dir, args.rtsp, args.rtsp_timeout)
        print(json.dumps(report, ensure_ascii=False))
        has_warning = any(item["state"] == "warn" for item in report["checks"])
        return 1 if not report["ok"] or (args.strict and has_warning) else 0
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
                parser.error(_redact_text(exc))
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
                    media_dir=args.media_dir,
                    width=args.width,
                    height=args.height,
                    confidence=args.confidence,
                    confirm_frames=args.confirm_frames,
                    fps=args.fps,
                    input_size=args.input_size,
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
            parser.error(_redact_text(exc))
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
        "snapshot_uri": event.snapshot_uri,
        "clip_uri": event.clip_uri,
        "clip_status": event.clip_status,
    }


def _health_report(
    db: str,
    model: str,
    media_dir: str,
    rtsp_url: str = "",
    rtsp_timeout: float = 10.0,
) -> dict[str, object]:
    """Return a non-destructive deployment report for scripts and operators."""
    inventory = {"sites": 0, "areas": 0, "cameras": 0, "streams": 0, "rules": 0, "active_rules": 0}
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
                        "rules": len(store.load_rules()),
                        "active_rules": len(store.load_rules(enabled_only=True, published_only=True)),
                    }
                )
            except (OSError, RuntimeError, sqlite3.Error, ValueError):
                integrity = "error"
            finally:
                store.close()
            add("SQLite 数据库", "ok" if integrity == "ok" else "error", integrity)
            if inventory["active_rules"]:
                add("规则运行状态", "ok", str(inventory["active_rules"]) + " 条启用且已发布的规则")
            elif inventory["rules"]:
                add("规则运行状态", "warn", "存在规则但没有启用且已发布的规则")
            else:
                add("规则运行状态", "warn", "尚未配置规则")
        else:
            add("SQLite 数据库", "warn", "数据库尚未创建，首次运行会自动初始化")

    ffmpeg = find_ffmpeg()
    add("FFmpeg", "ok" if ffmpeg is not None else "error", str(ffmpeg.executable) if ffmpeg else "未找到 FFmpeg")
    if rtsp_url:
        if ffmpeg is None:
            add("RTSP 探测", "error", "FFmpeg unavailable")
        else:
            try:
                source_resolution = ffmpeg.probe_resolution(rtsp_url, timeout=rtsp_timeout)
                frame = RTSPCapture(ffmpeg, rtsp_url, width=1280, height=720, fps=1.0).capture_one(timeout=rtsp_timeout)
                source_text = f"{source_resolution[0]}x{source_resolution[1]} source" if source_resolution else f"{frame.image.shape[1]}x{frame.image.shape[0]} output"
                add("RTSP 探测", "ok", source_text)
            except (OSError, RuntimeError, ValueError) as exc:
                add("RTSP 探测", "error", _redact_text(exc))
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
    resource = ResourceSampler().sample(root)
    usage = shutil.disk_usage(root)
    free_gb = usage.free / (1024**3)
    ratio = usage.free / max(usage.total, 1)
    disk_state = "error" if ratio < 0.05 else "warn" if ratio < 0.15 else "ok"
    add("磁盘空间", disk_state, f"{free_gb:.2f} GB 可用")
    resource_state = classify_resource_state(resource)
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
