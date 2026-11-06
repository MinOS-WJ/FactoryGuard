"""Export incident records for reporting and handover.

The export is a UTF-8 CSV with a BOM so Chinese text opens correctly in
Excel without any manual import step.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from factoryguard.domain.incidents import (
    SEVERITY_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    Incident,
)

COLUMNS: tuple[str, ...] = (
    "发生时间",
    "险情编号",
    "标题",
    "等级",
    "状态",
    "来源",
    "场所",
    "区域",
    "摄像头",
    "上报人",
    "描述",
    "处置记录",
)


def incident_rows(
    incidents: Iterable[Incident],
    *,
    area_names: dict[str, str] | None = None,
    camera_names: dict[str, str] | None = None,
    site_names: dict[str, str] | None = None,
    audit_trail: dict[str, list[str]] | None = None,
) -> list[list[str]]:
    """Build spreadsheet rows for the given incidents."""
    areas = area_names or {}
    cameras = camera_names or {}
    sites = site_names or {}
    trail = audit_trail or {}
    rows: list[list[str]] = []
    for incident in incidents:
        rows.append(
            [
                incident.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                incident.id,
                incident.title,
                SEVERITY_LABELS.get(incident.severity, str(incident.severity)),
                STATUS_LABELS.get(incident.status, str(incident.status)),
                SOURCE_LABELS.get(incident.source, str(incident.source)),
                sites.get(incident.site_id or "", incident.site_name_snapshot),
                areas.get(incident.area_id or "", incident.area_name_snapshot),
                cameras.get(incident.camera_id or "", incident.camera_name_snapshot),
                incident.reporter,
                incident.description,
                " / ".join(trail.get(incident.id, ())),
            ]
        )
    return rows


def write_incident_csv(
    path: str | Path,
    incidents: Sequence[Incident],
    *,
    area_names: dict[str, str] | None = None,
    camera_names: dict[str, str] | None = None,
    site_names: dict[str, str] | None = None,
    audit_trail: dict[str, list[str]] | None = None,
) -> Path:
    """Write incidents to a UTF-8 CSV file that Excel opens directly."""
    target = Path(path).expanduser()
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    rows = incident_rows(
        incidents,
        area_names=area_names,
        camera_names=camera_names,
        site_names=site_names,
        audit_trail=audit_trail,
    )
    # utf-8-sig keeps Chinese readable in Excel while staying plain CSV.
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return target


__all__ = ["COLUMNS", "incident_rows", "write_incident_csv"]
