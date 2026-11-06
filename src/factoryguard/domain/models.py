from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any
from uuid import uuid4

Point = tuple[float, float]


class StreamRole(StrEnum):
    """The purpose of a camera's RTSP stream.

    A camera may expose several streams at once.  Keeping the role on the
    stream entity avoids treating a camera or a numeric channel as the unit
    of detection.
    """

    MAIN = "main"
    SUB = "sub"
    ANALYSIS = "analysis"


class DeviceProtocol(StrEnum):
    """How FactoryGuard connects to a physical video device."""

    MANUAL = "manual"
    RTSP = "rtsp"
    ONVIF = "onvif"
    GB28181 = "gb28181"


class AnalysisPolicy(StrEnum):
    """Controls whether native device events are ingested."""

    NATIVE_ONLY = "native_only"
    DISABLED = "disabled"

    @classmethod
    def normalize(cls, value: AnalysisPolicy | str | None) -> AnalysisPolicy:
        if value is None:
            return cls.NATIVE_ONLY
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"local_fallback", "local_primary", "analysis"}:
            return cls.NATIVE_ONLY
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError("analysis policy is invalid") from exc


class EventSource(StrEnum):
    """Authoritative origin of an event."""

    ONVIF = "onvif"
    GB28181 = "gb28181"
    SYSTEM = "system"
    DEVICE_HEALTH = "device_health"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Site:
    """A physical factory/site that owns one or more areas."""

    id: str
    name: str
    description: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.name.strip():
            raise ValueError("site id and name are required")


