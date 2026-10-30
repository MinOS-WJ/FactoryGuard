from __future__ import annotations

"""CSV interchange for site, area, physical device, channel and stream inventory."""

import csv
import json
import re
from io import StringIO
from typing import Iterable, Mapping

from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry


CSV_FIELDS = (
    "site_id",
    "site_name",
    "site_description",
    "site_enabled",
    "area_id",
    "area_name",
    "area_parent_id",
    "area_enabled",
    "device_id",
    "device_name",
    "device_protocol",
    "device_endpoint",
    "device_vendor",
    "device_model",
    "device_firmware",
    "device_serial_number",
    "device_device_id",
    "device_analysis_policy",
    "device_capabilities",
    "device_enabled",
    "device_last_probe_at",
    "device_last_ok_at",
    "device_health_status",
    "device_health_detail",
    "device_last_probe_at",
    "device_last_ok_at",
    "device_health_status",
    "device_health_detail",
    "camera_id",
    "camera_name",
    "camera_vendor",
    "camera_address",
    "camera_enabled",
    "camera_device_id",
    "camera_channel_id",
    "stream_id",
    "stream_name",
    "stream_url",
    "stream_role",
    "stream_width",
    "stream_height",
    "stream_fps",
    "stream_model",
    "stream_confidence",
    "stream_input_size",
    "stream_enabled",
    "stream_device_id",
    "stream_analysis_policy",
    "stream_native_event_codes",
)

OPTIONAL_FIELDS = {
    "stream_input_size",
    "device_id",
    "device_name",
    "device_protocol",
    "device_endpoint",
    "device_vendor",
    "device_model",
    "device_firmware",
    "device_serial_number",
    "device_device_id",
    "device_analysis_policy",
    "device_capabilities",
    "device_enabled",
    "camera_device_id",
    "camera_channel_id",
    "stream_device_id",
    "stream_analysis_policy",
    "stream_native_event_codes",
}


def _redact_url(value: str) -> str:
    return re.sub(r"((?:rtsp|rtsps|https?)://)[^/@\s]+@", r"\1***:***@", value, flags=re.IGNORECASE)


def _contains_redacted_credentials(value: str) -> bool:
    return "***:***@" in value


def topology_to_csv(topology: EntityRegistry) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for site in topology.sites:
        site_areas = [area for area in topology.areas if area.site_id == site.id]
        if not site_areas:
            writer.writerow(_row(site, None, None, None, None))
        for area in site_areas:
            devices = [device for device in topology.devices if device.area_id == area.id]
            cameras = [camera for camera in topology.cameras if camera.area_id == area.id]
            for device in devices:
                device_cameras = [camera for camera in cameras if camera.device_id == device.id]
                if not device_cameras:
                    writer.writerow(_row(site, area, None, None, device))
                for camera in device_cameras:
                    streams = [stream for stream in topology.streams if stream.camera_id == camera.id]
                    for stream in streams or (None,):
                        writer.writerow(_row(site, area, camera, stream, device))
            for camera in cameras:
                if camera.device_id is None:
                    streams = [stream for stream in topology.streams if stream.camera_id == camera.id]
                    for stream in streams or (None,):
                        writer.writerow(_row(site, area, camera, stream, None))
            if not devices and not cameras:
                writer.writerow(_row(site, area, None, None, None))
    return output.getvalue()


