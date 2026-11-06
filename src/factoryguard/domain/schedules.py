"""Pure arming schedule rules for stream-level deployment control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_windows(text: str) -> tuple[TimeWindow, ...]:
    """Parse comma or semicolon separated ``HH:MM-HH:MM`` windows."""
    windows: list[TimeWindow] = []
    for raw in text.replace("，", ",").replace("；", ",").replace(";", ",").split(","):
        value = raw.strip()
        if not value:
            continue
        if "-" not in value:
            raise ValueError(f"时间段格式错误：{value}，应为 HH:MM-HH:MM")
        start, end = (part.strip() for part in value.split("-", 1))
        windows.append(TimeWindow(_parse_minute(start), _parse_minute(end)))
    return tuple(windows)


def _parse_minute(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"时间格式错误：{value}") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"时间超出范围：{value}")
    return hour * 60 + minute


def format_windows(windows: tuple[TimeWindow, ...]) -> str:
    def format_minute(value: int) -> str:
        return f"{value // 60:02d}:{value % 60:02d}"

    return ", ".join(
        f"{format_minute(item.start_minute)}-{format_minute(item.end_minute)}" for item in windows
    )


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A weekly minute range; ``end < start`` means it crosses midnight."""

    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        for value in (self.start_minute, self.end_minute):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 1440:
                raise ValueError("schedule minutes must be integers from 0 through 1439")

    def contains(self, minute: int) -> bool:
        if self.start_minute == self.end_minute:
            return True
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute < self.end_minute
        return minute >= self.start_minute or minute < self.end_minute

    @property
    def crosses_midnight(self) -> bool:
        return self.start_minute > self.end_minute


@dataclass(frozen=True, slots=True)
class ArmingSchedule:
    """Weekly arming windows with holidays and temporary overrides."""

    stream_id: str
    weekly: tuple[tuple[TimeWindow, ...], ...] = ((), (), (), (), (), (), ())
    holidays: frozenset[str] = frozenset()
    enabled: bool = True
    default_armed: bool = True
    override_armed: bool | None = None
    override_until: datetime | None = None
    timezone: str = "local"

    def __post_init__(self) -> None:
        if not self.stream_id:
            raise ValueError("schedule stream_id is required")
        if len(self.weekly) != 7:
            raise ValueError("schedule weekly must contain seven weekday entries")
        for holiday in self.holidays:
            try:
                date.fromisoformat(holiday)
            except ValueError as exc:
                raise ValueError(f"invalid holiday date: {holiday}") from exc
        if self.override_until is not None and self.override_until.tzinfo is None:
            raise ValueError("schedule override_until must be timezone-aware")
        if not isinstance(self.default_armed, bool):
            raise ValueError("default_armed must be boolean")

    def is_armed(self, at: datetime) -> bool:
        """Evaluate the schedule at an aware wall-clock timestamp."""
        if at.tzinfo is None:
            raise ValueError("schedule evaluation time must be timezone-aware")
        if self.override_armed is not None and (
            self.override_until is None or at < self.override_until
        ):
            return self.override_armed
        if not self.enabled:
            return self.default_armed
        local_at = at.astimezone(self._zone())
        if local_at.date().isoformat() in self.holidays:
            return False
        if not any(self.weekly):
            return self.default_armed
        minute = local_at.hour * 60 + local_at.minute
        today = self.weekly[local_at.weekday()]
        if any(window.contains(minute) for window in today):
            return True
        previous = self.weekly[(local_at.weekday() - 1) % 7]
        return minute < 1440 and any(
            window.crosses_midnight and minute < window.end_minute for window in previous
        )

    def _zone(self) -> ZoneInfo | None:
        if self.timezone.strip().lower() in {"", "local", "system"}:
            return None
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown schedule timezone: {self.timezone}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "weekly": [
                [[window.start_minute, window.end_minute] for window in day] for day in self.weekly
            ],
            "holidays": sorted(self.holidays),
            "enabled": self.enabled,
            "default_armed": self.default_armed,
            "override_armed": self.override_armed,
            "override_until": self.override_until.isoformat() if self.override_until else None,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArmingSchedule:
        weekly = tuple(
            tuple(TimeWindow(int(pair[0]), int(pair[1])) for pair in day)
            for day in value.get("weekly", ((), (), (), (), (), (), ()))
        )
        override_until = value.get("override_until")
        parsed_override = datetime.fromisoformat(str(override_until)) if override_until else None
        return cls(
            stream_id=str(value["stream_id"]),
            weekly=weekly,
            holidays=frozenset(str(item) for item in value.get("holidays", ())),
            enabled=bool(value.get("enabled", True)),
            default_armed=bool(value.get("default_armed", True)),
            override_armed=value.get("override_armed"),
            override_until=parsed_override,
            timezone=str(value.get("timezone", "local")),
        )


__all__ = ["ArmingSchedule", "TimeWindow", "format_windows", "parse_windows"]
