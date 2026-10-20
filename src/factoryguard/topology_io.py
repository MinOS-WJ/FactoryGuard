from __future__ import annotations

"""CSV interchange for factory topology and multi-stream camera inventories."""

import csv
from io import StringIO
from typing import Iterable, Mapping

from factoryguard.domain.models import Area, CameraDevice, Site, StreamRole, VideoStream
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
    "camera_id",
    "camera_name",
    "camera_vendor",
    "camera_address",
    "camera_enabled",
    "stream_id",
    "stream_name",
    "stream_url",
    "stream_role",
    "stream_width",
    "stream_height",
    "stream_fps",
    "stream_model",
    "stream_confidence",
    "stream_enabled",
)


def topology_to_csv(topology: EntityRegistry) -> str:
    """Serialize one row per stream, or one row per camera without streams."""
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for site in topology.sites:
        site_areas = [area for area in topology.areas if area.site_id == site.id]
        if not site_areas:
            writer.writerow(_row(site, None, None, None))
        for area in site_areas:
            cameras = [camera for camera in topology.cameras if camera.area_id == area.id]
            if not cameras:
                writer.writerow(_row(site, area, None, None))
            for camera in cameras:
                streams = [stream for stream in topology.streams if stream.camera_id == camera.id]
                for stream in streams or (None,):
                    writer.writerow(_row(site, area, camera, stream))
    return output.getvalue()


def topology_from_csv(text: str) -> EntityRegistry:
    """Parse and validate a CSV inventory into a nested entity registry."""
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("topology CSV is missing a header row")
    missing = [field for field in CSV_FIELDS if field not in reader.fieldnames]
    if missing:
        raise ValueError(f"topology CSV is missing columns: {', '.join(missing)}")
    sites: dict[str, Site] = {}
    areas: dict[str, Area] = {}
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
        camera_id = row.get("camera_id", "")
        if not camera_id:
            continue
        camera = CameraDevice(
            camera_id,
            area.id,
            _required(row, "camera_name", line_no),
            row.get("camera_vendor", ""),
            row.get("camera_address", ""),
            _parse_bool(row.get("camera_enabled", "1"), "camera_enabled", line_no),
        )
        _merge(cameras, camera, "camera", line_no)
        stream_id = row.get("stream_id", "")
        if not stream_id:
            continue
        if not row.get("stream_url"):
            raise ValueError(f"line {line_no}: stream_url is required when stream_id is present")
        try:
            stream = VideoStream(
                id=stream_id,
                camera_id=camera.id,
                name=_required(row, "stream_name", line_no),
                url=row["stream_url"],
                role=StreamRole(row.get("stream_role", StreamRole.MAIN.value)),
                width=int(row.get("stream_width") or 1280),
                height=int(row.get("stream_height") or 720),
                fps=float(row.get("stream_fps") or 2.0),
                model=row.get("stream_model") or None,
                confidence=float(row.get("stream_confidence") or 0.25),
                enabled=_parse_bool(row.get("stream_enabled", "1"), "stream_enabled", line_no),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"line {line_no}: invalid stream: {exc}") from exc
        _merge(streams, stream, "stream", line_no)
    if not sites:
        raise ValueError("topology CSV contains no entities")
    return EntityRegistry(
        sites=sites.values(),
        areas=areas.values(),
        cameras=cameras.values(),
        streams=streams.values(),
    )


def _row(
    site: Site,
    area: Area | None,
    camera: CameraDevice | None,
    stream: VideoStream | None,
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
        "camera_id": camera.id if camera else "",
        "camera_name": camera.name if camera else "",
        "camera_vendor": camera.vendor if camera else "",
        "camera_address": camera.address if camera else "",
        "camera_enabled": int(camera.enabled) if camera else "",
        "stream_id": stream.id if stream else "",
        "stream_name": stream.name if stream else "",
        "stream_url": stream.url if stream else "",
        "stream_role": str(stream.role) if stream else "",
        "stream_width": stream.width if stream else "",
        "stream_height": stream.height if stream else "",
        "stream_fps": stream.fps if stream else "",
        "stream_model": stream.model or "" if stream else "",
        "stream_confidence": stream.confidence if stream else "",
        "stream_enabled": int(stream.enabled) if stream else "",
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
