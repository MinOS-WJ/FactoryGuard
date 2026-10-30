from __future__ import annotations

"""Background RTSP monitoring worker used by the Qt control panel."""

import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

from factoryguard.app.native_events import decide_analysis
from factoryguard.app.pipeline import DetectionPipeline
from factoryguard.domain.models import (
    AnalysisPolicy,
    DeviceCapabilities,
    Frame,
    Rule,
    RuleMode,
    RuleType,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.inference.yolo_onnx import YoloOnnxEngine
from factoryguard.media.ffmpeg import encode_raw_clip, require_ffmpeg
from factoryguard.media.rtsp import RTSPCapture
from factoryguard.notifications.actions import execute_http_action
from factoryguard.notifications.destinations import NotificationDestination, destination_channel_map
from factoryguard.storage.sqlite import SQLiteStore

from .qt import HAS_QT
from .preview import save_frame_image


@dataclass(frozen=True, slots=True)
class StreamRuntimeConfig:
    """Runtime overrides for one logical VideoStream.

    The physical entity IDs are carried with the stream so an event can be
    traced back to its site, area and camera even when several cameras share
    one worker process.
    """

    stream_id: str = "CH01"
    url: str = "rtsp://localhost/live"
    model: str = "models/yolo11n.onnx"
    width: int = 1280
    height: int = 720
    fps: float = 2.0
    confidence: float = 0.25
    input_size: int = 640
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None
    name: str = "default stream"
    device_id: str | None = None
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.LOCAL_FALLBACK
    native_event_codes: tuple[str, ...] = ()
    capabilities: DeviceCapabilities = DeviceCapabilities()
    native_subscription_healthy: bool = False

    def __post_init__(self) -> None:
        if not self.stream_id or not self.url.strip() or not self.model.strip():
            raise ValueError("stream_id, url and model are required")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("stream dimensions must be positive")
        if not isfinite(self.fps) or self.fps <= 0:
            raise ValueError("stream fps must be positive")
        if not isfinite(self.confidence) or not 0 < self.confidence <= 1:
            raise ValueError("stream confidence must be between 0 and 1")
        if isinstance(self.input_size, bool) or not 64 <= int(self.input_size) <= 4096:
            raise ValueError("stream input_size must be between 64 and 4096")
        object.__setattr__(self, "input_size", int(self.input_size))
        try:
            policy = (
                self.analysis_policy
                if isinstance(self.analysis_policy, AnalysisPolicy)
                else AnalysisPolicy(str(self.analysis_policy))
            )
        except ValueError as exc:
            raise ValueError("stream analysis policy is invalid") from exc
        object.__setattr__(self, "analysis_policy", policy)
        object.__setattr__(
            self,
            "native_event_codes",
            tuple(dict.fromkeys(str(value).strip().lower() for value in self.native_event_codes if str(value).strip())),
        )

    @classmethod
    def from_video_stream(cls, stream: VideoStream, *, site_id: str | None = None, area_id: str | None = None, camera_id: str | None = None, fallback_model: str = "models/yolo11n.onnx", capabilities: DeviceCapabilities = DeviceCapabilities(), native_subscription_healthy: bool = False, device_policy: AnalysisPolicy | str | None = None) -> StreamRuntimeConfig:
        return cls(
            stream_id=stream.id,
            url=stream.url,
            model=stream.model or fallback_model,
            width=stream.width,
            height=stream.height,
            fps=stream.fps,
            confidence=stream.confidence,
            input_size=stream.input_size,
            site_id=site_id,
            area_id=area_id,
            camera_id=camera_id or stream.camera_id,
            name=stream.name,
            device_id=stream.device_id,
            analysis_policy=stream.analysis_policy or device_policy or AnalysisPolicy.LOCAL_FALLBACK,
            native_event_codes=stream.native_event_codes,
            capabilities=capabilities,
            native_subscription_healthy=native_subscription_healthy,
        )


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    url: str = "rtsp://localhost/live"
    model: str = "models/yolo11n.onnx"
    db: str = "data/factoryguard.db"
    width: int = 1280
    height: int = 720
    confidence: float = 0.25
    input_size: int = 640
    confirm_frames: int = 3
    fps: float = 2.0
    media_dir: str = "events-media"
    prebuffer_seconds: float = 3.0
    clip_timeout: float = 30.0
    streams: tuple[StreamRuntimeConfig | VideoStream, ...] = ()
    topology: EntityRegistry | None = None
    model_profiles: tuple[ModelProfile, ...] = ()
    model_bindings: tuple[ModelBinding, ...] = ()
    webhook_provider: str = "custom"
    webhook_url: str = ""
    webhook_secret: str = ""
    email_provider: str = "custom"
    email_host: str = ""
    email_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_sender: str = ""
    email_recipients: tuple[str, ...] = ()
    email_use_ssl: bool = False
    email_starttls: bool = True
    sms_provider: str = "custom_http"
    sms_endpoint: str = ""
    sms_token: str = ""
    sms_sender: str = ""
    sms_template_id: str = ""
    sms_recipients: tuple[str, ...] = ()
    delivery_destinations: tuple[NotificationDestination, ...] = ()
    notification_channels: tuple[str, ...] = ("webhook",)
    notification_interval: float = 5.0
    notification_batch: int = 50
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.LOCAL_FALLBACK
    gb28181_enabled: bool = False
    gb28181_platform_id: str = "340200000000000000"
    gb28181_domain: str = "3402000000"
    gb28181_password: str = ""
    gb28181_port: int = 5060

    def __post_init__(self) -> None:
        if isinstance(self.input_size, bool) or not 64 <= int(self.input_size) <= 4096:
            raise ValueError("input_size must be between 64 and 4096")
        object.__setattr__(self, "input_size", int(self.input_size))
        if not isfinite(self.notification_interval) or self.notification_interval <= 0:
            raise ValueError("notification interval must be positive")
        if isinstance(self.notification_batch, bool) or self.notification_batch < 1:
            raise ValueError("notification batch size must be positive")
        channels: list[str] = []
        raw_channels = (
            (self.notification_channels,)
            if isinstance(self.notification_channels, str)
            else self.notification_channels
        )
        for value in raw_channels:
            normalized = str(value).strip().lower()
            if not normalized:
                raise ValueError("notification channel must not be empty")
            if normalized not in channels:
                channels.append(normalized)
        object.__setattr__(self, "notification_channels", tuple(channels))
        destinations = tuple(self.delivery_destinations)
        channel_keys: list[str] = []
        for destination in destinations:
            if not isinstance(destination, NotificationDestination):
                raise TypeError("delivery destinations must be NotificationDestination instances")
            key = destination.outbox_channel
            if key in channel_keys:
                raise ValueError(f"duplicate notification destination: {key}")
            channel_keys.append(key)
        object.__setattr__(self, "delivery_destinations", destinations)

    def _apply_model_binding(self, runtime: StreamRuntimeConfig) -> StreamRuntimeConfig:
        profiles = {profile.id: profile for profile in self.model_profiles if profile.enabled}
        specificity = {"site": 0, "area": 1, "camera": 2, "stream": 3}
        bindings = sorted(
            self.model_bindings,
            key=lambda binding: (binding.priority, specificity.get(binding.scope_type, -1)),
            reverse=True,
        )
        for binding in bindings:
            profile = profiles.get(binding.model_profile_id)
            if profile is None:
                continue
            if not binding.matches(
                site_id=runtime.site_id,
                area_id=runtime.area_id,
                camera_id=runtime.camera_id,
                stream_id=runtime.stream_id,
            ):
                continue
            return replace(
                runtime,
                model=profile.model_uri or runtime.model,
                confidence=profile.confidence,
                input_size=min(profile.input_width, profile.input_height),
            )
        return runtime

    def resolved_streams(self) -> tuple[StreamRuntimeConfig, ...]:
        """Return all enabled streams, with legacy single-stream fallback."""
        if self.streams:
            result: list[StreamRuntimeConfig] = []
            for item in self.streams:
                if isinstance(item, StreamRuntimeConfig):
                    result.append(item)
                    continue
                if isinstance(item, VideoStream):
                    if not item.enabled:
                        continue
                    site_id = area_id = camera_id = None
                    if self.topology is not None:
                        path = self.topology.stream_path(item.id)
                        if not path.site.enabled or not path.camera.enabled or not all(area.enabled for area in path.areas):
                            continue
                        site_id, area_id, camera_id = path.site.id, path.area.id, path.camera.id
                    result.append(
                        StreamRuntimeConfig.from_video_stream(
                            item,
                            site_id=site_id,
                            area_id=area_id,
                            camera_id=camera_id,
                            fallback_model=self.model or "models/yolo11n.onnx",
                            capabilities=path.device.capabilities if path.device else DeviceCapabilities(live_video=True),
                            native_subscription_healthy=path.device.capabilities.native_events if path.device else False,
                            device_policy=path.device.analysis_policy if path.device else None,
                        )
                    )
                    continue
                raise TypeError("MonitorConfig.streams must contain StreamRuntimeConfig or VideoStream")
            return tuple(self._apply_model_binding(runtime) for runtime in result)
        if self.topology is not None and self.topology.streams:
            result: list[StreamRuntimeConfig] = []
            for stream in self.topology.streams:
                if not stream.enabled:
                    continue
                path = self.topology.stream_path(stream.id)
                if not path.site.enabled or not path.camera.enabled or not all(area.enabled for area in path.areas):
                    continue
                result.append(
                    StreamRuntimeConfig.from_video_stream(
                        stream,
                        site_id=path.site.id,
                        area_id=path.area.id,
                        camera_id=path.camera.id,
                        fallback_model=self.model or "models/yolo11n.onnx",
                        capabilities=path.device.capabilities if path.device else DeviceCapabilities(live_video=True),
                        native_subscription_healthy=path.device.capabilities.native_events if path.device else False,
                        device_policy=path.device.analysis_policy if path.device else None,
                    )
                )
            return tuple(self._apply_model_binding(runtime) for runtime in result)
        return (
            self._apply_model_binding(StreamRuntimeConfig(
                stream_id="CH01",
                url=self.url,
                model=self.model,
                width=self.width,
                height=self.height,
                fps=self.fps,
                confidence=self.confidence,
                input_size=self.input_size,
            )),
        )


if HAS_QT:
    from .qt import QObject, Signal, Slot

    class MonitorWorker(QObject):
        """Run capture, CPU inference and event persistence away from the UI thread."""

        status_changed = Signal(str, str)
        frame_processed = Signal(int, int)
        stream_frame_processed = Signal(str, int, int)
        preview_ready = Signal(object, object)
        stream_preview_ready = Signal(str, object, object)
        stream_status_changed = Signal(str, str, str)
        event_created = Signal(object)
        error = Signal(str)
        finished = Signal()

        def __init__(self, config: MonitorConfig) -> None:
            super().__init__()
            self.config = config
            self._stop = threading.Event()
            self._engine_lock = threading.Lock()
            self._engine_cache = {}

        def request_stop(self) -> None:
            """Thread-safe stop request; called directly by the GUI thread."""
            self._stop.set()

        @Slot()
        def run(self) -> None:
            try:
                runtimes = self.config.resolved_streams()
                with self._engine_lock:
                    self._engine_cache.clear()
                engine_label = "本机 ONNX Runtime"
                self.status_changed.emit("starting", f"正在初始化 {len(runtimes)} 路 FFmpeg / {engine_label}")
                ffmpeg = require_ffmpeg()
                workers = [
                    threading.Thread(
                        target=self._run_stream,
                        args=(ffmpeg, runtime),
                        name=f"stream-{runtime.stream_id}",
                        daemon=True,
                    )
                    for runtime in runtimes
                ]
                for worker in workers:
                    worker.start()
                self.status_changed.emit("running", f"运行 {len(workers)} 路 {engine_label} / FFmpeg {ffmpeg.version or 'unknown'}")
                while any(worker.is_alive() for worker in workers):
                    if self._stop.wait(0.2):
                        break
                for worker in workers:
                    worker.join()
            except Exception as exc:  # noqa: BLE001 - surface worker failures through the UI
                self.status_changed.emit("error", str(exc))
                self.error.emit(str(exc))
            finally:
                self.status_changed.emit("stopped", "监控已停止")
                self.finished.emit()

        def _run_stream(self, ffmpeg: Any, runtime: StreamRuntimeConfig) -> None:
            """Run one stream with bounded exponential restart backoff."""
            retry_delay = 1.0
            while not self._stop.is_set():
                self._run_stream_once(ffmpeg, runtime)
                if self._stop.is_set():
                    return
                self.stream_status_changed.emit(
                    runtime.stream_id,
                    "starting",
                    f"初始化失败，{retry_delay:g} 秒后自动重试",
                )
                self._stop.wait(retry_delay)
                retry_delay = min(30.0, retry_delay * 2.0)

        def _shared_engine(self, runtime: StreamRuntimeConfig):
            model_path = str(Path(runtime.model).expanduser().resolve())
            key = (model_path, float(runtime.confidence), int(runtime.input_size))
            with self._engine_lock:
                cached = self._engine_cache.get(key)
                if cached is None:
                    engine = YoloOnnxEngine(model_path, confidence=runtime.confidence, input_size=runtime.input_size)
                    health = engine.health_check()
                    self._engine_cache[key] = (engine, health)
                    return engine, health
                engine, health = cached
                if health.state != "ready":
                    engine.reset()
                    health = engine.health_check()
                    self._engine_cache[key] = (engine, health)
                return engine, health

        def _run_stream_once(self, ffmpeg: Any, runtime: StreamRuntimeConfig) -> None:
            """Run preview plus only the analysis required by the device policy."""
            frame_no = 0
            store: SQLiteStore | None = None
            last_status: str | None = None
            last_health_persist = 0.0

            def report(status: str, detail: str) -> None:
                nonlocal last_status, last_health_persist
                changed = status != last_status
                last_status = status
                if changed:
                    self.stream_status_changed.emit(runtime.stream_id, status, detail)
                now = time.monotonic()
                if store is not None and (changed or now - last_health_persist >= 10.0):
                    store.set_stream_health(runtime.stream_id, status, detail, actor="worker")
                    last_health_persist = now

            try:
                report("starting", "初始化视频连接")
                store = SQLiteStore(self.config.db)
                persisted_rules = store.load_rules(stream_id=runtime.stream_id, published_only=True)
                analysis_rules = [
                    rule for rule in persisted_rules if rule.enabled and rule.trigger_mode is not RuleMode.NATIVE_EVENT
                ]
                native_rules = [
                    rule for rule in persisted_rules if rule.enabled and rule.trigger_mode is RuleMode.NATIVE_EVENT
                ]
                subscription_state = (
                    getattr(store, "list_device_subscriptions", dict)().get(runtime.device_id, {}).get("state")
                    if runtime.device_id
                    else None
                )
                native_healthy = runtime.native_subscription_healthy and subscription_state != "degraded"
                decision = decide_analysis(
                    policy=runtime.analysis_policy,
                    capabilities=runtime.capabilities,
                    native_subscription_healthy=native_healthy,
                    native_rules=native_rules,
                    analysis_rules=analysis_rules,
                )
                engine = None
                pipeline = None
                rules_by_id: dict[str, Rule] = {}
                auto_default_rule = False

                def enable_local_analysis() -> None:
                    nonlocal engine, pipeline, rules_by_id, analysis_rules, auto_default_rule
                    local_engine, health = self._shared_engine(runtime)
                    if health.state != "ready":
                        raise RuntimeError(health.detail or f"本机备用分析不可用: {runtime.stream_id}")
                    if not analysis_rules:
                        default_rule = Rule(
                            id=f"qt-full-frame-{runtime.stream_id}",
                            stream_id=runtime.stream_id,
                            name="全画面人员检测",
                            type=RuleType.POLYGON,
                            geometry=((0, 0), (runtime.width, 0), (runtime.width, runtime.height), (0, runtime.height)),
                            confirm_frames=self.config.confirm_frames,
                            site_id=runtime.site_id,
                            area_id=runtime.area_id,
                            camera_id=runtime.camera_id,
                        )
                        auto_default_rule = True
                        analysis_rules = [default_rule]
                    engine = local_engine
                    pipeline = DetectionPipeline(
                        RuleEvaluator(analysis_rules),
                        store,
                        outbox_channels=self.config.notification_channels,
                        destination_channels=destination_channel_map(
                            destination for destination in self.config.delivery_destinations if destination.enabled
                        ),
                    )
                    rules_by_id = {rule.id: rule for rule in analysis_rules}

                if decision.use_local:
                    enable_local_analysis()
                report("ready", f"{decision.label} · 等待视频帧")
                capture = RTSPCapture(
                    ffmpeg,
                    runtime.url,
                    width=runtime.width,
                    height=runtime.height,
                    fps=runtime.fps,
                )
                frame_buffer: deque[Any] = deque(
                    maxlen=max(1, round(runtime.fps * max(0.0, self.config.prebuffer_seconds)))
                )
                retry_delay = 1.0
                armed = store.get_stream_armed(runtime.stream_id, default=True)
                schedule = store.get_arming_schedule(runtime.stream_id)
                while not self._stop.is_set():
                    try:
                        with capture.stream(timeout=12.0) as raw_frames:
                            while not self._stop.is_set():
                                raw = next(raw_frames, None)
                                if raw is None:
                                    raise RuntimeError("FFmpeg 直播流已结束")
                                retry_delay = 1.0
                                report("running", decision.label)
                                detections = engine.infer_image(raw.image) if engine is not None else ()
                                frame_buffer.append(raw.image.copy())
                                self.preview_ready.emit(raw.image.copy(), detections)
                                self.stream_preview_ready.emit(runtime.stream_id, raw.image.copy(), detections)
                                result_events: tuple[Any, ...] = ()
                                result_count = len(detections)
                                if frame_no % max(1, round(runtime.fps * 2)) == 0:
                                    subscription_state = (
                                        getattr(store, "list_device_subscriptions", dict)()
                                        .get(runtime.device_id, {})
                                        .get("state")
                                        if runtime.device_id
                                        else None
                                    )
                                    updated_decision = decide_analysis(
                                        policy=runtime.analysis_policy,
                                        capabilities=runtime.capabilities,
                                        native_subscription_healthy=(
                                            runtime.native_subscription_healthy
                                            and subscription_state != "degraded"
                                        ),
                                        native_rules=native_rules,
                                        analysis_rules=() if auto_default_rule else analysis_rules,
                                    )
                                    if updated_decision.use_local != decision.use_local:
                                        if updated_decision.use_local:
                                            enable_local_analysis()
                                        else:
                                            engine = None
                                            pipeline = None
                                            rules_by_id = {}
                                        decision = updated_decision
                                        report("running", decision.label)
                                    armed = store.get_stream_armed(runtime.stream_id, default=True)
                                    schedule = store.get_arming_schedule(runtime.stream_id)
                                now = datetime.now(UTC)
                                if pipeline is not None:
                                    frame = Frame(
                                        runtime.stream_id,
                                        frame_no,
                                        now,
                                        runtime.width,
                                        runtime.height,
                                        nvr_time=now,
                                        site_id=runtime.site_id,
                                        area_id=runtime.area_id,
                                        camera_id=runtime.camera_id,
                                    )
                                    schedule_armed = schedule is None or schedule.is_armed(now)
                                    result = pipeline.process(frame, detections, armed=armed and schedule_armed)
                                    result_events = tuple(result.events)
                                    result_count = int(result.detections)
                                self.frame_processed.emit(frame_no, result_count)
                                self.stream_frame_processed.emit(runtime.stream_id, frame_no, result_count)
                                for event in result_events:
                                    actions = rules_by_id.get(event.rule_id).actions if event.rule_id in rules_by_id else ("snapshot", "clip")
                                    snapshot_uri = self._save_event_snapshot(raw.image, event) if "snapshot" in actions else None
                                    clip_uri = (
                                        self._save_event_clip(
                                            ffmpeg,
                                            list(frame_buffer),
                                            event,
                                            width=runtime.width,
                                            height=runtime.height,
                                            fps=runtime.fps,
                                        )
                                        if "clip" in actions
                                        else None
                                    )
                                    clip_status = (
                                        "ready"
                                        if clip_uri is not None
                                        else "snapshot_only"
                                        if snapshot_uri is not None
                                        else "pending"
                                    )
                                    event_with_media = replace(
                                        event,
                                        snapshot_uri=snapshot_uri,
                                        clip_uri=clip_uri,
                                        clip_status=clip_status,
                                    )
                                    if snapshot_uri is not None or clip_uri is not None:
                                        store.update_event_media(
                                            event.id,
                                            snapshot_uri=snapshot_uri,
                                            clip_uri=clip_uri,
                                            clip_status=clip_status,
                                        )
                                    rule = rules_by_id.get(event.rule_id)
                                    if "http" in actions and rule is not None:
                                        ok, detail = execute_http_action(
                                            event_with_media,
                                            url=rule.http_action_url,
                                            timeout=rule.http_action_timeout,
                                        )
                                        if not ok:
                                            self.error.emit(f"{runtime.stream_id} 外部 HTTP 动作失败：{detail}")
                                    self.event_created.emit(_event_payload(event_with_media))
                                frame_no += 1
                        if not self._stop.is_set():
                            raise RuntimeError("FFmpeg 直播流已结束")
                    except Exception as exc:  # noqa: BLE001 - keep each stream alive on transient RTSP loss
                        if self._stop.is_set():
                            break
                        detail = f"{exc}；{retry_delay:g}秒后自动重试"
                        report("degraded", detail)
                        self.status_changed.emit(
                            "degraded",
                            f"{runtime.stream_id} 视频流暂时不可用：{detail}",
                        )
                        self.error.emit(f"{runtime.stream_id}: {detail}")
                        self._stop.wait(retry_delay)
                        retry_delay = min(30.0, retry_delay * 2.0)
            except Exception as exc:  # noqa: BLE001 - isolate one bad stream from its siblings
                report("offline", str(exc))
                self.status_changed.emit("error", f"{runtime.stream_id}: {exc}")
                self.error.emit(f"{runtime.stream_id}: {exc}")
            finally:
                report(
                    "stopped" if self._stop.is_set() else (last_status or "offline"),
                    "监控已停止" if self._stop.is_set() else "推流线程已退出",
                )
                if store is not None:
                    store.close()

        def _save_event_snapshot(self, image: Any, event: Any) -> str | None:
            try:
                media_root = self._media_root()
                filename = f"{event.occurred_at.strftime('%Y%m%d-%H%M%S')}-{event.id}.png"
                return save_frame_image(image, media_root / filename)
            except (OSError, ValueError) as exc:
                self.error.emit(f"无法保存事件快照: {exc}")
                return None

        def _save_event_clip(
            self,
            ffmpeg: Any,
            frames: list[Any],
            event: Any,
            *,
            width: int | None = None,
            height: int | None = None,
            fps: float | None = None,
        ) -> str | None:
            try:
                media_root = self._media_root()
                filename = f"{event.occurred_at.strftime('%Y%m%d-%H%M%S')}-{event.id}.mp4"
                return encode_raw_clip(
                    ffmpeg,
                    frames,
                    media_root / filename,
                    width=width or self.config.width,
                    height=height or self.config.height,
                    fps=fps or self.config.fps,
                    timeout=self.config.clip_timeout,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                self.error.emit(f"无法生成事件视频片段，将使用快照回放: {exc}")
                return None

        def _media_root(self) -> Path:
            media_root = Path(self.config.media_dir)
            if not media_root.is_absolute() and self.config.db != ":memory:":
                media_root = Path(self.config.db).expanduser().resolve().parent / media_root
            return media_root

else:

    class MonitorWorker:  # pragma: no cover - only used to produce a clear error without Qt
        def __init__(self, config: MonitorConfig) -> None:
            raise RuntimeError("PySide6 is required for the Qt monitoring worker")


def _event_payload(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "level": event.level,
        "channel_id": event.channel_id,
        "stream_id": event.stream_id or event.channel_id,
        "site_id": event.site_id,
        "area_id": event.area_id,
        "camera_id": event.camera_id,
        "entity": event.entity_ids,
        "occurred_at": event.occurred_at.isoformat(),
        "target": dict(event.target),
        "snapshot_uri": event.snapshot_uri,
        "clip_uri": event.clip_uri,
        "clip_status": event.clip_status,
    }


__all__ = ["MonitorConfig", "MonitorWorker", "StreamRuntimeConfig"]
