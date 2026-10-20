from __future__ import annotations

"""Background RTSP monitoring worker used by the Qt control panel."""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

from factoryguard.app.pipeline import DetectionPipeline
from factoryguard.domain.models import Frame, Rule, RuleType, VideoStream
from factoryguard.domain.topology import EntityRegistry
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.inference.remote import CentralInferenceClient
from factoryguard.inference.yolo_onnx import YoloOnnxEngine
from factoryguard.media.ffmpeg import encode_raw_clip, require_ffmpeg
from factoryguard.media.rtsp import RTSPCapture
from factoryguard.notifications.destinations import NotificationDestination
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
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None
    name: str = "default stream"

    def __post_init__(self) -> None:
        if not self.stream_id or not self.url.strip() or not self.model.strip():
            raise ValueError("stream_id, url and model are required")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("stream dimensions must be positive")
        if not isfinite(self.fps) or self.fps <= 0:
            raise ValueError("stream fps must be positive")
        if not isfinite(self.confidence) or not 0 < self.confidence <= 1:
            raise ValueError("stream confidence must be between 0 and 1")

    @classmethod
    def from_video_stream(cls, stream: VideoStream, *, site_id: str | None = None, area_id: str | None = None, camera_id: str | None = None) -> StreamRuntimeConfig:
        return cls(
            stream_id=stream.id,
            url=stream.url,
            model=stream.model or "models/yolo11n.onnx",
            width=stream.width,
            height=stream.height,
            fps=stream.fps,
            confidence=stream.confidence,
            site_id=site_id,
            area_id=area_id,
            camera_id=camera_id or stream.camera_id,
            name=stream.name,
        )


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    url: str = "rtsp://localhost/live"
    model: str = "models/yolo11n.onnx"
    db: str = "data/factoryguard.db"
    width: int = 1280
    height: int = 720
    confidence: float = 0.25
    confirm_frames: int = 3
    fps: float = 2.0
    media_dir: str = "events-media"
    prebuffer_seconds: float = 3.0
    clip_timeout: float = 30.0
    streams: tuple[StreamRuntimeConfig | VideoStream, ...] = ()
    topology: EntityRegistry | None = None
    inference_mode: str = "central"
    central_inference_url: str = "http://127.0.0.1:8765"
    central_inference_api_key: str = ""
    central_inference_timeout: float = 10.0
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

    def __post_init__(self) -> None:
        mode = str(self.inference_mode).strip().lower()
        if mode not in {"central", "local"}:
            raise ValueError("inference mode must be central or local")
        object.__setattr__(self, "inference_mode", mode)
        if not str(self.central_inference_url).strip():
            raise ValueError("central inference URL is required")
        if not isfinite(self.central_inference_timeout) or self.central_inference_timeout <= 0:
            raise ValueError("central inference timeout must be positive")
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
        if not channels:
            raise ValueError("at least one notification channel is required")
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
                        )
                    )
                    continue
                raise TypeError("MonitorConfig.streams must contain StreamRuntimeConfig or VideoStream")
            return tuple(result)
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
                    )
                )
            return tuple(result)
        return (
            StreamRuntimeConfig(
                stream_id="CH01",
                url=self.url,
                model=self.model,
                width=self.width,
                height=self.height,
                fps=self.fps,
                confidence=self.confidence,
            ),
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

        def request_stop(self) -> None:
            """Thread-safe stop request; called directly by the GUI thread."""
            self._stop.set()

        @Slot()
        def run(self) -> None:
            try:
                runtimes = self.config.resolved_streams()
                engine_label = "中心推理" if self.config.inference_mode == "central" else "本地 CPU"
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

        def _run_stream_once(self, ffmpeg: Any, runtime: StreamRuntimeConfig) -> None:
            """Run one independent capture/inference chain for one stream."""
            frame_no = 0
            store: SQLiteStore | None = None
            last_status: str | None = None

            def report(status: str, detail: str) -> None:
                nonlocal last_status
                changed = status != last_status
                last_status = status
                if changed:
                    self.stream_status_changed.emit(runtime.stream_id, status, detail)
                if store is not None:
                    store.set_stream_health(runtime.stream_id, status, detail, actor="worker")

            try:
                report("starting", "初始化推理与视频连接")
                if self.config.inference_mode == "central":
                    engine = CentralInferenceClient(
                        self.config.central_inference_url,
                        api_key=self.config.central_inference_api_key,
                        timeout=self.config.central_inference_timeout,
                    )
                else:
                    engine = YoloOnnxEngine(runtime.model, confidence=runtime.confidence)
                health = engine.health_check()
                if health.state != "ready":
                    raise RuntimeError(health.detail or f"推理服务不可用: {runtime.stream_id}")
                report("ready", f"{health.provider} 推理已就绪，等待视频帧")
                store = SQLiteStore(self.config.db)
                store.set_stream_health(runtime.stream_id, "ready", "模型已就绪，等待视频帧", actor="worker")
                persisted_rules = store.load_rules(stream_id=runtime.stream_id)
                rules = [rule for rule in persisted_rules if rule.enabled]
                if not persisted_rules:
                    default_rule = Rule(
                        id=f"qt-full-frame-{runtime.stream_id}",
                        stream_id=runtime.stream_id,
                        name="full-frame person detection",
                        type=RuleType.POLYGON,
                        geometry=((0, 0), (runtime.width, 0), (runtime.width, runtime.height), (0, runtime.height)),
                        confirm_frames=self.config.confirm_frames,
                        site_id=runtime.site_id,
                        area_id=runtime.area_id,
                        camera_id=runtime.camera_id,
                    )
                    store.save_rule(default_rule)
                    rules = [default_rule]
                armed = store.get_stream_armed(runtime.stream_id, default=True)
                schedule = store.get_arming_schedule(runtime.stream_id)
                pipeline = DetectionPipeline(
                    RuleEvaluator(rules),
                    store,
                    outbox_channels=self.config.notification_channels,
                )
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
                while not self._stop.is_set():
                    try:
                        raw = next(capture.capture(1, timeout=12.0), None)
                        if raw is None:
                            raise RuntimeError("FFmpeg 未返回视频帧")
                        report("running", "视频帧接收正常")
                        detections = engine.infer_image(raw.image)
                        frame_buffer.append(raw.image.copy())
                        self.preview_ready.emit(raw.image.copy(), detections)
                        self.stream_preview_ready.emit(runtime.stream_id, raw.image.copy(), detections)
                        now = datetime.now(UTC)
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
                        if frame_no % max(1, round(runtime.fps * 2)) == 0:
                            armed = store.get_stream_armed(runtime.stream_id, default=True)
                            schedule = store.get_arming_schedule(runtime.stream_id)
                        schedule_armed = schedule is None or schedule.is_armed(now)
                        result = pipeline.process(frame, detections, armed=armed and schedule_armed)
                        self.frame_processed.emit(frame_no, result.detections)
                        self.stream_frame_processed.emit(runtime.stream_id, frame_no, result.detections)
                        for event in result.events:
                            snapshot_uri = self._save_event_snapshot(raw.image, event)
                            clip_uri = self._save_event_clip(
                                ffmpeg,
                                list(frame_buffer),
                                event,
                                width=runtime.width,
                                height=runtime.height,
                                fps=runtime.fps,
                            )
                            if store is not None and (snapshot_uri is not None or clip_uri is not None):
                                store.update_event_media(
                                    event.id,
                                    snapshot_uri=snapshot_uri,
                                    clip_uri=clip_uri,
                                    clip_status="ready" if clip_uri is not None else "snapshot_only",
                                )
                            payload = _event_payload(event)
                            payload["snapshot_uri"] = snapshot_uri
                            payload["clip_uri"] = clip_uri
                            payload["clip_status"] = "ready" if clip_uri is not None else "snapshot_only"
                            self.event_created.emit(payload)
                        frame_no += 1
                    except Exception as exc:  # noqa: BLE001 - keep each stream alive on transient RTSP loss
                        if self._stop.is_set():
                            break
                        report("degraded", str(exc))
                        self.status_changed.emit("degraded", f"{runtime.stream_id} 视频流暂时不可用：{exc}")
                        self.error.emit(f"{runtime.stream_id}: {exc}")
                        self._stop.wait(1.0)
            except Exception as exc:  # noqa: BLE001 - isolate one bad stream from its siblings
                report("offline", str(exc))
                self.status_changed.emit("error", f"{runtime.stream_id}: {exc}")
                self.error.emit(f"{runtime.stream_id}: {exc}")
            finally:
                report("stopped" if self._stop.is_set() else (last_status or "offline"), "监控已停止" if self._stop.is_set() else "推流线程已退出")
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
