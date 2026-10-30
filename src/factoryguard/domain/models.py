from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from types import MappingProxyType
from typing import Any
from .condition_tree import normalize_condition_nodes
from uuid import uuid4

Point = tuple[float, float]
BBox = tuple[float, float, float, float]


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
    """Controls whether native device events or local inference are used."""

    NATIVE_ONLY = "native_only"
    LOCAL_FALLBACK = "local_fallback"
    LOCAL_PRIMARY = "local_primary"
    DISABLED = "disabled"


class EventSource(StrEnum):
    """Authoritative origin of an event."""

    ONVIF = "onvif"
    GB28181 = "gb28181"
    LOCAL_INFERENCE = "local_inference"
    DEVICE_HEALTH = "device_health"
    MANUAL = "manual"


class RuleMode(StrEnum):
    """Whether a rule is evaluated from pixels or device event codes."""

    ANALYSIS = "analysis"
    NATIVE_EVENT = "native_event"


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
    def from_dict(cls, value: Mapping[str, Any] | None) -> "DeviceCapabilities":
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
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.LOCAL_FALLBACK
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.site_id or not self.area_id or not self.name.strip():
            raise ValueError("device id, site_id, area_id and name are required")
        if not self.endpoint.strip():
            raise ValueError("device endpoint is required")
        try:
            protocol = self.protocol if isinstance(self.protocol, DeviceProtocol) else DeviceProtocol(str(self.protocol))
        except ValueError as exc:
            raise ValueError("device protocol must be manual, rtsp, onvif or gb28181") from exc
        try:
            policy = (
                self.analysis_policy
                if isinstance(self.analysis_policy, AnalysisPolicy)
                else AnalysisPolicy(str(self.analysis_policy))
            )
        except ValueError as exc:
            raise ValueError("analysis policy is invalid") from exc
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "analysis_policy", policy)
        if not isinstance(self.capabilities, DeviceCapabilities):
            object.__setattr__(self, "capabilities", DeviceCapabilities.from_dict(self.capabilities))


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
    model: str | None = None
    confidence: float = 0.25
    enabled: bool = True
    input_size: int = 640
    device_id: str | None = None
    analysis_policy: AnalysisPolicy | str | None = None
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
        if not isfinite(self.confidence) or not 0 < self.confidence <= 1:
            raise ValueError("stream confidence must be between 0 and 1")
        if isinstance(self.input_size, bool) or not 64 <= int(self.input_size) <= 4096:
            raise ValueError("stream input_size must be between 64 and 4096")
        object.__setattr__(self, "input_size", int(self.input_size))
        if self.analysis_policy is not None:
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


# Domain language aliases used by integrations that call a site a factory or
# a camera stream simply a stream.
Factory = Site
Camera = CameraDevice
Stream = VideoStream


class RuleType(StrEnum):
    POLYGON = "polygon"
    LINE = "line"


class EventStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Detection:
    """One model output. Coordinates are pixels in the source frame."""

    label: str
    confidence: float
    bbox: BBox
    track_id: str | None = None

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("detection label is required")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("detection confidence must be between 0 and 1")
        if len(self.bbox) != 4:
            raise ValueError("detection bbox must contain four coordinates")
        x1, y1, x2, y2 = self.bbox
        if not all(isfinite(value) for value in self.bbox):
            raise ValueError("detection bbox coordinates must be finite")
        if x2 < x1 or y2 < y1:
            raise ValueError("detection bbox must use x1 <= x2 and y1 <= y2")

    @property
    def anchor(self) -> Point:
        x1, _y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)

    @property
    def height(self) -> float:
        return max(0.0, self.bbox[3] - self.bbox[1])


@dataclass(frozen=True, slots=True)
class Frame:
    stream_id: str
    frame_no: int
    captured_at: datetime
    width: int
    height: int
    detections: tuple[Detection, ...] = ()
    nvr_time: datetime | None = None
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.frame_no < 0:
            raise ValueError("frame_no must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")

    @property
    def entity_ids(self) -> dict[str, str | None]:
        return {
            "site_id": self.site_id,
            "area_id": self.area_id,
            "camera_id": self.camera_id,
            "stream_id": self.stream_id,
        }