@dataclass(frozen=True, slots=True)
class Area:
    """A nested operational area inside a site."""

    id: str
    site_id: str
    name: str
    parent_id: str | None = None
    geometry: tuple[Point, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.site_id or not self.name.strip():
            raise ValueError("area id, site_id and name are required")
        if self.geometry and not all(
            len(point) == 2 and all(isfinite(value) for value in point) for point in self.geometry
        ):
            raise ValueError("area geometry must contain finite points")


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    """A checked capability snapshot. Unsupported features stay false."""

    live_video: bool = False
    native_events: bool = False
    recording_search: bool = False
    ptz: bool = False
    digital_io: bool = False
    metadata: bool = False
    profiles: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    media_profiles: tuple[str, ...] = ()

    def supports(self, event_code: str) -> bool:
        normalized = str(event_code).strip().lower()
        return self.native_events and (
            not self.event_types
            or normalized in {str(value).strip().lower() for value in self.event_types}
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "live_video": self.live_video,
            "native_events": self.native_events,
            "recording_search": self.recording_search,
            "ptz": self.ptz,
            "digital_io": self.digital_io,
            "metadata": self.metadata,
            "profiles": list(self.profiles),
            "event_types": list(self.event_types),
            "media_profiles": list(self.media_profiles),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> DeviceCapabilities:
        data = value if isinstance(value, Mapping) else {}

        def values(key: str) -> tuple[str, ...]:
            raw = data.get(key, ())
            if isinstance(raw, str):
                raw = (raw,)
            try:
                return tuple(str(item) for item in raw)
            except TypeError:
                return ()

        return cls(
            live_video=bool(data.get("live_video", False)),
            native_events=bool(data.get("native_events", False)),
            recording_search=bool(data.get("recording_search", False)),
            ptz=bool(data.get("ptz", False)),
            digital_io=bool(data.get("digital_io", False)),
            metadata=bool(data.get("metadata", False)),
            profiles=values("profiles"),
            event_types=values("event_types"),
            media_profiles=values("media_profiles"),
        )


@dataclass(frozen=True, slots=True)
class VideoDevice:
    """A physical camera, encoder or NVR reached through a standard protocol."""

    id: str
    site_id: str
    area_id: str
    name: str
    protocol: DeviceProtocol | str
    endpoint: str
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    serial_number: str = ""
    device_id: str = ""
    credentials_ref: str | None = None
    capabilities: DeviceCapabilities = DeviceCapabilities()
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.NATIVE_ONLY
    enabled: bool = True
    last_probe_at: str | None = None
    last_ok_at: str | None = None
    health_status: str = "unknown"
    health_detail: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.site_id or not self.area_id or not self.name.strip():
            raise ValueError("device id, site_id, area_id and name are required")
        if not self.endpoint.strip():
            raise ValueError("device endpoint is required")
        try:
            protocol = (
                self.protocol
                if isinstance(self.protocol, DeviceProtocol)
                else DeviceProtocol(str(self.protocol))
            )
        except ValueError as exc:
            raise ValueError("device protocol must be manual, rtsp, onvif or gb28181") from exc
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(
            self,
            "analysis_policy",
            AnalysisPolicy.normalize(self.analysis_policy),
        )
        if not isinstance(self.capabilities, DeviceCapabilities):
            object.__setattr__(
                self, "capabilities", DeviceCapabilities.from_dict(self.capabilities)
            )
        for field_name in ("last_probe_at", "last_ok_at"):
            value = getattr(self, field_name)
            if isinstance(value, datetime):
                object.__setattr__(self, field_name, value.astimezone(UTC).isoformat())
        if self.health_status not in {"unknown", "ready", "degraded", "offline"}:
            raise ValueError("device health_status must be unknown, ready, degraded or offline")


@dataclass(frozen=True, slots=True)
class CameraDevice:
    """A physical video channel exposed by a camera, encoder or NVR."""

    id: str
    area_id: str
    name: str
    vendor: str = ""
    address: str = ""
    enabled: bool = True
    device_id: str | None = None
    channel_id: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.area_id or not self.name.strip():
            raise ValueError("camera id, area_id and name are required")


@dataclass(frozen=True, slots=True)
class VideoStream:
    """One independently consumable video stream exposed by a channel."""

    id: str
    camera_id: str
    name: str
    url: str
    role: StreamRole | str = StreamRole.MAIN
    width: int = 1280
    height: int = 720
    fps: float = 2.0
    enabled: bool = True
    device_id: str | None = None
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.NATIVE_ONLY
    native_event_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.camera_id or not self.name.strip():
            raise ValueError("stream id, camera_id and name are required")
        if not self.url.strip():
            raise ValueError("stream url is required")
        try:
            role = self.role if isinstance(self.role, StreamRole) else StreamRole(str(self.role))
        except ValueError as exc:
            raise ValueError("stream role must be main, sub, or analysis") from exc
        object.__setattr__(self, "role", role)
        if self.width <= 0 or self.height <= 0:
            raise ValueError("stream dimensions must be positive")
        if not isfinite(self.fps) or self.fps <= 0:
            raise ValueError("stream fps must be positive")
        object.__setattr__(
            self,
            "analysis_policy",
            AnalysisPolicy.normalize(self.analysis_policy),
        )
        object.__setattr__(
            self,
            "native_event_codes",
            tuple(
                dict.fromkeys(
                    str(value).strip().lower()
                    for value in self.native_event_codes
                    if str(value).strip()
                )
            ),
        )


# Domain language aliases used by integrations that call a site a factory or
# a camera stream simply a stream.
Factory = Site
Camera = CameraDevice
Stream = VideoStream


class EventStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True, init=False)
class NativeEventRule:
    """Maps authoritative device event codes into the event workflow."""

    id: str = ""
    channel_id: str = ""
    name: str = ""
    native_event_codes: tuple[str, ...] = ()
    dedup_window_seconds: float = 5.0
    actions: tuple[str, ...] = ("event", "notify")
    http_action_url: str = ""
    http_action_timeout: float = 5.0
    ack_deadline_seconds: float = 0.0
    escalation_level: str = ""
    destination_ids: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    enabled: bool = True
    publication_status: str = "published"
    version: int = 1
    stream_id: str = ""
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None

    def __init__(
        self,
        id: str,
        channel_id: str | None = None,
        name: str | None = None,
        *_legacy_positional: Any,
        native_event_codes: Sequence[str] = (),
        dedup_window_seconds: float = 5.0,
        actions: Sequence[str] = ("event", "notify"),
        http_action_url: str = "",
        http_action_timeout: float = 5.0,
        ack_deadline_seconds: float = 0.0,
        escalation_level: str = "",
        destination_ids: Sequence[str] = (),
        assignees: Sequence[str] = (),
        enabled: bool = True,
        publication_status: str = "published",
        version: int = 1,
        stream_id: str | None = None,
        site_id: str | None = None,
        area_id: str | None = None,
        camera_id: str | None = None,
        **legacy: Any,
    ) -> None:
        del _legacy_positional, legacy  # Legacy visual-rule fields are intentionally ignored.
        resolved_stream = stream_id or channel_id or ""
        resolved_channel = channel_id or resolved_stream
        values = {
            "id": id,
            "channel_id": resolved_channel,
            "name": name or "",
            "native_event_codes": tuple(native_event_codes),
            "dedup_window_seconds": dedup_window_seconds,
            "actions": tuple(actions),
            "http_action_url": http_action_url.strip(),
            "http_action_timeout": http_action_timeout,
            "ack_deadline_seconds": ack_deadline_seconds,
            "escalation_level": escalation_level.strip().lower(),
            "destination_ids": tuple(destination_ids),
            "assignees": tuple(assignees),
            "enabled": bool(enabled),
            "publication_status": publication_status,
            "version": version,
            "stream_id": resolved_stream,
            "site_id": site_id,
            "area_id": area_id,
            "camera_id": camera_id,
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.id or not self.stream_id:
            raise ValueError("rule id and stream_id (channel_id) are required")
        if not isfinite(self.dedup_window_seconds) or self.dedup_window_seconds < 0:
            raise ValueError("rule dedup_window_seconds must be finite and non-negative")
        if not isfinite(self.http_action_timeout) or self.http_action_timeout <= 0:
            raise ValueError("rule http_action_timeout must be positive")
        if not isfinite(self.ack_deadline_seconds) or self.ack_deadline_seconds < 0:
            raise ValueError("rule ack_deadline_seconds must be finite and non-negative")
        allowed_actions = {"event", "notify", "audit", "http"}
        normalized_actions: list[str] = []
        for action in self.actions:
            normalized = str(action).strip().lower()
            if normalized not in allowed_actions or normalized in normalized_actions:
                continue
            normalized_actions.append(normalized)
        if "event" not in normalized_actions:
            normalized_actions.insert(0, "event")
        object.__setattr__(self, "actions", tuple(normalized_actions))
        object.__setattr__(
            self,
            "native_event_codes",
            tuple(
                dict.fromkeys(
                    str(value).strip().lower()
                    for value in self.native_event_codes
                    if str(value).strip()
                )
            ),
        )
        object.__setattr__(self, "destination_ids", tuple(dict.fromkeys(self.destination_ids)))
        object.__setattr__(self, "assignees", tuple(dict.fromkeys(self.assignees)))
        if self.publication_status not in {"draft", "pending_review", "published", "rejected"}:
            raise ValueError("rule publication_status is invalid")
        if self.version < 1:
            raise ValueError("rule version must be positive")


Rule = NativeEventRule


@dataclass(frozen=True, slots=True)
class NativeEvent:
    """One authoritative event produced by a camera, encoder or NVR."""

    device_id: str
    channel_id: str
    event_code: str
    occurred_at: datetime
    source: EventSource | str
    title: str = ""
    level: str = "high"
    source_event_id: str | None = None
    stream_id: str | None = None
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None
    payload: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.device_id or not self.channel_id or not self.event_code.strip():
            raise ValueError("native event device_id, channel_id and event_code are required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("native event occurred_at must be timezone-aware")
        try:
            source = (
                self.source
                if isinstance(self.source, EventSource)
                else EventSource(str(self.source))
            )
        except ValueError as exc:
            raise ValueError("native event source is invalid") from exc
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "event_code", self.event_code.strip().lower())
        object.__setattr__(self, "stream_id", self.stream_id or self.channel_id)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @property
    def correlation_key(self) -> str:
        semantic = self.payload.get("semantic") or self.event_code
        return f"native:{self.device_id}:{self.channel_id}:{str(semantic).strip().lower()}"

    def to_event(self, *, rule: Rule) -> Event:
        if self.event_code not in rule.native_event_codes:
            raise ValueError(f"rule {rule.id} does not accept event code {self.event_code}")
        source_event_id = (
            self.source_event_id
            or f"{self.source.value}:{self.event_code}:{self.occurred_at.isoformat()}"
        )
        return Event(
            id=f"evt-{uuid4().hex}",
            dedup_key=f"native:{self.source.value}:{self.device_id}:{source_event_id}",
            rule_id=rule.id,
            channel_id=rule.channel_id,
            stream_id=self.stream_id or rule.stream_id,
            site_id=self.site_id or rule.site_id,
            area_id=self.area_id or rule.area_id,
            camera_id=self.camera_id or rule.camera_id,
            level=rule.escalation_level or self.level,
            occurred_at=self.occurred_at,
            target={
                "label": self.event_code,
                "event_code": self.event_code,
                "title": self.title or self.event_code,
                "source_event_id": source_event_id,
                "native_payload": dict(self.payload),
                "rule_name": rule.name,
                "assignees": list(rule.assignees),
            },
            clip_status="native_reference",
            assignees=tuple(rule.assignees),
            source=self.source,
            source_event_id=source_event_id,
            native=True,
            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
            correlation_key=self.correlation_key,
            dedup_window_seconds=rule.dedup_window_seconds,
            device_id=self.device_id,
        )


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    dedup_key: str
    rule_id: str
    channel_id: str
    level: str
    occurred_at: datetime
    target: Mapping[str, Any]
    status: EventStatus = EventStatus.OPEN
    nvr_time: datetime | None = None
    clock_delta_ms: int | None = None
    snapshot_uri: str | None = None
    clip_uri: str | None = None
    clip_status: str = "pending"
    assignees: tuple[str, ...] = ()
    stream_id: str | None = None
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None
    device_id: str | None = None
    source: EventSource | str = EventSource.SYSTEM
    source_event_id: str | None = None
    native: bool = False
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.NATIVE_ONLY
    correlation_key: str = ""
    dedup_window_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.id or not self.dedup_key or not self.rule_id or not self.channel_id:
            raise ValueError("event id, dedup_key, rule_id and channel_id are required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("event occurred_at must be timezone-aware")
        if self.stream_id is None:
            object.__setattr__(self, "stream_id", self.channel_id)
        try:
            source = (
                self.source
                if isinstance(self.source, EventSource)
                else EventSource(str(self.source))
            )
        except ValueError as exc:
            raise ValueError("event source is invalid") from exc
        object.__setattr__(self, "source", source)
        object.__setattr__(
            self,
            "analysis_policy",
            AnalysisPolicy.normalize(self.analysis_policy),
        )
        object.__setattr__(self, "native", bool(self.native))
        if not isfinite(self.dedup_window_seconds) or self.dedup_window_seconds < 0:
            raise ValueError("event dedup_window_seconds must be finite and non-negative")
        if not self.correlation_key:
            semantic = (
                self.target.get("semantic")
                or self.target.get("condition")
                or self.target.get("label")
                or "event"
            )
            object.__setattr__(
                self,
                "correlation_key",
                f"semantic:{self.stream_id or self.channel_id}:{str(semantic).strip().lower()}",
            )

    @property
    def entity_ids(self) -> dict[str, str | None]:
        return {
            "site_id": self.site_id,
            "area_id": self.area_id,
            "camera_id": self.camera_id,
            "stream_id": self.stream_id or self.channel_id,
        }


def utc_now() -> datetime:
    return datetime.now(UTC)
