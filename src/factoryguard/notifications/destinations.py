"""Independent notification destination instances."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


_DESTINATION_KINDS = {"webhook", "email", "sms"}


@dataclass(slots=True)
class NotificationDestination:
    """One independently addressable delivery endpoint.

    Public settings are persisted by the UI. Runtime credentials deliberately
    live only in ``secrets`` and are excluded from ``to_public_dict``.
    """

    id: str
    kind: str
    name: str
    provider: str
    enabled: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        self.kind = str(self.kind).strip().lower()
        self.name = str(self.name).strip()
        self.provider = str(self.provider).strip().lower() or "custom"
        if not self.id or not self.name:
            raise ValueError("destination id and name are required")
        if self.kind not in _DESTINATION_KINDS:
            raise ValueError(f"unsupported destination kind: {self.kind}")
        if not isinstance(self.settings, dict):
            raise TypeError("destination settings must be a dictionary")
        if not isinstance(self.secrets, dict):
            raise TypeError("destination secrets must be a dictionary")

    @property
    def outbox_channel(self) -> str:
        """Return a stable channel key while keeping single defaults compatible."""
        if self.id == f"default-{self.kind}":
            return self.kind
        return f"{self.kind}:{self.id}"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "provider": self.provider,
            "enabled": bool(self.enabled),
            "settings": dict(self.settings),
        }

    @classmethod
    def from_public_dict(cls, value: Mapping[str, Any]) -> NotificationDestination:
        settings = value.get("settings", {})
        return cls(
            id=str(value.get("id", "")),
            kind=str(value.get("kind", "")),
            name=str(value.get("name", "")),
            provider=str(value.get("provider", "custom")),
            enabled=bool(value.get("enabled", False)),
            settings=dict(settings) if isinstance(settings, Mapping) else {},
        )


def destination_channel_map(destinations: Iterable[NotificationDestination]) -> dict[str, str]:
    """Map destination IDs to their concrete outbox channel keys."""
    return {destination.id: destination.outbox_channel for destination in destinations}


def resolve_notification_channels(
    destination_ids: Iterable[str],
    *,
    fallback_channels: Iterable[str],
    destination_channels: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Resolve rule-scoped destinations, falling back to global channels.

    An empty ``destination_ids`` list means the rule has no special target and
    therefore uses the global notification channels.  When IDs are present,
    only channels owned by those destination instances are returned.
    """
    mapping = dict(destination_channels or {})
    requested = [str(value).strip() for value in destination_ids]
    if requested:
        channels: list[str] = []
        for destination_id in requested:
            channel = mapping.get(destination_id)
            if channel and channel not in channels:
                channels.append(str(channel).strip().lower())
        return tuple(channels)
    channels = []
    for value in fallback_channels:
        normalized = str(value).strip().lower()
        if normalized and normalized not in channels:
            channels.append(normalized)
    return tuple(channels)

def new_destination_id(kind: str) -> str:
    normalized = str(kind).strip().lower()
    if normalized not in _DESTINATION_KINDS:
        raise ValueError(f"unsupported destination kind: {kind}")
    return f"{normalized}-{uuid4().hex[:10]}"


def default_destination(kind: str, *, enabled: bool, provider: str | None = None) -> NotificationDestination:
    defaults = {
        "webhook": ("默认 Webhook", "custom"),
        "email": ("默认邮箱", "custom"),
        "sms": ("默认短信", "custom_http"),
    }
    try:
        name, default_provider = defaults[str(kind).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported destination kind: {kind}") from exc
    return NotificationDestination(
        id=f"default-{kind}",
        kind=kind,
        name=name,
        provider=provider or default_provider,
        enabled=enabled,
    )


__all__ = [
    "NotificationDestination",
    "default_destination",
    "destination_channel_map",
    "new_destination_id",
    "resolve_notification_channels",
]
