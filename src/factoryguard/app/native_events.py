from __future__ import annotations

"""Native-first analysis policy and event-to-rule bridge."""

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from factoryguard.domain.models import (
    AnalysisPolicy,
    DeviceCapabilities,
    Event,
    EventSource,
    NativeEvent,
    Rule,
    RuleMode,
)
from factoryguard.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class AnalysisDecision:
    policy: AnalysisPolicy
    use_native: bool
    use_local: bool
    reason: str

    @property
    def label(self) -> str:
        if self.use_local and self.use_native:
            return "设备事件 + 本机备用分析"
        if self.use_native:
            return "设备原生事件"
        if self.use_local:
            return "本机备用分析"
        return "分析已停用"


def decide_analysis(
    *,
    policy: AnalysisPolicy | str,
    capabilities: DeviceCapabilities,
    native_subscription_healthy: bool,
    native_rules: Sequence[Rule] = (),
    analysis_rules: Sequence[Rule] = (),
) -> AnalysisDecision:
    try:
        resolved = policy if isinstance(policy, AnalysisPolicy) else AnalysisPolicy(str(policy))
    except ValueError as exc:
        raise ValueError("analysis policy is invalid") from exc
    if resolved is AnalysisPolicy.DISABLED:
        return AnalysisDecision(resolved, False, False, "已停用")
    native_available = capabilities.native_events and native_subscription_healthy
    has_native_work = bool(native_rules)
    has_analysis_work = bool(analysis_rules)
    if resolved is AnalysisPolicy.NATIVE_ONLY:
        return AnalysisDecision(
            resolved,
            native_available,
            False,
            "仅接收设备原生事件" if native_available else "设备原生事件不可用且不允许本机兜底",
        )
    if resolved is AnalysisPolicy.LOCAL_PRIMARY:
        return AnalysisDecision(
            resolved,
            native_available,
            has_analysis_work or not native_available,
            "本机分析为主，设备事件作为补充",
        )
    if native_available and has_native_work and not has_analysis_work:
        return AnalysisDecision(resolved, True, False, "设备已提供所需事件，本机分析待命")
    if native_available and has_native_work and has_analysis_work:
        return AnalysisDecision(resolved, True, True, "部分规则需要本机分析")
    use_local = has_analysis_work or not native_available
    return AnalysisDecision(
        resolved,
        native_available,
        use_local,
        (
            "规则需要本机分析"
            if has_analysis_work
            else "设备缺少所需事件能力，启用本机备用分析"
            if use_local
            else "当前规则由设备事件完成"
        ),
    )


class NativeEventIngestor:
    """Turn checked device events into the existing event/outbox workflow."""

    def __init__(self, store: SQLiteStore, rules: Sequence[Rule], *, channels: Sequence[str] = ()) -> None:
        self.store = store
        self.rules = tuple(rule for rule in rules if rule.trigger_mode is RuleMode.NATIVE_EVENT and rule.enabled)
        self.channels = tuple(dict.fromkeys(str(value) for value in channels if str(value)))

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
