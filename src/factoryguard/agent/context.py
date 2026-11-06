"""Deterministic, privacy-aware context used by the assistant and dashboard.

The language model never receives raw credentials or unrestricted database
rows.  It receives a compact Chinese snapshot assembled from the same local
entities that power the console.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from factoryguard.domain.incidents import SEVERITY_LABELS, STATUS_LABELS, Incident
from factoryguard.domain.models import Area, CameraDevice, Site, VideoDevice, VideoStream
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.sqlite import SQLiteStore

ONLINE_HEALTH_STATES = {"online", "running", "healthy", "ok", "ready", "connected"}
OFFLINE_HEALTH_STATES = {"offline", "failed", "error", "stopped", "disconnected", "degraded"}
HEALTH_STALE_AFTER = timedelta(minutes=30)
HEALTH_FUTURE_TOLERANCE = timedelta(minutes=5)


def _parse_health_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        timestamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _validated_health_snapshot(
    state: str,
    detail: object,
    checked_at: object,
    *,
    now: datetime,
) -> tuple[str, str, str]:
    """Return a health snapshot only when it has a credible, recent timestamp."""
    raw_checked_at = str(checked_at or "").strip()
    timestamp = _parse_health_timestamp(raw_checked_at)
    if timestamp is None:
        return "unknown", "尚未完成设备状态检测", ""
    if timestamp > now + HEALTH_FUTURE_TOLERANCE:
        return "unknown", "设备检测时间异常，等待重新检测", raw_checked_at
    if now - timestamp > HEALTH_STALE_AFTER:
        return "unknown", "上次设备检测已过期，等待重新检测", raw_checked_at
    return state, str(detail or ""), raw_checked_at


PROTOCOL_LABELS = {
    "onvif": "ONVIF",
    "gb28181": "GB/T 28181",
    "rtsp": "RTSP",
    "manual": "手动接入",
}


def _select_best_stream(streams: tuple[VideoStream, ...]) -> VideoStream | None:
    enabled = [stream for stream in streams if stream.enabled]
    if not enabled:
        return None
    order = {"sub": 0, "main": 1, "analysis": 2}
    return sorted(enabled, key=lambda stream: order.get(str(stream.role), 9))[0]


@dataclass(frozen=True, slots=True)
class CameraView:
    camera: CameraDevice
    site: Site
    area: Area
    device: VideoDevice | None
    streams: tuple[VideoStream, ...]
    health_status: str = "unknown"
    health_detail: str = ""
    health_updated_at: str = ""
    subscription_state: str = "unknown"
    subscription_detail: str = ""

    @property
    def protocol(self) -> str:
        return str(self.device.protocol) if self.device is not None else "rtsp"

    @property
    def protocol_label(self) -> str:
        return PROTOCOL_LABELS.get(self.protocol, self.protocol.upper())

    @property
    def is_disabled(self) -> bool:
        return not self.camera.enabled or (self.device is not None and not self.device.enabled)

    @property
    def is_online(self) -> bool:
        return not self.is_disabled and self.health_status in ONLINE_HEALTH_STATES

    @property
    def is_offline(self) -> bool:
        return not self.is_disabled and self.health_status in OFFLINE_HEALTH_STATES

    @property
    def best_stream(self) -> VideoStream | None:
        if self.is_disabled:
            return None
        return _select_best_stream(self.streams)

    @property
    def status_label(self) -> str:
        if self.is_disabled:
            return "已停用"
        if self.is_online:
            return "在线"
        if self.is_offline:
            return "离线"
        return "待检测"

    @property
    def subscription_label(self) -> str:
        """Describe the alarm subscription in plain language.

        A camera can be perfectly online while its alarm subscription has
        died, so the merchant needs to see that state on the card itself.
        """
        if self.is_disabled:
            return ""
        state = str(self.subscription_state or "").strip().lower()
        if not state or state == "unknown":
            return ""
        if state in {"healthy", "online", "ok", "active"}:
            return ""
        detail = " ".join(str(self.subscription_detail or "").split())[:60]
        label = "告警订阅异常" if state in {"degraded", "failed", "error"} else "告警订阅状态异常"
        return f"{label}：{detail}" if detail else label

    @property
    def health_checked_text(self) -> str:
        if self.is_disabled:
            return "状态检测已暂停"
        if not self.health_updated_at:
            return "尚未检测"
        try:
            timestamp = datetime.fromisoformat(self.health_updated_at)
        except ValueError:
            return "检测时间未知"
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return f"检测于 {timestamp.astimezone().strftime('%m-%d %H:%M')}"


@dataclass(frozen=True, slots=True)
class OperationalContext:
    generated_at: datetime
    topology: EntityRegistry
    cameras: tuple[CameraView, ...]
    incidents: tuple[Incident, ...]

    @property
    def active_cameras(self) -> tuple[CameraView, ...]:
        return tuple(camera for camera in self.cameras if not camera.is_disabled)

    @property
    def disabled_cameras(self) -> tuple[CameraView, ...]:
        return tuple(camera for camera in self.cameras if camera.is_disabled)

    @property
    def online_cameras(self) -> tuple[CameraView, ...]:
        return tuple(camera for camera in self.cameras if camera.is_online)

    @property
    def offline_cameras(self) -> tuple[CameraView, ...]:
        return tuple(camera for camera in self.cameras if camera.is_offline)

    @property
    def unknown_cameras(self) -> tuple[CameraView, ...]:
        return tuple(
            camera
            for camera in self.cameras
            if not camera.is_disabled and not camera.is_online and not camera.is_offline
        )

    @property
    def open_incidents(self) -> tuple[Incident, ...]:
        return tuple(
            incident
            for incident in self.incidents
            if str(incident.status) in {"open", "acknowledged"}
        )

    @property
    def high_incidents(self) -> tuple[Incident, ...]:
        return tuple(
            incident
            for incident in self.open_incidents
            if str(incident.severity) in {"high", "critical"}
        )

    @property
    def area_count(self) -> int:
        return len(self.topology.areas)

    def area_name(self, area_id: str | None, *, snapshot: str = "") -> str:
        """Return a human area label that stays unique across multiple sites.

        ``snapshot`` carries the name recorded when the incident was created,
        so history stays readable after the area is renamed or removed.
        """
        if not area_id:
            return "未指定区域"
        try:
            area = self.topology.get_area(area_id)
        except KeyError:
            return snapshot or "区域已移除"
        if snapshot:
            # History records where the incident happened at that time, so the
            # captured name stays authoritative even after a rename.
            return snapshot
        if not self._area_name_is_ambiguous(area.site_id, area.name):
            return area.name
        try:
            site_name = self.topology.get_site(area.site_id).name
        except KeyError:
            return area.name
        return f"{site_name} · {area.name}"

    def camera_name(self, camera_id: str | None, *, snapshot: str = "") -> str:
        """Return a human camera label that stays unique across multiple sites."""
        if not camera_id:
            return "未指定摄像头"
        try:
            camera = self.topology.get_camera(camera_id)
        except KeyError:
            return snapshot or "摄像头已移除"
        if snapshot:
            return snapshot
        area = next((item for item in self.topology.areas if item.id == camera.area_id), None)
        if area is None or not self._area_name_is_ambiguous(area.site_id, area.name):
            return camera.name
        try:
            site_name = self.topology.get_site(area.site_id).name
        except KeyError:
            return camera.name
        return f"{site_name} · {camera.name}"

    def _area_name_is_ambiguous(self, site_id: str, name: str) -> bool:
        """Report whether the same area name exists under more than one site."""
        target = name.strip().casefold()
        if not target:
            return False
        return (
            sum(
                1
                for item in self.topology.areas
                if item.name.strip().casefold() == target and item.site_id != site_id
            )
            > 0
        )

    def to_prompt(self) -> str:
        lines = [
            "【FactoryGuard 实时运营快照】",
            f"生成时间：{self.generated_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
            f"场区：{len(self.topology.sites)} 个；区域：{len(self.topology.areas)} 个；摄像头：{len(self.cameras)} 台。",
            f"摄像头状态：在线 {len(self.online_cameras)}，离线 {len(self.offline_cameras)}，"
            f"待检测 {len(self.unknown_cameras)}，已停用 {len(self.disabled_cameras)}。",
            f"未关闭险情：{len(self.open_incidents)} 条，其中高危及紧急 {len(self.high_incidents)} 条。",
            "",
            "【区域与摄像头】",
        ]
        if not self.cameras:
            lines.append("尚未接入摄像头。")
        for camera in self.cameras:
            detail = f"- {camera.site.name} / {camera.area.name} / {camera.camera.name}：{camera.protocol_label}，{camera.status_label}"
            detail += f"，{camera.health_checked_text}"
            if camera.health_detail:
                detail += f"，{camera.health_detail}"
            if camera.subscription_detail:
                detail += f"，告警订阅：{camera.subscription_detail}"
            lines.append(detail)
        lines.extend(("", "【最近险情】"))
        if not self.incidents:
            lines.append("暂无险情记录。")
        for incident in self.incidents[:20]:
            severity = SEVERITY_LABELS.get(incident.severity, str(incident.severity))
            status = STATUS_LABELS.get(incident.status, str(incident.status))
            location = " / ".join(
                value
                for value in (
                    self.area_name(incident.area_id, snapshot=incident.area_name_snapshot),
                    self.camera_name(incident.camera_id, snapshot=incident.camera_name_snapshot),
                )
                if value
            )
            lines.append(
                f"- {incident.occurred_at.astimezone().strftime('%m-%d %H:%M')} [{severity}/{status}] "
                f"{incident.title}（{location}）{incident.description}".strip()
            )
        lines.extend(
            (
                "",
                "回答规则：只能基于以上快照陈述事实；无法确认时明确说“平台数据不足”，不要猜测。",
            )
        )
        return "\n".join(lines)

    def dashboard_summary(self) -> str:
        if not self.cameras:
            return "还没有接入摄像头。先添加区域和摄像头，平台就能开始汇总现场情况。"
        if not self.active_cameras:
            return "所有摄像头均已暂停接入。需要恢复值守时，请在“区域与设备”中恢复摄像头。"
        if self.high_incidents:
            first = self.high_incidents[0]
            return (
                f"当前有 {len(self.high_incidents)} 条高优先级险情需要关注，"
                f"最近一条位于{self.area_name(first.area_id, snapshot=first.area_name_snapshot)}："
                f"{first.title}。"
            )
        if self.offline_cameras:
            names = "、".join(
                f"{camera.site.name}的{camera.camera.name}" for camera in self.offline_cameras[:3]
            )
            suffix = "等" if len(self.offline_cameras) > 3 else ""
            return (
                f"{names}{suffix} {len(self.offline_cameras)} 台摄像头离线，建议先检查网络或供电。"
            )
        if self.unknown_cameras:
            return (
                f"{len(self.unknown_cameras)} 台摄像头尚未完成状态检测。"
                "请在“实时画面”中刷新设备状态，确认在线情况后再判断现场是否平稳。"
            )
        if self.open_incidents:
            return f"目前有 {len(self.open_incidents)} 条险情待处理，暂无紧急升级。"
        return "当前没有高优先级险情，设备与区域运行平稳。可以随时问我现场情况。"


def build_operational_context(
    store: SQLiteStore,
    *,
    now: datetime | None = None,
) -> OperationalContext:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    topology = store.load_topology()
    health = store.list_stream_health()
    subscriptions = store.list_device_subscriptions()
    cameras: list[CameraView] = []
    for camera in topology.cameras:
        try:
            area = topology.get_area(camera.area_id)
            site = topology.get_site(area.site_id)
        except KeyError:
            continue
        device = None
        if camera.device_id:
            try:
                device = topology.get_device(camera.device_id)
            except KeyError:
                device = None
        streams = tuple(stream for stream in topology.streams if stream.camera_id == camera.id)
        best_stream = _select_best_stream(streams)
        state = "unknown"
        detail = ""
        checked_at = ""
        for stream in (best_stream,) if best_stream is not None else ():
            stream_health = health.get(stream.id)
            if not stream_health:
                continue
            candidate = str(stream_health.get("status", "unknown")).strip().lower()
            candidate_state, candidate_detail, candidate_checked_at = _validated_health_snapshot(
                candidate,
                stream_health.get("detail", ""),
                stream_health.get("updated_at", ""),
                now=timestamp,
            )
            if candidate_state in ONLINE_HEALTH_STATES:
                state = candidate_state
                detail = candidate_detail
                checked_at = candidate_checked_at
                break
            if state == "unknown" and candidate_state != "unknown":
                state = candidate_state
                detail = candidate_detail
                checked_at = candidate_checked_at
            elif state == "unknown" and candidate_detail:
                detail = candidate_detail
                checked_at = candidate_checked_at
        if device is not None and state == "unknown":
            device_state = str(device.health_status).strip().lower()
            device_state, device_detail, device_checked_at = _validated_health_snapshot(
                device_state,
                device.health_detail,
                device.last_probe_at,
                now=timestamp,
            )
            if device_state in ONLINE_HEALTH_STATES | OFFLINE_HEALTH_STATES:
                state = device_state
                detail = device_detail
                checked_at = device_checked_at
        subscription = subscriptions.get(device.id, {}) if device is not None else {}
        cameras.append(
            CameraView(
                camera=camera,
                site=site,
                area=area,
                device=device,
                streams=streams,
                health_status=state,
                health_detail=detail,
                health_updated_at=checked_at,
                subscription_state=str(subscription.get("state", "unknown")),
                subscription_detail=str(subscription.get("detail", "")),
            )
        )
    incidents = tuple(store.list_operational_incidents(recent_limit=100))
    return OperationalContext(
        generated_at=timestamp,
        topology=topology,
        cameras=tuple(cameras),
        incidents=incidents,
    )


__all__ = [
    "HEALTH_STALE_AFTER",
    "OFFLINE_HEALTH_STATES",
    "ONLINE_HEALTH_STATES",
    "PROTOCOL_LABELS",
    "CameraView",
    "OperationalContext",
    "build_operational_context",
]
