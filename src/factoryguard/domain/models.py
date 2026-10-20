from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Any
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
    """A nested operational area inside a site.

    ``parent_id`` is optional for a top-level area and can point to another
    area in the same site for warehouses, workshops, lines, and zones.
    """

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
class CameraDevice:
    """A physical camera installed in an area."""

    id: str
    area_id: str
    name: str
    vendor: str = ""
    address: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.area_id or not self.name.strip():
            raise ValueError("camera id, area_id and name are required")


@dataclass(frozen=True, slots=True)
class VideoStream:
    """One independently consumable RTSP stream exposed by a camera."""

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
    # ``channel_id`` remains the wire/API compatibility name.  It is always
    # normalized to the same value as ``stream_id`` in ``__init__``.
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
    enabled: bool = True
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
        enabled: bool = True,
        version: int = 1,
        *,
        stream_id: str | None = None,
        site_id: str | None = None,
        area_id: str | None = None,
        camera_id: str | None = None,
    ) -> None:
        resolved_stream = stream_id or channel_id or ""
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "channel_id", resolved_stream)
        object.__setattr__(self, "stream_id", resolved_stream)
        object.__setattr__(self, "name", name or "")
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "geometry", geometry)
        object.__setattr__(self, "target_labels", frozenset(target_labels))
        object.__setattr__(self, "min_confidence", min_confidence)
        object.__setattr__(self, "min_box_height", min_box_height)
        object.__setattr__(self, "confirm_frames", confirm_frames)
        object.__setattr__(self, "cooldown_seconds", cooldown_seconds)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "site_id", site_id)
        object.__setattr__(self, "area_id", area_id)
        object.__setattr__(self, "camera_id", camera_id)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.id or not self.stream_id:
            raise ValueError("rule id and stream_id (channel_id) are required")
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
            # Keep the geometry invariant at the domain boundary instead of
            # allowing an invalid polygon to silently match no detections.
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
    stream_id: str | None = None
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None

    def __post_init__(self) -> None:
        # Events created by older integrations only carry ``channel_id``.
        # Normalize the new stream identity while retaining that field for
        # existing webhooks and database queries.
        if not self.id or not self.dedup_key or not self.rule_id or not self.channel_id:
            raise ValueError("event id, dedup_key, rule_id and stream_id are required")
        if self.stream_id is None:
            object.__setattr__(self, "stream_id", self.channel_id)

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
    ) -> Event:
        track_id = detection.track_id or "untracked"
        # The same track may generate another alert after cooldown. Include
        # the frame timestamp so SQLite idempotency suppresses retries of one
        # event without suppressing later events for the same person.
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
            },
            track=tuple(track),
        )


def utc_now() -> datetime:
    return datetime.now(UTC)
