"""Native-first analysis policy and event-to-rule bridge."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from factoryguard.domain.models import (
    AnalysisPolicy,
    DeviceCapabilities,
    Event,
    EventSource,
    NativeEvent,
    Rule,
)
from factoryguard.notifications.actions import execute_http_action
from factoryguard.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class AnalysisDecision:
    """Describe whether native device events are currently ingested."""

    policy: AnalysisPolicy
    use_native: bool
    reason: str

    @property
    def label(self) -> str:
        return "设备原生事件" if self.use_native else "事件接入未就绪"


def decide_analysis(
    *,
    policy: AnalysisPolicy | str,
    capabilities: DeviceCapabilities,
    native_subscription_healthy: bool,
    native_rules: Sequence[Rule] = (),
    analysis_rules: Sequence[Rule] = (),
) -> AnalysisDecision:
    """Return the native-event state; local frame analysis is unsupported."""
    del native_rules, analysis_rules
    resolved = AnalysisPolicy.normalize(policy)
    if resolved is AnalysisPolicy.DISABLED:
        return AnalysisDecision(resolved, False, "事件接入已停用")
    native_available = capabilities.native_events and native_subscription_healthy
    return AnalysisDecision(
        resolved,
        native_available,
        "设备原生事件已就绪" if native_available else "设备原生事件不可用",
    )


class NativeEventIngestor:
    """Turn checked device events into the existing event/outbox workflow."""

    def __init__(
        self, store: SQLiteStore, rules: Sequence[Rule], *, channels: Sequence[str] = ()
    ) -> None:
        self.store = store
        self.rules = tuple(rule for rule in rules if rule.enabled)
        self.channels = tuple(dict.fromkeys(str(value) for value in channels if str(value)))
        self.last_action_errors: list[str] = []

    def matching_rules(self, event: NativeEvent) -> tuple[Rule, ...]:
        return tuple(
            rule
            for rule in self.rules
            if rule.stream_id == (event.stream_id or event.channel_id)
            and event.event_code in rule.native_event_codes
            and (rule.site_id is None or rule.site_id == event.site_id)
            and (rule.area_id is None or rule.area_id == event.area_id)
            and (rule.camera_id is None or rule.camera_id == event.camera_id)
        )

    def ingest(self, event: NativeEvent, *, actor: str = "device-event") -> tuple[Event, ...]:
        stored: list[Event] = []
        for rule in self.matching_rules(event):
            if self.store.has_recent_correlated_event(
                event.correlation_key,
                within_seconds=rule.dedup_window_seconds,
                exclude_source=str(event.source),
                at=event.occurred_at,
            ):
                continue
            mapped = event.to_event(rule=rule)
            created = self.store.create_event_with_outbox(
                mapped,
                channels=self.channels,
                actor=actor,
                enqueue=bool(self.channels),
            )
            if created:
                if "http" in rule.actions:
                    ok, detail = execute_http_action(
                        mapped,
                        url=rule.http_action_url,
                        timeout=rule.http_action_timeout,
                    )
                    if not ok:
                        self.last_action_errors.append(f"{rule.id}: {detail}")
                stored.append(mapped)
        return tuple(stored)

    def record_subscription(
        self,
        device_id: str,
        source: EventSource | str,
        *,
        healthy: bool,
        detail: str = "",
        last_event_at: datetime | None = None,
    ) -> None:
        self.store.set_device_subscription(
            device_id,
            source=str(source),
            state="healthy" if healthy else "degraded",
            detail=detail,
            last_event_at=last_event_at,
            actor="device-event",
        )


__all__ = ["AnalysisDecision", "NativeEventIngestor", "decide_analysis"]
