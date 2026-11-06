"""Operator-facing incident records.

Incidents are intentionally separate from low-level device events.  A camera
may emit several events for one real-world situation; the merchant console
turns those signals, or a direct request from a user, into one actionable
incident that can be acknowledged and closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    FALSE_ALARM = "false_alarm"


class IncidentSource(StrEnum):
    USER = "user"
    AI_AGENT = "ai_agent"
    DEVICE = "device"
    SYSTEM = "system"


SEVERITY_LABELS = {
    IncidentSeverity.LOW: "提示",
    IncidentSeverity.MEDIUM: "关注",
    IncidentSeverity.HIGH: "高危",
    IncidentSeverity.CRITICAL: "紧急",
}

STATUS_LABELS = {
    IncidentStatus.OPEN: "待处理",
    IncidentStatus.ACKNOWLEDGED: "处理中",
    IncidentStatus.RESOLVED: "已解决",
    IncidentStatus.FALSE_ALARM: "误报",
}

SOURCE_LABELS = {
    IncidentSource.USER: "用户上报",
    IncidentSource.AI_AGENT: "问询登记",
    IncidentSource.DEVICE: "设备告警",
    IncidentSource.SYSTEM: "系统发现",
}


@dataclass(frozen=True, slots=True)
class Incident:
    """One user-visible safety or security incident."""

    id: str
    title: str
    description: str = ""
    severity: IncidentSeverity | str = IncidentSeverity.MEDIUM
    status: IncidentStatus | str = IncidentStatus.OPEN
    site_id: str | None = None
    area_id: str | None = None
    camera_id: str | None = None
    site_name_snapshot: str = ""
    area_name_snapshot: str = ""
    camera_name_snapshot: str = ""
    source: IncidentSource | str = IncidentSource.USER
    reporter: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ai_summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.title.strip():
            raise ValueError("incident id and title are required")
        try:
            severity = (
                self.severity
                if isinstance(self.severity, IncidentSeverity)
                else IncidentSeverity(str(self.severity))
            )
            status = (
                self.status
                if isinstance(self.status, IncidentStatus)
                else IncidentStatus(str(self.status))
            )
            source = (
                self.source
                if isinstance(self.source, IncidentSource)
                else IncidentSource(str(self.source))
            )
        except ValueError as exc:
            raise ValueError("incident severity, status or source is invalid") from exc
        for field_name in ("occurred_at", "created_at", "updated_at"):
            value = getattr(self, field_name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(f"incident {field_name} must be timezone-aware")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("incident metadata must be a mapping")
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def create(
        cls,
        *,
        title: str,
        description: str = "",
        severity: IncidentSeverity | str = IncidentSeverity.MEDIUM,
        site_id: str | None = None,
        area_id: str | None = None,
        camera_id: str | None = None,
        site_name: str = "",
        area_name: str = "",
        camera_name: str = "",
        source: IncidentSource | str = IncidentSource.USER,
        reporter: str = "",
        ai_summary: str = "",
        metadata: Mapping[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> Incident:
        now = datetime.now(UTC)
        return cls(
            id=f"inc-{uuid4().hex}",
            title=title,
            description=description,
            severity=severity,
            site_id=site_id,
            area_id=area_id,
            camera_id=camera_id,
            site_name_snapshot=site_name,
            area_name_snapshot=area_name,
            camera_name_snapshot=camera_name,
            source=source,
            reporter=reporter,
            occurred_at=occurred_at or now,
            created_at=now,
            updated_at=now,
            ai_summary=ai_summary,
            metadata=dict(metadata or {}),
        )

    def with_status(self, status: IncidentStatus | str) -> Incident:
        normalized = status if isinstance(status, IncidentStatus) else IncidentStatus(str(status))
        return Incident(
            id=self.id,
            title=self.title,
            description=self.description,
            severity=self.severity,
            status=normalized,
            site_id=self.site_id,
            area_id=self.area_id,
            camera_id=self.camera_id,
            source=self.source,
            reporter=self.reporter,
            occurred_at=self.occurred_at,
            created_at=self.created_at,
            updated_at=datetime.now(UTC),
            ai_summary=self.ai_summary,
            metadata=self.metadata,
        )


__all__ = [
    "SEVERITY_LABELS",
    "SOURCE_LABELS",
    "STATUS_LABELS",
    "Incident",
    "IncidentSeverity",
    "IncidentSource",
    "IncidentStatus",
]