@dataclass(frozen=True, slots=True, init=False)
class Rule:
    id: str = ""
    channel_id: str = ""
    name: str = ""
    type: RuleType | None = None
    geometry: tuple[Point, ...] = ()
    target_labels: frozenset[str] = frozenset({"person"})
    min_confidence: float = 0.45
    min_box_height: float = 32.0
    confirm_frames: int = 3
    cooldown_seconds: float = 60.0
    direction: str = "both"
    condition: str = ""
    count_threshold: int = 0
    window_seconds: float = 0.0
    dwell_seconds: float = 0.0
    actions: tuple[str, ...] = ("event", "snapshot", "clip", "notify")
    http_action_url: str = ""
    http_action_timeout: float = 5.0
    ack_deadline_seconds: float = 0.0
    escalation_level: str = ""
    destination_ids: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    condition_nodes: tuple[dict[str, Any], ...] = ()
    trigger_mode: RuleMode | str = RuleMode.ANALYSIS
    native_event_codes: tuple[str, ...] = ()
    dedup_window_seconds: float = 5.0
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
        type: RuleType | None = None,
        geometry: tuple[Point, ...] = (),
        target_labels: frozenset[str] = frozenset({"person"}),
        min_confidence: float = 0.45,
        min_box_height: float = 32.0,
        confirm_frames: int = 3,
        cooldown_seconds: float = 60.0,
        direction: str = "both",
        condition: str = "",
        count_threshold: int = 0,
        window_seconds: float = 0.0,
        dwell_seconds: float = 0.0,
        actions: tuple[str, ...] = ("event", "snapshot", "clip", "notify"),
        http_action_url: str = "",
        http_action_timeout: float = 5.0,
        ack_deadline_seconds: float = 0.0,
        escalation_level: str = "",
        destination_ids: tuple[str, ...] = (),
        assignees: tuple[str, ...] = (),
        condition_nodes: tuple[dict[str, Any], ...] = (),
        enabled: bool = True,
        publication_status: str = "published",
        version: int = 1,
        *,
        trigger_mode: RuleMode | str = RuleMode.ANALYSIS,
        native_event_codes: tuple[str, ...] = (),
        dedup_window_seconds: float = 5.0,
        stream_id: str | None = None,
        site_id: str | None = None,
        area_id: str | None = None,
        camera_id: str | None = None,
    ) -> None:
        resolved_stream = stream_id or channel_id or ""
        if type is not None and not isinstance(type, RuleType):
            try:
                type = RuleType(str(type))
            except ValueError as exc:
                raise ValueError("rule type must be polygon or line") from exc
        values = {
            "id": id,
            "channel_id": resolved_stream,
            "stream_id": resolved_stream,
            "name": name or "",
            "type": type,
            "geometry": geometry,
            "target_labels": frozenset(target_labels),
            "min_confidence": min_confidence,
            "min_box_height": min_box_height,
            "confirm_frames": confirm_frames,
            "cooldown_seconds": cooldown_seconds,
            "direction": direction,
            "condition": condition,
            "count_threshold": count_threshold,
            "window_seconds": window_seconds,
            "dwell_seconds": dwell_seconds,
            "actions": tuple(actions),
            "http_action_url": http_action_url.strip(),
            "http_action_timeout": http_action_timeout,
            "ack_deadline_seconds": ack_deadline_seconds,
            "escalation_level": escalation_level.strip().lower(),
            "destination_ids": tuple(destination_ids),
            "assignees": tuple(assignees),
            "condition_nodes": normalize_condition_nodes(condition_nodes),
            "native_event_codes": tuple(
                dict.fromkeys(str(value).strip().lower() for value in native_event_codes if str(value).strip())
            ),
            "dedup_window_seconds": dedup_window_seconds,
            "enabled": enabled,
            "publication_status": publication_status,
            "version": version,
            "site_id": site_id,
            "area_id": area_id,
            "camera_id": camera_id,
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)
        try:
            mode = trigger_mode if isinstance(trigger_mode, RuleMode) else RuleMode(str(trigger_mode))
        except ValueError as exc:
            raise ValueError("rule trigger_mode must be analysis or native_event") from exc
        object.__setattr__(self, "trigger_mode", mode)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.id or not self.stream_id:
            raise ValueError("rule id and stream_id (channel_id) are required")
        if self.trigger_mode is RuleMode.NATIVE_EVENT:
            self._validate_native_event()
        else:
            self._validate_analysis()
        allowed_actions = {"event", "snapshot", "clip", "notify", "audit", "http"}
        normalized_actions: list[str] = []
        for action in self.actions:
            normalized = str(action).strip().lower()
            if normalized not in allowed_actions:
                raise ValueError(f"unsupported rule action: {action!r}")
            if normalized not in normalized_actions:
                normalized_actions.append(normalized)
        if "event" not in normalized_actions:
            normalized_actions.insert(0, "event")
        object.__setattr__(self, "actions", tuple(normalized_actions))
        if self.http_action_url and not self.http_action_url.lower().startswith(("http://", "https://")):
            raise ValueError("http_action_url must use HTTP or HTTPS")
        if not isfinite(self.http_action_timeout) or self.http_action_timeout <= 0:
            raise ValueError("http_action_timeout must be positive")
        if "http" in normalized_actions and not self.http_action_url:
            raise ValueError("http action requires http_action_url")
        if not isfinite(self.ack_deadline_seconds) or self.ack_deadline_seconds < 0:
            raise ValueError("ack_deadline_seconds must be finite and non-negative")
        if self.escalation_level and self.escalation_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("escalation_level must be low, medium, high or critical")
        if any(not str(value).strip() for value in self.destination_ids):
            raise ValueError("destination_ids must not contain empty values")
        if any(not str(value).strip() for value in self.assignees):
            raise ValueError("assignees must not contain empty values")
        if self.publication_status not in {"draft", "pending_review", "published"}:
            raise ValueError("publication_status must be draft, pending_review or published")

    def _validate_native_event(self) -> None:
        if not self.native_event_codes:
            raise ValueError("native event rules require at least one event code")
        if not isfinite(self.dedup_window_seconds) or self.dedup_window_seconds < 0:
            raise ValueError("dedup_window_seconds must be finite and non-negative")
        object.__setattr__(self, "condition", self.condition or "native_event")

    def _validate_analysis(self) -> None:
        if not isinstance(self.type, RuleType):
            raise TypeError("rule type must be polygon or line")
        if not self.geometry or not all(
            len(point) == 2 and all(isfinite(value) for value in point) for point in self.geometry
        ):
            raise ValueError("rule geometry must contain finite points")
        if self.type is RuleType.POLYGON and len(self.geometry) < 3:
            raise ValueError("polygon rules require at least three points")
        if self.type is RuleType.LINE and len(self.geometry) != 2:
            raise ValueError("line rules require exactly two points")
        if self.type is RuleType.POLYGON:
            from .geometry import polygon_is_valid

            if not polygon_is_valid(self.geometry):
                raise ValueError("polygon rule geometry must be non-degenerate and non-self-intersecting")
        elif self.geometry[0] == self.geometry[1]:
            raise ValueError("line rule endpoints must be distinct")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not isfinite(self.min_box_height) or self.min_box_height < 0:
            raise ValueError("min_box_height must be finite and non-negative")
        if not isinstance(self.confirm_frames, int) or self.confirm_frames < 1:
            raise ValueError("confirm_frames must be positive and cooldown non-negative")
        if not isfinite(self.cooldown_seconds) or self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be finite and non-negative")
        if self.direction not in {"both", "up", "down", "left", "right"}:
            raise ValueError("direction must be both, up, down, left, or right")
        condition = self.condition or ("line_cross" if self.type is RuleType.LINE else "intrusion")
        line_conditions = {"line_cross", "line_count"}
        area_conditions = {"intrusion", "occupancy", "dwell"}
        if condition not in line_conditions | area_conditions:
            raise ValueError("condition must be intrusion, line_cross, line_count, occupancy, or dwell")
        if condition in line_conditions and self.type is not RuleType.LINE:
            raise ValueError("line conditions require a line rule")
        if condition in area_conditions and self.type is not RuleType.POLYGON:
            raise ValueError("area conditions require a polygon rule")
        if condition in {"line_count", "occupancy"} and (isinstance(self.count_threshold, bool) or self.count_threshold < 1):
            raise ValueError("count_threshold must be positive for count conditions")
        if condition == "line_count" and (not isfinite(self.window_seconds) or self.window_seconds <= 0):
            raise ValueError("window_seconds must be positive for line_count")
        if condition == "dwell" and (not isfinite(self.dwell_seconds) or self.dwell_seconds <= 0):
            raise ValueError("dwell_seconds must be positive for dwell")
        object.__setattr__(self, "condition", condition)


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
            source = self.source if isinstance(self.source, EventSource) else EventSource(str(self.source))
        except ValueError as exc:
            raise ValueError("native event source is invalid") from exc
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "event_code", self.event_code.strip().lower())
        object.__setattr__(self, "stream_id", self.stream_id or self.channel_id)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @property
    def correlation_key(self) -> str:
        semantic = self.payload.get("semantic") or self.event_code
        return f"native:{self.stream_id}:{str(semantic).strip().lower()}"

    def to_event(self, *, rule: Rule) -> "Event":
        if rule.trigger_mode is not RuleMode.NATIVE_EVENT:
            raise ValueError("native events require a native_event rule")
        if self.event_code not in rule.native_event_codes:
            raise ValueError(f"rule {rule.id} does not accept event code {self.event_code}")
        source_event_id = self.source_event_id or f"{self.source.value}:{self.event_code}:{self.occurred_at.isoformat()}"
        return Event(
            id=f"evt-{uuid4().hex}",
            dedup_key=f"native:{self.source.value}:{source_event_id}",
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
    track: tuple[Point, ...] = ()
    assignees: tuple[str, ...] = ()
    stream_id: str | None = None
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None
    device_id: str | None = None
    source: EventSource | str = EventSource.LOCAL_INFERENCE
    source_event_id: str | None = None
    native: bool = False
    analysis_policy: AnalysisPolicy | str = AnalysisPolicy.LOCAL_FALLBACK
    correlation_key: str = ""
    dedup_window_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.id or not self.dedup_key or not self.rule_id or not self.channel_id:
            raise ValueError("event id, dedup_key, rule_id and stream_id are required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("event occurred_at must be timezone-aware")
        if self.stream_id is None:
            object.__setattr__(self, "stream_id", self.channel_id)
        try:
            source = self.source if isinstance(self.source, EventSource) else EventSource(str(self.source))
        except ValueError as exc:
            raise ValueError("event source is invalid") from exc
        try:
            policy = (
                self.analysis_policy
                if isinstance(self.analysis_policy, AnalysisPolicy)
                else AnalysisPolicy(str(self.analysis_policy))
            )
        except ValueError as exc:
            raise ValueError("event analysis policy is invalid") from exc
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "analysis_policy", policy)
        object.__setattr__(self, "native", bool(self.native))
        if not isfinite(self.dedup_window_seconds) or self.dedup_window_seconds < 0:
            raise ValueError("event dedup_window_seconds must be finite and non-negative")
        if not self.correlation_key:
            semantic = self.target.get("semantic") or self.target.get("condition") or self.target.get("label") or "event"
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

    @classmethod
    def create(
        cls,
        *,
        rule: Rule,
        frame: Frame,
        detection: Detection,
        track: Sequence[Point] = (),
        level: str = "high",
    ) -> "Event":
        track_id = detection.track_id or "untracked"
        dedup_key = f"{frame.stream_id}:{rule.id}:{track_id}:{frame.captured_at.isoformat()}"
        nvr_time = frame.nvr_time
        delta = None
        if nvr_time is not None:
            delta = round((frame.captured_at - nvr_time).total_seconds() * 1000)
        return cls(
            id=f"evt-{uuid4().hex}",
            dedup_key=dedup_key,
            rule_id=rule.id,
            channel_id=rule.channel_id,
            stream_id=frame.stream_id,
            site_id=frame.site_id or rule.site_id,
            area_id=frame.area_id or rule.area_id,
            camera_id=frame.camera_id or rule.camera_id,
            level=level,
            occurred_at=frame.captured_at,
            nvr_time=nvr_time,
            clock_delta_ms=delta,
            target={
                "label": detection.label,
                "confidence": detection.confidence,
                "bbox": detection.bbox,
                "track_id": detection.track_id,
                "condition": rule.condition,
                "rule_name": rule.name,
                "assignees": list(rule.assignees),
            },
            track=tuple(track),
            assignees=tuple(rule.assignees),
            source=EventSource.LOCAL_INFERENCE,
            native=False,
            analysis_policy=AnalysisPolicy.LOCAL_FALLBACK,
            correlation_key=f"semantic:{frame.stream_id}:{rule.condition}",
            dedup_window_seconds=rule.dedup_window_seconds,
        )


def utc_now() -> datetime:
    return datetime.now(UTC)