def topology_from_csv(text: str) -> EntityRegistry:
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("topology CSV is missing a header row")
    missing = [field for field in CSV_FIELDS if field not in reader.fieldnames and field not in OPTIONAL_FIELDS]
    if missing:
        raise ValueError(f"topology CSV is missing columns: {', '.join(missing)}")
    sites: dict[str, Site] = {}
    areas: dict[str, Area] = {}
    devices: dict[str, VideoDevice] = {}
    cameras: dict[str, CameraDevice] = {}
    streams: dict[str, VideoStream] = {}
    for line_no, raw in enumerate(reader, start=2):
        row = {key: (value or "").strip() for key, value in raw.items() if key is not None}
        site = Site(
            _required(row, "site_id", line_no),
            _required(row, "site_name", line_no),
            row.get("site_description", ""),
            _parse_bool(row.get("site_enabled", "1"), "site_enabled", line_no),
        )
        _merge(sites, site, "site", line_no)
        area_id = row.get("area_id", "")
        if not area_id:
            continue
        area = Area(
            area_id,
            site.id,
            _required(row, "area_name", line_no),
            row.get("area_parent_id") or None,
            enabled=_parse_bool(row.get("area_enabled", "1"), "area_enabled", line_no),
        )
        _merge(areas, area, "area", line_no)
        device_id = row.get("device_id", "")
        if device_id:
            try:
                device = VideoDevice(
                    id=device_id,
                    site_id=site.id,
                    area_id=area.id,
                    name=_required(row, "device_name", line_no),
                    protocol=DeviceProtocol(row.get("device_protocol", DeviceProtocol.RTSP.value)),
                    endpoint=_required(row, "device_endpoint", line_no),
                    vendor=row.get("device_vendor", ""),
                    model=row.get("device_model", ""),
                    firmware=row.get("device_firmware", ""),
                    serial_number=row.get("device_serial_number", ""),
                    device_id=row.get("device_device_id", ""),
                    capabilities=DeviceCapabilities.from_dict(json.loads(row.get("device_capabilities") or "{}")),
                    analysis_policy=AnalysisPolicy(row.get("device_analysis_policy", AnalysisPolicy.LOCAL_FALLBACK.value)),
                    enabled=_parse_bool(row.get("device_enabled", "1"), "device_enabled", line_no),
                    last_probe_at=row.get("device_last_probe_at") or None,
                    last_ok_at=row.get("device_last_ok_at") or None,
                    health_status=row.get("device_health_status") or "unknown",
                    health_detail=row.get("device_health_detail") or "",
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"line {line_no}: invalid device: {exc}") from exc
            _merge(devices, device, "device", line_no)
        camera_id = row.get("camera_id", "")
        if not camera_id:
            continue
        camera_device_id = row.get("camera_device_id", "") or device_id or None
        if camera_device_id and camera_device_id not in devices:
            raise ValueError(f"line {line_no}: camera references unknown device {camera_device_id}")
        camera = CameraDevice(
            camera_id,
            area.id,
            _required(row, "camera_name", line_no),
            row.get("camera_vendor", ""),
            row.get("camera_address", ""),
            _parse_bool(row.get("camera_enabled", "1"), "camera_enabled", line_no),
            device_id=camera_device_id,
            channel_id=row.get("camera_channel_id", ""),
        )
        _merge(cameras, camera, "camera", line_no)
        stream_id = row.get("stream_id", "")
        if not stream_id:
            continue
        if not row.get("stream_url"):
            raise ValueError(f"line {line_no}: stream_url is required when stream_id is present")
        try:
            stream_url = _required(row, "stream_url", line_no)
            if _contains_redacted_credentials(stream_url):
                raise ValueError(f"line {line_no}: stream_url contains redacted credentials; replace them before importing")
            policy_value = row.get("stream_analysis_policy", "")
            stream = VideoStream(
                id=stream_id,
                camera_id=camera.id,
                name=_required(row, "stream_name", line_no),
                url=stream_url,
                role=StreamRole(row.get("stream_role", StreamRole.MAIN.value)),
                width=int(row.get("stream_width") or 1280),
                height=int(row.get("stream_height") or 720),
                fps=float(row.get("stream_fps") or 2.0),
                model=row.get("stream_model") or None,
                confidence=float(row.get("stream_confidence") or 0.25),
                input_size=int(row.get("stream_input_size") or 640),
                enabled=_parse_bool(row.get("stream_enabled", "1"), "stream_enabled", line_no),
                device_id=row.get("stream_device_id", "") or camera_device_id,
                analysis_policy=AnalysisPolicy(policy_value) if policy_value else None,
                native_event_codes=tuple(
                    value.strip()
                    for value in row.get("stream_native_event_codes", "").split(",")
                    if value.strip()
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"line {line_no}: invalid stream: {exc}") from exc
        _merge(streams, stream, "stream", line_no)
    if not sites:
        raise ValueError("topology CSV contains no entities")
    return EntityRegistry(
        sites=sites.values(),
        areas=areas.values(),
        devices=devices.values(),
        cameras=cameras.values(),
        streams=streams.values(),
    )


def _row(
    site: Site,
    area: Area | None,
    camera: CameraDevice | None,
    stream: VideoStream | None,
    device: VideoDevice | None,
) -> dict[str, object]:
    return {
        "site_id": site.id,
        "site_name": site.name,
        "site_description": site.description,
        "site_enabled": int(site.enabled),
        "area_id": area.id if area else "",
        "area_name": area.name if area else "",
        "area_parent_id": area.parent_id if area and area.parent_id else "",
        "area_enabled": int(area.enabled) if area else "",
        "device_id": device.id if device else "",
        "device_name": device.name if device else "",
        "device_protocol": str(device.protocol) if device else "",
        "device_endpoint": _redact_url(device.endpoint) if device else "",
        "device_vendor": device.vendor if device else "",
        "device_model": device.model if device else "",
        "device_firmware": device.firmware if device else "",
        "device_serial_number": device.serial_number if device else "",
        "device_device_id": device.device_id if device else "",
        "device_analysis_policy": str(device.analysis_policy) if device else "",
        "device_capabilities": json.dumps(device.capabilities.as_dict(), separators=(",", ":")) if device else "",
        "device_enabled": int(device.enabled) if device else "",
        "device_last_probe_at": device.last_probe_at or "" if device else "",
        "device_last_ok_at": device.last_ok_at or "" if device else "",
        "device_health_status": device.health_status if device else "",
        "device_health_detail": device.health_detail if device else "",
        "camera_id": camera.id if camera else "",
        "camera_name": camera.name if camera else "",
        "camera_vendor": camera.vendor if camera else "",
        "camera_address": camera.address if camera else "",
        "camera_enabled": int(camera.enabled) if camera else "",
        "camera_device_id": camera.device_id or "" if camera else "",
        "camera_channel_id": camera.channel_id if camera else "",
        "stream_id": stream.id if stream else "",
        "stream_name": stream.name if stream else "",
        "stream_url": _redact_url(stream.url) if stream else "",
        "stream_role": str(stream.role) if stream else "",
        "stream_width": stream.width if stream else "",
        "stream_height": stream.height if stream else "",
        "stream_fps": stream.fps if stream else "",
        "stream_model": stream.model or "" if stream else "",
        "stream_confidence": stream.confidence if stream else "",
        "stream_input_size": stream.input_size if stream else "",
        "stream_enabled": int(stream.enabled) if stream else "",
        "stream_device_id": stream.device_id or "" if stream else "",
        "stream_analysis_policy": str(stream.analysis_policy) if stream and stream.analysis_policy else "",
        "stream_native_event_codes": ",".join(stream.native_event_codes) if stream else "",
    }


def _required(row: Mapping[str, str], field: str, line_no: int) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise ValueError(f"line {line_no}: {field} is required")
    return value


def _parse_bool(value: str, field: str, line_no: int) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "启用"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "停用"}:
        return False
    raise ValueError(f"line {line_no}: {field} must be 0/1 or true/false")


def _merge(bucket: dict[str, object], value: object, kind: str, line_no: int) -> None:
    identifier = str(getattr(value, "id"))
    existing = bucket.get(identifier)
    if existing is not None and existing != value:
        raise ValueError(f"line {line_no}: conflicting {kind} definition for {identifier}")
    bucket[identifier] = value


__all__ = ["CSV_FIELDS", "topology_from_csv", "topology_to_csv"]
