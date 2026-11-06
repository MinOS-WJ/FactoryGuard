from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

from factoryguard.devices.base import Recording
from factoryguard.domain.incidents import (
    STATUS_LABELS,
    Incident,
    IncidentSeverity,
    IncidentSource,
    IncidentStatus,
)
from factoryguard.domain.models import (
    Area,
    CameraDevice,
    DeviceCapabilities,
    Event,
    EventStatus,
    Rule,
    Site,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.schedules import ArmingSchedule, TimeWindow
from factoryguard.domain.topology import EntityRegistry

ALLOWED_INCIDENT_STATUS_TRANSITIONS = {
    IncidentStatus.OPEN: {
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.RESOLVED,
        IncidentStatus.FALSE_ALARM,
    },
    IncidentStatus.ACKNOWLEDGED: {
        IncidentStatus.OPEN,
        IncidentStatus.RESOLVED,
        IncidentStatus.FALSE_ALARM,
    },
    IncidentStatus.RESOLVED: {IncidentStatus.OPEN},
    IncidentStatus.FALSE_ALARM: {IncidentStatus.OPEN},
}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS site (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS area (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL REFERENCES site(id),
  parent_id TEXT REFERENCES area(id),
  name TEXT NOT NULL,
  geometry TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_area_site_parent ON area(site_id, parent_id);
CREATE TABLE IF NOT EXISTS video_device (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL REFERENCES site(id),
  area_id TEXT NOT NULL REFERENCES area(id),
  name TEXT NOT NULL,
  protocol TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  firmware TEXT NOT NULL DEFAULT '',
  serial_number TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  credential_ref TEXT,
  capabilities TEXT NOT NULL DEFAULT '{}',
  analysis_policy TEXT NOT NULL DEFAULT 'native_only',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_probe_at TEXT,
  last_ok_at TEXT,
  health_status TEXT NOT NULL DEFAULT 'unknown',
  health_detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_device_area ON video_device(area_id, protocol);
CREATE TABLE IF NOT EXISTS device_credential (
  credential_ref TEXT PRIMARY KEY,
  ciphertext TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS camera_device (
  id TEXT PRIMARY KEY,
  area_id TEXT NOT NULL REFERENCES area(id),
  name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  device_id TEXT REFERENCES video_device(id),
  channel_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_camera_area ON camera_device(area_id);
CREATE TABLE IF NOT EXISTS video_stream (
  id TEXT PRIMARY KEY,
  camera_id TEXT NOT NULL REFERENCES camera_device(id),
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'main',
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  fps REAL NOT NULL,
  model TEXT,
  confidence REAL NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  input_size INTEGER NOT NULL DEFAULT 640,
  device_id TEXT REFERENCES video_device(id),
  analysis_policy TEXT,
  native_event_codes TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_stream_camera ON video_stream(camera_id);
-- Legacy-compatible storage for native event routing rules.
CREATE TABLE IF NOT EXISTS detection_rule (
  id TEXT PRIMARY KEY,
  stream_id TEXT NOT NULL,
  site_id TEXT,
  area_id TEXT,
  camera_id TEXT,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  geometry TEXT NOT NULL,
  target_labels TEXT NOT NULL,
  min_confidence REAL NOT NULL,
  min_box_height REAL NOT NULL,
  confirm_frames INTEGER NOT NULL,
  cooldown_seconds REAL NOT NULL,
  direction TEXT NOT NULL,
  condition TEXT NOT NULL DEFAULT '',
  count_threshold INTEGER NOT NULL DEFAULT 0,
  window_seconds REAL NOT NULL DEFAULT 0,
  dwell_seconds REAL NOT NULL DEFAULT 0,
  actions TEXT NOT NULL DEFAULT '',
  http_action_url TEXT NOT NULL DEFAULT '',
  http_action_timeout REAL NOT NULL DEFAULT 5.0,
  ack_deadline_seconds REAL NOT NULL DEFAULT 0,
  escalation_level TEXT NOT NULL DEFAULT '',
  destination_ids TEXT NOT NULL DEFAULT '[]',
  assignees TEXT NOT NULL DEFAULT '[]',
  condition_nodes TEXT NOT NULL DEFAULT '[]',
  trigger_mode TEXT NOT NULL DEFAULT 'analysis',
  native_event_codes TEXT NOT NULL DEFAULT '[]',
  dedup_window_seconds REAL NOT NULL DEFAULT 5.0,
  enabled INTEGER NOT NULL DEFAULT 1,
  publication_status TEXT NOT NULL DEFAULT 'published',
  version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_rule_stream ON detection_rule(stream_id, enabled);
CREATE TABLE IF NOT EXISTS rule_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  definition TEXT NOT NULL,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(rule_id, version)
);
CREATE TABLE IF NOT EXISTS rule_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  actor TEXT NOT NULL,
  decision TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL,
  UNIQUE(rule_id, version, actor, decision)
);
CREATE INDEX IF NOT EXISTS ix_rule_review_rule_version ON rule_review(rule_id, version);
CREATE TABLE IF NOT EXISTS stream_arming (
  stream_id TEXT PRIMARY KEY,
  armed INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system'
);
CREATE TABLE IF NOT EXISTS arming_schedule (
  stream_id TEXT PRIMARY KEY,
  timezone TEXT NOT NULL DEFAULT 'local',
  weekly TEXT NOT NULL,
  holidays TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  default_armed INTEGER NOT NULL DEFAULT 1,
  override_armed INTEGER,
  override_until TEXT
);
CREATE TABLE IF NOT EXISTS event (
  id TEXT PRIMARY KEY,
  dedup_key TEXT NOT NULL UNIQUE,
  rule_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  stream_id TEXT,
  site_id TEXT,
  area_id TEXT,
  camera_id TEXT,
  level TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  nvr_time TEXT,
  clock_delta_ms INTEGER,
  snapshot_uri TEXT,
  clip_uri TEXT,
  clip_status TEXT NOT NULL DEFAULT 'pending',
  status TEXT NOT NULL DEFAULT 'open',
  escalated_at TEXT,
  payload TEXT NOT NULL,
  device_id TEXT,
  source TEXT NOT NULL DEFAULT 'system',
  source_event_id TEXT,
  native INTEGER NOT NULL DEFAULT 0,
  analysis_policy TEXT NOT NULL DEFAULT 'native_only',
  correlation_key TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS device_subscription (
  device_id TEXT PRIMARY KEY REFERENCES video_device(id),
  source TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  last_event_at TEXT
);
CREATE TABLE IF NOT EXISTS recording_index (
  id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES video_device(id),
  channel_id TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  source TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_recording_device_time ON recording_index(device_id, channel_id, start_at);
CREATE TABLE IF NOT EXISTS notification_outbox (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES event(id),
  channel TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  sent_at TEXT,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  resource TEXT NOT NULL,
  result TEXT NOT NULL,
  ts TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_event_time ON event(occurred_at);
CREATE INDEX IF NOT EXISTS ix_outbox_retry ON notification_outbox(status, next_retry_at);
CREATE TABLE IF NOT EXISTS stream_health (
  stream_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  last_ok_at TEXT,
  reconnect_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_stream_health_status ON stream_health(status, updated_at);
CREATE TABLE IF NOT EXISTS incident (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  severity TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'open',
  site_id TEXT,
  area_id TEXT,
  camera_id TEXT,
  site_name_snapshot TEXT NOT NULL DEFAULT '',
  area_name_snapshot TEXT NOT NULL DEFAULT '',
  camera_name_snapshot TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'user',
  reporter TEXT NOT NULL DEFAULT '',
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ai_summary TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_incident_status_time ON incident(status, occurred_at);
CREATE INDEX IF NOT EXISTS ix_incident_area_time ON incident(area_id, occurred_at);
CREATE TABLE IF NOT EXISTS incident_notification_delivery (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT NOT NULL REFERENCES incident(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 1,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(incident_id, channel)
);
CREATE INDEX IF NOT EXISTS ix_incident_notification_status
  ON incident_notification_delivery(status, updated_at);
CREATE TABLE IF NOT EXISTS assistant_conversation (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '现场助手',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assistant_message (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES assistant_conversation(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'offline',
  citations TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assistant_message_conversation ON assistant_message(conversation_id, created_at);
"""


class SQLiteStore:
    """Small transaction boundary for Event + Outbox + AuditLog writes."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            database_path = Path(self.path)
            database_path.parent.mkdir(parents=True, exist_ok=True)
        # Monitoring, UI and notification workers may write at the same time.
        # Keep a generous busy timeout so short WAL writer contention is
        # handled by SQLite instead of surfacing as ``database is locked``.
        self._connection: sqlite3.Connection | None = sqlite3.connect(self.path, timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.executescript(SCHEMA)
        # Event writes are low volume and durability is more important than
        # the small throughput gain from NORMAL synchronous mode.
        self._connection.execute("PRAGMA synchronous=FULL")
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add device/native-event columns and migrate legacy RTSP cameras."""
        connection = self._connection_or_raise()

        def table_columns(table: str) -> set[str]:
            return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

        def add_columns(table: str, additions: Mapping[str, str]) -> None:
            existing = table_columns(table)
            for name, definition in additions.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        with connection:
            add_columns(
                "incident",
                {
                    "site_name_snapshot": "TEXT NOT NULL DEFAULT ''",
                    "area_name_snapshot": "TEXT NOT NULL DEFAULT ''",
                    "camera_name_snapshot": "TEXT NOT NULL DEFAULT ''",
                },
            )
            add_columns(
                "event",
                {
                    "stream_id": "TEXT",
                    "site_id": "TEXT",
                    "area_id": "TEXT",
                    "camera_id": "TEXT",
                    "device_id": "TEXT",
                    "source": "TEXT NOT NULL DEFAULT 'system'",
                    "source_event_id": "TEXT",
                    "native": "INTEGER NOT NULL DEFAULT 0",
                    "analysis_policy": "TEXT NOT NULL DEFAULT 'native_only'",
                    "correlation_key": "TEXT NOT NULL DEFAULT ''",
                },
            )
            add_columns("video_stream", {"input_size": "INTEGER NOT NULL DEFAULT 640"})
            add_columns(
                "video_device",
                {
                    "last_probe_at": "TEXT",
                    "last_ok_at": "TEXT",
                    "health_status": "TEXT NOT NULL DEFAULT 'unknown'",
                    "health_detail": "TEXT NOT NULL DEFAULT ''",
                },
            )
            add_columns(
                "camera_device",
                {"device_id": "TEXT", "channel_id": "TEXT NOT NULL DEFAULT ''"},
            )
            add_columns(
                "detection_rule",
                {
                    "condition": "TEXT NOT NULL DEFAULT ''",
                    "count_threshold": "INTEGER NOT NULL DEFAULT 0",
                    "window_seconds": "REAL NOT NULL DEFAULT 0",
                    "dwell_seconds": "REAL NOT NULL DEFAULT 0",
                    "actions": "TEXT NOT NULL DEFAULT ''",
                    "http_action_url": "TEXT NOT NULL DEFAULT ''",
                    "http_action_timeout": "REAL NOT NULL DEFAULT 5.0",
                    "ack_deadline_seconds": "REAL NOT NULL DEFAULT 0",
                    "escalation_level": "TEXT NOT NULL DEFAULT ''",
                    "destination_ids": "TEXT NOT NULL DEFAULT '[]'",
                    "assignees": "TEXT NOT NULL DEFAULT '[]'",
                    "condition_nodes": "TEXT NOT NULL DEFAULT '[]'",
                    "publication_status": "TEXT NOT NULL DEFAULT 'published'",
                    "trigger_mode": "TEXT NOT NULL DEFAULT 'analysis'",
                    "native_event_codes": "TEXT NOT NULL DEFAULT '[]'",
                    "dedup_window_seconds": "REAL NOT NULL DEFAULT 5.0",
                },
            )
            stream_columns = table_columns("video_stream")
            for name, definition in {
                "device_id": "TEXT",
                "analysis_policy": "TEXT",
                "native_event_codes": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if name not in stream_columns:
                    connection.execute(f"ALTER TABLE video_stream ADD COLUMN {name} {definition}")

            legacy_rows = connection.execute(
                """SELECT camera.id, camera.area_id, camera.name, camera.vendor, camera.address,
                          area.site_id, stream.url
                   FROM camera_device camera
                   JOIN area ON area.id = camera.area_id
                   LEFT JOIN video_stream stream ON stream.camera_id = camera.id
                   WHERE camera.device_id IS NULL
                   GROUP BY camera.id"""
            ).fetchall()
            for row in legacy_rows:
                device_id = f"device-{row['id']}"
                raw_endpoint = _endpoint_without_credentials(
                    str(row["url"] or row["address"] or "")
                )
                protocol = "rtsp" if raw_endpoint else "manual"
                endpoint = raw_endpoint or f"manual://{row['id']}"
                connection.execute(
                    """INSERT OR IGNORE INTO video_device
                       (id, site_id, area_id, name, protocol, endpoint, vendor, model, firmware,
                        serial_number, device_id, credential_ref, capabilities, analysis_policy, enabled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, NULL, ?, 'native_only', 1)""",
                    (
                        device_id,
                        row["site_id"],
                        row["area_id"],
                        row["name"],
                        protocol,
                        endpoint,
                        row["vendor"] or "",
                        "",
                        row["id"],
                        json.dumps(
                            DeviceCapabilities(live_video=True, profiles=("RTSP/RTP",)).as_dict()
                        ),
                    ),
                )
                connection.execute(
                    "UPDATE camera_device SET device_id = ?, channel_id = COALESCE(NULLIF(channel_id, ''), id) WHERE id = ?",
                    (device_id, row["id"]),
                )
            connection.execute(
                "UPDATE video_stream SET device_id = COALESCE(device_id, (SELECT device_id FROM camera_device WHERE camera_device.id = video_stream.camera_id))"
            )
            connection.execute("DROP VIEW IF EXISTS camera")
            connection.execute(
                "CREATE VIEW camera AS SELECT id, area_id, name, vendor, address, enabled, device_id, channel_id FROM camera_device"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_camera_device ON camera_device(device_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_stream_device ON video_stream(device_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_event_stream_time ON event(stream_id, occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_event_entity_time ON event(site_id, area_id, camera_id, occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_event_correlation ON event(correlation_key, occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_event_source_event ON event(source, source_event_id)"
            )

    def referenced_credential_refs(self) -> tuple[str, ...]:
        """Return credential references still used by a physical device."""
        connection = self._connection_or_raise()
        rows = connection.execute(
            "SELECT DISTINCT credential_ref FROM video_device"
            " WHERE credential_ref IS NOT NULL AND credential_ref != ''"
        )
        return tuple(sorted({str(row[0]) for row in rows if row[0]}))

    def prune_history(
        self,
        *,
        retention_days: float,
        now: datetime | None = None,
        actor: str = "retention",
    ) -> dict[str, int]:
        """Remove expired device events, audit rows and delivered outbox entries.

        Incidents and their notification history are kept because operators
        rely on them for reporting.  Only the raw device event stream and the
        audit trail are trimmed so a long-running installation does not grow
        without bound.
        """
        if not isfinite(retention_days) or retention_days < 0:
            raise ValueError("retention_days must be finite and non-negative")
        if not actor.strip():
            raise ValueError("retention actor is required")
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            raise ValueError("retention reference time must be timezone-aware")
        cutoff = (reference - timedelta(days=retention_days)).isoformat()
        connection = self._connection_or_raise()
        with connection:
            outbox = connection.execute(
                "DELETE FROM notification_outbox WHERE status = 'sent' AND sent_at IS NOT NULL"
                " AND sent_at < ?",
                (cutoff,),
            ).rowcount
            events = connection.execute(
                "DELETE FROM event WHERE occurred_at < ?"
                " AND id NOT IN (SELECT event_id FROM notification_outbox)",
                (cutoff,),
            ).rowcount
            audit = connection.execute(
                "DELETE FROM audit_log WHERE ts < ?",
                (cutoff,),
            ).rowcount
            # Chat history is a working buffer, not a business record, so it
            # follows the same retention window as the audit trail.
            assistant = connection.execute(
                "DELETE FROM assistant_message WHERE created_at < ?",
                (cutoff,),
            ).rowcount
        return {
            "outbox": max(int(outbox), 0),
            "events": max(int(events), 0),
            "audit": max(int(audit), 0),
            "assistant": max(int(assistant), 0),
        }

    def checkpoint_wal(self) -> None:
        """Fold the write-ahead log back into the database file."""
        connection = self._connection_or_raise()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def save_topology(self, topology: EntityRegistry) -> None:
        """Persist a complete site -> area -> camera -> stream hierarchy.

        Inserts are ordered from parents to children so foreign-key checks are
        active even when a caller builds the registry in arbitrary order.
        Existing IDs are updated in place and unrelated records are retained.
        """
        connection = self._connection_or_raise()
        with connection:
            for site in topology.sites:
                self._upsert_site(connection, site)
            for area in topology.areas:
                self._upsert_area(connection, area)
            for device in topology.devices:
                self._upsert_device(connection, device)
            for camera in topology.cameras:
                self._upsert_camera(connection, camera)
            for stream in topology.streams:
                self._upsert_stream(connection, stream)

    def save_camera_bundle(
        self,
        device: VideoDevice,
        camera: CameraDevice,
        stream: VideoStream | None = None,
        *,
        actor: str = "system",
    ) -> None:
        """Persist a camera bundle atomically while retaining topology audit."""
        if not actor.strip():
            raise ValueError("camera bundle actor is required")
        if camera.device_id != device.id:
            raise ValueError("camera bundle device_id must match the physical device")
        if camera.area_id != device.area_id:
            raise ValueError("camera bundle area_id must match the physical device")
        if stream is not None:
            if stream.camera_id != camera.id:
                raise ValueError("camera bundle stream camera_id must match the camera")
            if stream.device_id not in (None, device.id):
                raise ValueError("camera bundle stream device_id must match the physical device")
        now = datetime.now(UTC).isoformat()
        connection = self._connection_or_raise()
        entries = [
            (
                "topology.device.updated",
                device.id,
                {
                    "site_id": device.site_id,
                    "area_id": device.area_id,
                    "protocol": str(device.protocol),
                    "endpoint": _redact_url(device.endpoint),
                    "capabilities": device.capabilities.as_dict(),
                    "analysis_policy": str(device.analysis_policy),
                    "enabled": device.enabled,
                    "health_status": device.health_status,
                    "last_probe_at": device.last_probe_at,
                    "last_ok_at": device.last_ok_at,
                },
            ),
            (
                "topology.camera.updated",
                camera.id,
                {
                    "area_id": camera.area_id,
                    "name": camera.name,
                    "vendor": camera.vendor,
                    "address": _redact_url(camera.address),
                    "enabled": camera.enabled,
                },
            ),
        ]
        if stream is not None:
            entries.append(
                (
                    "topology.stream.updated",
                    stream.id,
                    {
                        "camera_id": stream.camera_id,
                        "name": stream.name,
                        "url": _redact_url(stream.url),
                        "role": str(stream.role),
                        "width": stream.width,
                        "height": stream.height,
                        "fps": stream.fps,
                        "enabled": bool(stream.enabled),
                    },
                )
            )
        with connection:
            self._upsert_device(connection, device)
            self._upsert_camera(connection, camera)
            if stream is not None:
                self._upsert_stream(connection, stream)
            for action, resource, detail in entries:
                connection.execute(
                    """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                       VALUES (?, ?, ?, 'success', ?, ?)""",
                    (
                        actor,
                        action,
                        resource,
                        now,
                        json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    ),
                )

    def save_device_camera_batch(
        self,
        devices: Sequence[VideoDevice],
        cameras: Sequence[CameraDevice],
        *,
        actor: str = "system",
    ) -> None:
        """Persist shared-device camera imports as one all-or-nothing batch."""
        if not actor.strip():
            raise ValueError("camera import actor is required")
        device_list = tuple(devices)
        camera_list = tuple(cameras)
        if not device_list and not camera_list:
            return
        device_by_id = {device.id: device for device in device_list}
        if len(device_by_id) != len(device_list):
            raise ValueError("camera import device ids must be unique")
        camera_by_id = {camera.id: camera for camera in camera_list}
        if len(camera_by_id) != len(camera_list):
            raise ValueError("camera import camera ids must be unique")
        connection = self._connection_or_raise()
        device_refs: dict[str, tuple[str, str]] = {
            device.id: (device.site_id, device.area_id) for device in device_list
        }
        for camera in camera_list:
            refs = device_refs.get(camera.device_id)
            if refs is None:
                row = connection.execute(
                    "SELECT site_id, area_id FROM video_device WHERE id = ?",
                    (camera.device_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("camera import references an unknown physical device")
                refs = (str(row["site_id"]), str(row["area_id"]))
                device_refs[camera.device_id] = refs
            site_id, area_id = refs
            area = connection.execute(
                "SELECT site_id FROM area WHERE id = ?",
                (camera.area_id,),
            ).fetchone()
            if area is None:
                raise ValueError("camera import references an unknown area")
            if str(area["site_id"]) != site_id:
                raise ValueError("camera import device and area must belong to the same site")
            if camera.area_id != area_id:
                raise ValueError("camera import area_id must match the physical device")
        now = datetime.now(UTC).isoformat()
        with connection:
            for device in device_list:
                self._upsert_device(connection, device)
            for camera in camera_list:
                self._upsert_camera(connection, camera)
            for device in device_list:
                self._insert_topology_audit(
                    connection,
                    actor=actor,
                    action="topology.device.updated",
                    resource=device.id,
                    now=now,
                    detail={
                        "site_id": device.site_id,
                        "area_id": device.area_id,
                        "protocol": str(device.protocol),
                        "endpoint": _redact_url(device.endpoint),
                        "capabilities": device.capabilities.as_dict(),
                        "analysis_policy": str(device.analysis_policy),
                        "enabled": device.enabled,
                        "health_status": device.health_status,
                        "last_probe_at": device.last_probe_at,
                        "last_ok_at": device.last_ok_at,
                    },
                )
            for camera in camera_list:
                self._insert_topology_audit(
                    connection,
                    actor=actor,
                    action="topology.camera.updated",
                    resource=camera.id,
                    now=now,
                    detail={
                        "area_id": camera.area_id,
                        "name": camera.name,
                        "vendor": camera.vendor,
                        "address": _redact_url(camera.address),
                        "enabled": camera.enabled,
                        "device_id": camera.device_id,
                        "channel_id": camera.channel_id,
                    },
                )

    @staticmethod
    def _insert_topology_audit(
        connection: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        resource: str,
        now: str,
        detail: Mapping[str, object],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
               VALUES (?, ?, ?, 'success', ?, ?)""",
            (
                actor,
                action,
                resource,
                now,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
            ),
        )

    def save_area_bundle(self, site: Site, area: Area, *, actor: str = "system") -> None:
        """Persist a site and area atomically with topology audit."""
        if not actor.strip():
            raise ValueError("area bundle actor is required")
        if area.site_id != site.id:
            raise ValueError("area bundle site_id must match the site")
        if area.parent_id == area.id:
            raise ValueError("area cannot be its own parent")
        connection = self._connection_or_raise()
        duplicate = connection.execute(
            "SELECT id FROM area WHERE site_id = ? AND lower(name) = lower(?) AND id != ?",
            (area.site_id, area.name, area.id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("area name must be unique within the same site")
        if area.parent_id:
            parent = connection.execute(
                "SELECT site_id FROM area WHERE id = ?", (area.parent_id,)
            ).fetchone()
            if parent is None:
                raise ValueError("area bundle references an unknown parent area")
            if str(parent["site_id"]) != site.id:
                raise ValueError("area bundle parent must belong to the same site")
        now = datetime.now(UTC).isoformat()
        entries = [
            (
                "topology.site.updated",
                site.id,
                {"name": site.name, "description": site.description, "enabled": site.enabled},
            ),
            (
                "topology.area.updated",
                area.id,
                {
                    "site_id": area.site_id,
                    "parent_id": area.parent_id,
                    "name": area.name,
                    "geometry_points": len(area.geometry),
                    "enabled": area.enabled,
                },
            ),
        ]
        with connection:
            self._upsert_site(connection, site)
            self._upsert_area(connection, area)
            for action, resource, detail in entries:
                connection.execute(
                    """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                       VALUES (?, ?, ?, 'success', ?, ?)""",
                    (
                        actor,
                        action,
                        resource,
                        now,
                        json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    ),
                )

    def save_site(self, site: Site, *, actor: str = "system") -> None:
        self._save_topology_entity(
            kind="site",
            entity_id=site.id,
            actor=actor,
            detail={"name": site.name, "description": site.description, "enabled": site.enabled},
            upsert=lambda connection: self._upsert_site(connection, site),
        )

    def delete_site(self, site_id: str, *, actor: str = "system") -> bool:
        """Delete an empty site; sites that still hold areas are rejected."""
        if not actor.strip():
            raise ValueError("site actor is required")
        if not site_id:
            raise ValueError("site id is required")
        connection = self._connection_or_raise()
        now = datetime.now(UTC).isoformat()
        with connection:
            site = connection.execute(
                "SELECT id, name FROM site WHERE id = ?",
                (site_id,),
            ).fetchone()
            if site is None:
                return False
            blocked = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM area WHERE site_id = ?)",
                (site_id,),
            ).fetchone()[0]
            if blocked:
                raise ValueError("该场所下仍有区域，请先移除区域后再删除")
            connection.execute("DELETE FROM site WHERE id = ?", (site_id,))
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'topology.site.deleted', ?, 'success', ?, ?)""",
                (
                    actor,
                    site_id,
                    now,
                    json.dumps({"name": str(site["name"])}, ensure_ascii=False),
                ),
            )
        return True

    def save_area(self, area: Area, *, actor: str = "system") -> None:
        self._save_topology_entity(
            kind="area",
            entity_id=area.id,
            actor=actor,
            detail={
                "site_id": area.site_id,
                "parent_id": area.parent_id,
                "name": area.name,
                "geometry_points": len(area.geometry),
                "enabled": area.enabled,
            },
            upsert=lambda connection: self._upsert_area(connection, area),
        )

    def save_device(self, device: VideoDevice, *, actor: str = "system") -> None:
        self._save_topology_entity(
            kind="device",
            entity_id=device.id,
            actor=actor,
            detail={
                "site_id": device.site_id,
                "area_id": device.area_id,
                "protocol": str(device.protocol),
                "endpoint": _redact_url(device.endpoint),
                "capabilities": device.capabilities.as_dict(),
                "analysis_policy": str(device.analysis_policy),
                "enabled": device.enabled,
                "health_status": device.health_status,
                "last_probe_at": device.last_probe_at,
                "last_ok_at": device.last_ok_at,
            },
            upsert=lambda connection: self._upsert_device(connection, device),
        )

    def save_camera(self, camera: CameraDevice, *, actor: str = "system") -> None:
        self._save_topology_entity(
            kind="camera",
            entity_id=camera.id,
            actor=actor,
            detail={
                "area_id": camera.area_id,
                "name": camera.name,
                "vendor": camera.vendor,
                "address": _redact_url(camera.address),
                "enabled": camera.enabled,
            },
            upsert=lambda connection: self._upsert_camera(connection, camera),
        )

    def save_stream(self, stream: VideoStream, *, actor: str = "system") -> None:
        """Insert or update one stream and record the topology change.

        ``save_topology`` is used for bulk imports and deliberately keeps its
        existing audit behavior.  This method is the single-entity edit path
        used by the control panel, so it records a redacted change summary.
        """
        if not actor.strip():
            raise ValueError("stream actor is required")
        connection = self._connection_or_raise()
        previous = connection.execute(
            "SELECT camera_id, name, url, role, width, height, fps, enabled "
            "FROM video_stream WHERE id = ?",
            (stream.id,),
        ).fetchone()
        current = {
            "camera_id": stream.camera_id,
            "name": stream.name,
            "url": _redact_url(stream.url),
            "role": str(stream.role),
            "width": stream.width,
            "height": stream.height,
            "fps": stream.fps,
            "enabled": bool(stream.enabled),
        }
        if previous is None:
            changes = current
            operation = "created"
        else:
            changes = {
                key: value
                for key, value in current.items()
                if value != (bool(previous[key]) if key == "enabled" else previous[key])
            }
            operation = "updated"
        detail = json.dumps(
            {"operation": operation, "stream_id": stream.id, "changes": changes},
            ensure_ascii=False,
            sort_keys=True,
        )
        now = datetime.now(UTC).isoformat()
        with connection:
            self._upsert_stream(connection, stream)
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'topology.stream.updated', ?, 'success', ?, ?)""",
                (actor, stream.id, now, detail),
            )

    def set_entity_enabled(
        self, kind: str, entity_id: str, enabled: bool, *, actor: str = "system"
    ) -> bool:
        """Enable or disable one topology entity without deleting it."""
        tables = {
            "site": "site",
            "area": "area",
            "device": "video_device",
            "camera": "camera_device",
            "stream": "video_stream",
        }
        table = tables.get(kind)
        if table is None:
            raise ValueError("entity kind must be site, area, device, camera, or stream")
        if not entity_id:
            raise ValueError("entity id is required")
        if not actor.strip():
            raise ValueError("entity actor is required")
        connection = self._connection_or_raise()
        with connection:
            result = connection.execute(
                f"UPDATE {table} SET enabled = ? WHERE id = ?", (int(enabled), entity_id)
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'topology.enabled.changed', ?, 'success', ?, ?)""",
                (
                    actor,
                    entity_id,
                    datetime.now(UTC).isoformat(),
                    f"kind={kind}; enabled={int(enabled)}",
                ),
            )
        return True

    def set_camera_enabled(self, camera_id: str, enabled: bool, *, actor: str = "system") -> bool:
        """Pause or resume one camera/channel without deleting its history.

        A physical device is disabled only when none of its channels remain
        enabled, so other channels on a shared NVR/camera keep subscribing.
        """
        if not camera_id.strip():
            raise ValueError("camera id is required")
        if not actor.strip():
            raise ValueError("camera actor is required")
        connection = self._connection_or_raise()
        now = datetime.now(UTC).isoformat()
        with connection:
            camera = connection.execute(
                "SELECT id, device_id FROM camera_device WHERE id = ?",
                (camera_id,),
            ).fetchone()
            if camera is None:
                raise KeyError(f"camera not found: {camera_id}")
            device_id = str(camera["device_id"]) if camera["device_id"] else ""
            connection.execute(
                "UPDATE camera_device SET enabled = ? WHERE id = ?",
                (int(enabled), camera_id),
            )
            device_enabled: bool | None = None
            if device_id:
                if enabled:
                    connection.execute(
                        "UPDATE video_device SET enabled = 1 WHERE id = ?", (device_id,)
                    )
                    device_enabled = True
                else:
                    other_enabled = connection.execute(
                        """SELECT 1 FROM camera_device
                           WHERE device_id = ? AND id != ? AND enabled = 1 LIMIT 1""",
                        (device_id, camera_id),
                    ).fetchone()
                    if other_enabled is None:
                        connection.execute(
                            "UPDATE video_device SET enabled = 0 WHERE id = ?", (device_id,)
                        )
                        device_enabled = False
                    else:
                        device_enabled = True
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'topology.camera.enabled', ?, 'success', ?, ?)""",
                (
                    actor,
                    camera_id,
                    now,
                    json.dumps(
                        {
                            "enabled": bool(enabled),
                            "device_id": device_id or None,
                            "device_enabled": device_enabled,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        return True

    def delete_camera(self, camera_id: str, *, actor: str = "system") -> str | None:
        """Delete one camera/channel and its streams while preserving event history.

        The physical device is removed only when no other channel references it.
        The returned credential reference, if any, can then be removed from the
        external configuration store by the UI or caller.
        """
        if not actor.strip():
            raise ValueError("camera actor is required")
        if not camera_id:
            raise ValueError("camera id is required")
        connection = self._connection_or_raise()
        now = datetime.now(UTC).isoformat()
        with connection:
            camera = connection.execute(
                "SELECT id, name, device_id FROM camera_device WHERE id = ?",
                (camera_id,),
            ).fetchone()
            if camera is None:
                raise KeyError(f"camera not found: {camera_id}")
            device_id = str(camera["device_id"]) if camera["device_id"] else ""
            credential_ref: str | None = None
            if device_id:
                device = connection.execute(
                    "SELECT credential_ref FROM video_device WHERE id = ?",
                    (device_id,),
                ).fetchone()
                if device is not None and device["credential_ref"]:
                    credential_ref = str(device["credential_ref"])
            stream_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM video_stream WHERE camera_id = ?",
                    (camera_id,),
                )
            ]
            if stream_ids:
                placeholders = ",".join("?" for _ in stream_ids)
                connection.execute(
                    f"DELETE FROM stream_health WHERE stream_id IN ({placeholders})",
                    stream_ids,
                )
                connection.execute(
                    f"DELETE FROM stream_arming WHERE stream_id IN ({placeholders})",
                    stream_ids,
                )
                connection.execute(
                    f"DELETE FROM arming_schedule WHERE stream_id IN ({placeholders})",
                    stream_ids,
                )
                connection.execute(
                    f"DELETE FROM detection_rule WHERE stream_id IN ({placeholders})",
                    stream_ids,
                )
            connection.execute("DELETE FROM video_stream WHERE camera_id = ?", (camera_id,))
            connection.execute("DELETE FROM camera_device WHERE id = ?", (camera_id,))
            removed_device = False
            if device_id:
                remaining = connection.execute(
                    "SELECT 1 FROM camera_device WHERE device_id = ? LIMIT 1",
                    (device_id,),
                ).fetchone()
                if remaining is None:
                    connection.execute(
                        "DELETE FROM device_subscription WHERE device_id = ?",
                        (device_id,),
                    )
                    connection.execute(
                        "DELETE FROM recording_index WHERE device_id = ?",
                        (device_id,),
                    )
                    connection.execute("DELETE FROM video_device WHERE id = ?", (device_id,))
                    removed_device = True
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'topology.camera.deleted', ?, 'success', ?, ?)""",
                (
                    actor,
                    camera_id,
                    now,
                    json.dumps(
                        {
                            "name": str(camera["name"]),
                            "device_id": device_id,
                            "device_removed": removed_device,
                            "streams_removed": len(stream_ids),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        return credential_ref if removed_device else None

    def delete_area(self, area_id: str, *, actor: str = "system") -> bool:
        """Delete an empty area; populated or nested areas are rejected."""
        if not actor.strip():
            raise ValueError("area actor is required")
        if not area_id:
            raise ValueError("area id is required")
        connection = self._connection_or_raise()
        now = datetime.now(UTC).isoformat()
        with connection:
            area = connection.execute(
                "SELECT id, name FROM area WHERE id = ?",
                (area_id,),
            ).fetchone()
            if area is None:
                return False
            blocked = connection.execute(
                """SELECT
                     EXISTS(SELECT 1 FROM camera_device WHERE area_id = ?)
                     OR EXISTS(SELECT 1 FROM video_device WHERE area_id = ?)
                     OR EXISTS(SELECT 1 FROM area WHERE parent_id = ?)""",
                (area_id, area_id, area_id),
            ).fetchone()[0]
            if blocked:
                raise ValueError("区域仍包含摄像头、设备或子区域，请先移除关联内容")
            connection.execute("DELETE FROM area WHERE id = ?", (area_id,))
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'topology.area.deleted', ?, 'success', ?, ?)""",
                (actor, area_id, now, json.dumps({"name": str(area["name"])}, ensure_ascii=False)),
            )
        return True

    def _save_topology_entity(
        self,
        *,
        kind: str,
        entity_id: str,
        actor: str,
        detail: Mapping[str, object],
        upsert: Any,
    ) -> None:
        """Persist one topology entity and write a redacted audit record."""
        if not actor.strip():
            raise ValueError(f"{kind} actor is required")
        if not entity_id:
            raise ValueError(f"{kind} id is required")
        now = datetime.now(UTC).isoformat()
        connection = self._connection_or_raise()
        with connection:
            upsert(connection)
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, ?, ?, 'success', ?, ?)""",
                (
                    actor,
                    f"topology.{kind}.updated",
                    entity_id,
                    now,
                    json.dumps(detail, ensure_ascii=False, sort_keys=True),
                ),
            )

    def load_topology(self) -> EntityRegistry:
        """Load and validate the site -> area -> device -> channel -> stream tree."""
        connection = self._connection_or_raise()
        sites = [
            Site(
                str(row["id"]),
                str(row["name"]),
                str(row["description"] or ""),
                bool(row["enabled"]),
            )
            for row in connection.execute("SELECT * FROM site ORDER BY rowid")
        ]
        areas = [
            Area(
                id=str(row["id"]),
                site_id=str(row["site_id"]),
                name=str(row["name"]),
                parent_id=str(row["parent_id"]) if row["parent_id"] is not None else None,
                geometry=tuple(tuple(point) for point in json.loads(row["geometry"] or "[]")),
                enabled=bool(row["enabled"]),
            )
            for row in connection.execute("SELECT * FROM area ORDER BY rowid")
        ]
        devices = [
            VideoDevice(
                id=str(row["id"]),
                site_id=str(row["site_id"]),
                area_id=str(row["area_id"]),
                name=str(row["name"]),
                protocol=str(row["protocol"]),
                endpoint=str(row["endpoint"]),
                vendor=str(row["vendor"] or ""),
                model=str(row["model"] or ""),
                firmware=str(row["firmware"] or ""),
                serial_number=str(row["serial_number"] or ""),
                device_id=str(row["device_id"] or ""),
                credentials_ref=str(row["credential_ref"]) if row["credential_ref"] else None,
                capabilities=DeviceCapabilities.from_dict(json.loads(row["capabilities"] or "{}")),
                analysis_policy=str(row["analysis_policy"]),
                enabled=bool(row["enabled"]),
                last_probe_at=str(row["last_probe_at"]) if row["last_probe_at"] else None,
                last_ok_at=str(row["last_ok_at"]) if row["last_ok_at"] else None,
                health_status=str(row["health_status"] or "unknown"),
                health_detail=str(row["health_detail"] or ""),
            )
            for row in connection.execute("SELECT * FROM video_device ORDER BY rowid")
        ]
        cameras = [
            CameraDevice(
                id=str(row["id"]),
                area_id=str(row["area_id"]),
                name=str(row["name"]),
                vendor=str(row["vendor"] or ""),
                address=str(row["address"] or ""),
                enabled=bool(row["enabled"]),
                device_id=str(row["device_id"]) if row["device_id"] else None,
                channel_id=str(row["channel_id"] or ""),
            )
            for row in connection.execute("SELECT * FROM camera_device ORDER BY rowid")
        ]
        streams = [
            VideoStream(
                id=str(row["id"]),
                camera_id=str(row["camera_id"]),
                name=str(row["name"]),
                url=str(row["url"]),
                role=str(row["role"]),
                width=int(row["width"]),
                height=int(row["height"]),
                fps=float(row["fps"]),
                enabled=bool(row["enabled"]),
                device_id=str(row["device_id"]) if row["device_id"] else None,
                analysis_policy=str(row["analysis_policy"]) if row["analysis_policy"] else None,
                native_event_codes=tuple(json.loads(row["native_event_codes"] or "[]")),
            )
            for row in connection.execute("SELECT * FROM video_stream ORDER BY rowid")
        ]
        return EntityRegistry(
            sites=sites, areas=areas, devices=devices, cameras=cameras, streams=streams
        )

    def list_stream_paths(self) -> list[dict[str, object]]:
        """Return resolved entity paths for stream selectors and dashboards."""
        topology = self.load_topology()
        return [topology.stream_path(stream.id).as_dict() for stream in topology.streams]

    def set_stream_health(
        self,
        stream_id: str,
        status: str,
        detail: str = "",
        *,
        updated_at: datetime | None = None,
        actor: str = "worker",
    ) -> None:
        """Persist one stream health transition and its recovery counters."""
        if not stream_id:
            raise ValueError("stream_id is required")
        normalized_status = status.strip().lower()
        if not normalized_status:
            raise ValueError("stream health status is required")
        if not actor.strip():
            raise ValueError("stream health actor is required")
        now = _timestamp(updated_at)
        connection = self._connection_or_raise()
        previous = connection.execute(
            "SELECT status, reconnect_count, last_ok_at FROM stream_health WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()
        reconnect_count = int(previous["reconnect_count"]) if previous is not None else 0
        if normalized_status in {"degraded", "offline"} and (
            previous is None or str(previous["status"]) != normalized_status
        ):
            reconnect_count += 1
        last_ok_at = previous["last_ok_at"] if previous is not None else None
        if normalized_status in {"ready", "running", "online", "healthy", "ok", "connected"}:
            last_ok_at = now
        safe_detail = _redact_url(str(detail))[-2000:]
        with connection:
            connection.execute(
                """INSERT INTO stream_health
                   (stream_id, status, detail, updated_at, last_ok_at, reconnect_count)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(stream_id) DO UPDATE SET status=excluded.status,
                     detail=excluded.detail, updated_at=excluded.updated_at,
                     last_ok_at=excluded.last_ok_at, reconnect_count=excluded.reconnect_count""",
                (stream_id, normalized_status, safe_detail, now, last_ok_at, reconnect_count),
            )
            if previous is None or str(previous["status"]) != normalized_status:
                connection.execute(
                    """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                       VALUES (?, 'stream.health.changed', ?, 'success', ?, ?)""",
                    (actor, stream_id, now, f"status={normalized_status}; detail={safe_detail}"),
                )

    def list_stream_health(self) -> dict[str, dict[str, object]]:
        """Return persisted health snapshots keyed by stream ID."""
        return {
            str(row["stream_id"]): {
                "status": str(row["status"]),
                "detail": str(row["detail"]),
                "updated_at": str(row["updated_at"]),
                "last_ok_at": str(row["last_ok_at"]) if row["last_ok_at"] else None,
                "reconnect_count": int(row["reconnect_count"]),
            }
            for row in self._connection_or_raise().execute(
                "SELECT * FROM stream_health ORDER BY stream_id"
            )
        }

    def save_rule(self, rule: Rule, *, actor: str = "system", note: str = "") -> None:
        """Insert or update a persisted detection rule and audit the change."""
        if not actor.strip():
            raise ValueError("rule actor is required")
        connection = self._connection_or_raise()
        detail = json.dumps(_rule_to_dict(rule), ensure_ascii=False, sort_keys=True)
        with connection:
            self._upsert_rule(connection, rule)
            connection.execute(
                """INSERT OR REPLACE INTO rule_version
                   (rule_id, version, definition, created_at, actor, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    rule.id,
                    rule.version,
                    detail,
                    datetime.now(UTC).isoformat(),
                    actor,
                    note.strip(),
                ),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'rule.updated', ?, 'success', ?, ?)""",
                (actor, rule.id, datetime.now(UTC).isoformat(), detail),
            )

    def load_rules(
        self,
        *,
        stream_id: str | None = None,
        enabled_only: bool = False,
        published_only: bool = False,
    ) -> list[Rule]:
        """Load persisted rules, optionally scoped to one stream."""
        connection = self._connection_or_raise()
        clauses: list[str] = []
        values: list[object] = []
        if stream_id:
            clauses.append("stream_id = ?")
            values.append(stream_id)
        if enabled_only:
            clauses.append("enabled = 1")
        if published_only:
            clauses.append("publication_status = 'published'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = connection.execute(
            f"SELECT * FROM detection_rule {where} ORDER BY rowid", values
        ).fetchall()
        rules: list[Rule] = []
        for row in rows:
            rules.append(
                Rule(
                    id=str(row["id"]),
                    channel_id=str(row["stream_id"]),
                    stream_id=str(row["stream_id"]),
                    site_id=str(row["site_id"]) if row["site_id"] is not None else None,
                    area_id=str(row["area_id"]) if row["area_id"] is not None else None,
                    camera_id=str(row["camera_id"]) if row["camera_id"] is not None else None,
                    name=str(row["name"]),
                    actions=tuple(json.loads(row["actions"] or "[]")) or ("event", "notify"),
                    http_action_url=str(row["http_action_url"] or ""),
                    http_action_timeout=float(row["http_action_timeout"] or 5.0),
                    ack_deadline_seconds=float(row["ack_deadline_seconds"] or 0.0),
                    escalation_level=str(row["escalation_level"] or ""),
                    destination_ids=tuple(json.loads(row["destination_ids"] or "[]")),
                    assignees=tuple(json.loads(row["assignees"] or "[]")),
                    native_event_codes=tuple(json.loads(row["native_event_codes"] or "[]")),
                    dedup_window_seconds=float(row["dedup_window_seconds"] or 5.0),
                    enabled=bool(row["enabled"]),
                    publication_status=str(row["publication_status"] or "published"),
                    version=int(row["version"]),
                )
            )
        return rules

    def list_rule_versions(self, rule_id: str, *, limit: int = 50) -> list[sqlite3.Row]:
        if not rule_id.strip():
            raise ValueError("rule_id is required")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("rule version limit must be positive")
        return list(
            self._connection_or_raise().execute(
                """SELECT rule_id, version, created_at, actor, note
                   FROM rule_version WHERE rule_id = ?
                   ORDER BY version DESC LIMIT ?""",
                (rule_id, limit),
            )
        )

    def restore_rule_version(self, rule_id: str, version: int, *, actor: str = "system") -> Rule:
        if not rule_id.strip() or not actor.strip():
            raise ValueError("rule_id and actor are required")
        connection = self._connection_or_raise()
        row = connection.execute(
            "SELECT definition FROM rule_version WHERE rule_id = ? AND version = ?",
            (rule_id, version),
        ).fetchone()
        if row is None:
            raise ValueError(f"rule version not found: {rule_id} v{version}")
        restored = _rule_from_dict(json.loads(row["definition"]))
        max_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM rule_version WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
        next_version = int(max_row["version"]) + 1
        restored = replace(restored, version=next_version)
        self.save_rule(restored, actor=actor, note=f"restored from v{version}")
        return restored

    def _rule_by_id(self, rule_id: str) -> Rule | None:
        if not rule_id.strip():
            return None
        return next((rule for rule in self.load_rules() if rule.id == rule_id), None)

    def submit_rule_for_review(
        self, rule_id: str, *, actor: str = "system", note: str = ""
    ) -> Rule:
        if not rule_id.strip() or not actor.strip():
            raise ValueError("rule_id and actor are required")
        rule = self._rule_by_id(rule_id)
        if rule is None:
            raise ValueError(f"rule not found: {rule_id}")
        if rule.publication_status != "draft":
            raise ValueError("only draft rules can be submitted for review")
        updated = replace(rule, publication_status="pending_review", version=rule.version + 1)
        self.save_rule(updated, actor=actor, note=note.strip() or "submitted for review")
        return updated

    def approve_rule(self, rule_id: str, *, actor: str = "system", note: str = "") -> Rule:
        if not rule_id.strip() or not actor.strip():
            raise ValueError("rule_id and actor are required")
        rule = self._rule_by_id(rule_id)
        if rule is None:
            raise ValueError(f"rule not found: {rule_id}")
        if rule.publication_status != "pending_review":
            raise ValueError("only pending_review rules can be approved")
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT OR IGNORE INTO rule_review
                (rule_id, version, actor, decision, note, ts)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    rule.id,
                    rule.version,
                    actor,
                    "approve",
                    note.strip(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        approvers = self.rule_approval_actors(rule.id, rule.version)
        if len(approvers) >= 2:
            published = replace(rule, publication_status="published", version=rule.version + 1)
            self.save_rule(published, actor=actor, note=note.strip() or "approved by two reviewers")
            return published
        return rule

    def reject_rule(self, rule_id: str, *, actor: str = "system", note: str = "") -> Rule:
        if not rule_id.strip() or not actor.strip():
            raise ValueError("rule_id and actor are required")
        rule = self._rule_by_id(rule_id)
        if rule is None:
            raise ValueError(f"rule not found: {rule_id}")
        if rule.publication_status != "pending_review":
            raise ValueError("only pending_review rules can be rejected")
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT OR IGNORE INTO rule_review
                (rule_id, version, actor, decision, note, ts)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    rule.id,
                    rule.version,
                    actor,
                    "reject",
                    note.strip(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        updated = replace(rule, publication_status="draft", version=rule.version + 1)
        self.save_rule(updated, actor=actor, note=note.strip() or "rejected; returned to draft")
        return updated

    def rule_approval_actors(self, rule_id: str, version: int | None = None) -> tuple[str, ...]:
        if not rule_id.strip():
            raise ValueError("rule_id is required")
        connection = self._connection_or_raise()
        if version is None:
            rows = connection.execute(
                """SELECT DISTINCT actor FROM rule_review
                   WHERE rule_id = ? AND decision = 'approve'
                   ORDER BY ts""",
                (rule_id,),
            )
        else:
            rows = connection.execute(
                """SELECT DISTINCT actor FROM rule_review
                   WHERE rule_id = ? AND version = ? AND decision = 'approve'
                   ORDER BY ts""",
                (rule_id, int(version)),
            )
        return tuple(str(row[0]) for row in rows)

    def list_rule_reviews(self, rule_id: str, *, limit: int = 100) -> list[sqlite3.Row]:
        if not rule_id.strip():
            raise ValueError("rule_id is required")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("review limit must be positive")
        return list(
            self._connection_or_raise().execute(
                """SELECT rule_id, version, actor, decision, note, ts
                   FROM rule_review WHERE rule_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (rule_id, limit),
            )
        )

    def export_rule_template(self, rule_id: str) -> dict[str, Any]:
        """Export one rule as a portable template without retaining its ID."""
        if not rule_id.strip():
            raise ValueError("rule_id is required")
        rule = self._rule_by_id(rule_id)
        if rule is None:
            raise ValueError(f"rule not found: {rule_id}")
        definition = dict(_rule_to_dict(rule))
        definition.pop("id", None)
        definition.pop("version", None)
        definition.pop("enabled", None)
        definition.pop("publication_status", None)
        definition.pop("stream_id", None)
        definition.pop("site_id", None)
        definition.pop("area_id", None)
        definition.pop("camera_id", None)
        return {
            "schema_version": 1,
            "type": "factoryguard_rule_template",
            "name": rule.name,
            "rule": definition,
        }

    def import_rule_template(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str = "system",
        note: str = "",
        new_id: str | None = None,
        stream_id: str | None = None,
    ) -> Rule:
        """Import a portable rule template and persist it as a new draft."""
        if not actor.strip():
            raise ValueError("rule actor is required")
        if not isinstance(payload, Mapping):
            raise TypeError("rule template must be a mapping")
        if payload.get("type") != "factoryguard_rule_template":
            raise ValueError("invalid rule template type")
        definition = payload.get("rule")
        if not isinstance(definition, Mapping):
            raise ValueError("rule template is missing rule definition")
        if not definition.get("stream_id") and not stream_id:
            raise ValueError("rule template is missing stream_id and no target stream was supplied")
        if not definition.get("stream_id") and stream_id:
            definition = dict(definition)
            definition["stream_id"] = stream_id
        definition = dict(definition)
        definition["id"] = new_id or f"rule-imported-{uuid4().hex[:10]}"
        rule = _rule_from_dict(definition)
        rule = replace(
            rule,
            id=new_id or f"rule-imported-{uuid4().hex[:10]}",
            publication_status="draft",
            enabled=True,
            version=1,
        )
        self.save_rule(rule, actor=actor, note=note.strip() or "imported from rule template")
        return rule

    def set_rule_enabled(self, rule_id: str, enabled: bool, *, actor: str = "system") -> bool:
        """Enable or disable one rule without deleting its history."""
        if not rule_id:
            raise ValueError("rule_id is required")
        if not actor.strip():
            raise ValueError("rule actor is required")
        connection = self._connection_or_raise()
        with connection:
            result = connection.execute(
                "UPDATE detection_rule SET enabled = ? WHERE id = ?",
                (int(enabled), rule_id),
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'rule.enabled.changed', ?, 'success', ?, ?)""",
                (actor, rule_id, datetime.now(UTC).isoformat(), f"enabled={int(enabled)}"),
            )
        return True

    def get_stream_armed(self, stream_id: str, *, default: bool = True) -> bool:
        """Return one stream's persisted arming state."""
        if not stream_id:
            raise ValueError("stream_id is required")
        row = (
            self._connection_or_raise()
            .execute("SELECT armed FROM stream_arming WHERE stream_id = ?", (stream_id,))
            .fetchone()
        )
        return bool(row["armed"]) if row is not None else default

    def list_stream_arming(self) -> dict[str, bool]:
        """Return all explicitly persisted stream arming states."""
        return {
            str(row["stream_id"]): bool(row["armed"])
            for row in self._connection_or_raise().execute(
                "SELECT stream_id, armed FROM stream_arming"
            )
        }

    def set_stream_armed(self, stream_id: str, armed: bool, *, actor: str = "system") -> None:
        """Persist a stream's arm/disarm state and write an audit record."""
        if not stream_id:
            raise ValueError("stream_id is required")
        if not actor.strip():
            raise ValueError("arming actor is required")
        now = datetime.now(UTC).isoformat()
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT INTO stream_arming(stream_id, armed, updated_at, actor)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(stream_id) DO UPDATE SET armed=excluded.armed,
                     updated_at=excluded.updated_at, actor=excluded.actor""",
                (stream_id, int(armed), now, actor),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'stream.arming.changed', ?, 'success', ?, ?)""",
                (actor, stream_id, now, f"armed={int(armed)}"),
            )

    def save_arming_schedule(self, schedule: ArmingSchedule, *, actor: str = "system") -> None:
        """Persist one stream schedule and audit the configuration change."""
        if not actor.strip():
            raise ValueError("schedule actor is required")
        payload = schedule.as_dict()
        now = datetime.now(UTC).isoformat()
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT INTO arming_schedule
                   (stream_id, timezone, weekly, holidays, enabled, default_armed,
                    override_armed, override_until)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(stream_id) DO UPDATE SET timezone=excluded.timezone,
                     weekly=excluded.weekly, holidays=excluded.holidays,
                     enabled=excluded.enabled, default_armed=excluded.default_armed,
                     override_armed=excluded.override_armed,
                     override_until=excluded.override_until""",
                (
                    schedule.stream_id,
                    schedule.timezone,
                    json.dumps(payload["weekly"], separators=(",", ":")),
                    json.dumps(payload["holidays"], separators=(",", ":")),
                    int(schedule.enabled),
                    int(schedule.default_armed),
                    None if schedule.override_armed is None else int(schedule.override_armed),
                    schedule.override_until.isoformat() if schedule.override_until else None,
                ),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'arming.schedule.changed', ?, 'success', ?, ?)""",
                (
                    actor,
                    schedule.stream_id,
                    now,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get_arming_schedule(self, stream_id: str) -> ArmingSchedule | None:
        if not stream_id:
            raise ValueError("stream_id is required")
        row = (
            self._connection_or_raise()
            .execute("SELECT * FROM arming_schedule WHERE stream_id = ?", (stream_id,))
            .fetchone()
        )
        if row is None:
            return None
        weekly = json.loads(row["weekly"])
        holidays = json.loads(row["holidays"] or "[]")
        override_until = row["override_until"]
        return ArmingSchedule(
            stream_id=str(row["stream_id"]),
            weekly=tuple(
                tuple(TimeWindow(int(pair[0]), int(pair[1])) for pair in day) for day in weekly
            ),
            holidays=frozenset(str(item) for item in holidays),
            enabled=bool(row["enabled"]),
            default_armed=bool(row["default_armed"]),
            override_armed=bool(row["override_armed"])
            if row["override_armed"] is not None
            else None,
            override_until=datetime.fromisoformat(str(override_until)) if override_until else None,
            timezone=str(row["timezone"]),
        )

    def export_configuration(self) -> dict[str, Any]:
        """Return portable topology, rule, arming and schedule configuration.

        Events, media and notification history intentionally stay in the
        database backup path.  This JSON package contains only the mutable
        configuration needed to migrate a deployment or review it offline.
        Webhook secrets are never exported.
        """
        topology = self.load_topology()
        topology_payload = topology.to_dict()
        for stream in topology_payload["streams"]:
            stream["url"] = _redact_url(str(stream["url"]))
        for device in topology_payload.get("devices", []):
            device["endpoint"] = _redact_url(str(device["endpoint"]))
        schedules: list[dict[str, Any]] = []
        schedule_stream_ids = set(self.list_stream_arming()) | {
            str(row["stream_id"])
            for row in self._connection_or_raise().execute("SELECT stream_id FROM arming_schedule")
        }
        for stream_id in sorted(schedule_stream_ids):
            schedule = self.get_arming_schedule(stream_id)
            if schedule is not None:
                schedules.append(schedule.as_dict())
        return {
            "schema_version": 2,
            "exported_at": datetime.now(UTC).isoformat(),
            "topology": topology_payload,
            "credentials_redacted": True,
            "rules": [_rule_to_dict(rule) for rule in self.load_rules()],
            "stream_arming": self.list_stream_arming(),
            "arming_schedules": schedules,
        }

    def import_configuration(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str = "system",
        replace: bool = False,
    ) -> None:
        """Validate and atomically import a configuration package.

        ``replace`` only replaces configuration tables.  Events, audit rows,
        outbox records and media references remain intact so a migration does
        not destroy incident history.
        """
        if not isinstance(payload, Mapping):
            raise TypeError("configuration package must be a mapping")
        if int(payload.get("schema_version", 0)) not in {1, 2}:
            raise ValueError("unsupported configuration schema version")
        if not actor.strip():
            raise ValueError("configuration actor is required")
        topology = EntityRegistry.from_dict(dict(payload.get("topology") or {}))
        streams = {stream.id for stream in topology.streams}
        rules = [_rule_from_dict(item) for item in payload.get("rules", ())]
        if any(rule.stream_id not in streams for rule in rules):
            raise ValueError("configuration rule references an unknown stream")
        schedules = [
            ArmingSchedule.from_dict(dict(item)) for item in payload.get("arming_schedules", ())
        ]
        if any(schedule.stream_id not in streams for schedule in schedules):
            raise ValueError("configuration schedule references an unknown stream")
        arming = {
            str(stream_id): bool(value)
            for stream_id, value in dict(payload.get("stream_arming") or {}).items()
        }
        if any(stream_id not in streams for stream_id in arming):
            raise ValueError("configuration arming state references an unknown stream")

        connection = self._connection_or_raise()
        now = datetime.now(UTC).isoformat()
        with connection:
            if replace:
                connection.execute("DELETE FROM arming_schedule")
                connection.execute("DELETE FROM stream_arming")
                connection.execute("DELETE FROM detection_rule")
                connection.execute("DELETE FROM video_stream")
                connection.execute("DELETE FROM camera_device")
                connection.execute("DELETE FROM video_device")
                connection.execute("DELETE FROM area")
                connection.execute("DELETE FROM site")
            for site in topology.sites:
                self._upsert_site(connection, site)
            for area in topology.areas:
                self._upsert_area(connection, area)
            for device in topology.devices:
                self._upsert_device(connection, device)
            for camera in topology.cameras:
                self._upsert_camera(connection, camera)
            for stream in topology.streams:
                self._upsert_stream(connection, stream)
            for rule in rules:
                self._upsert_rule(connection, rule)
            for stream_id, armed in arming.items():
                connection.execute(
                    """INSERT INTO stream_arming(stream_id, armed, updated_at, actor)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(stream_id) DO UPDATE SET armed=excluded.armed,
                         updated_at=excluded.updated_at, actor=excluded.actor""",
                    (stream_id, int(armed), now, actor),
                )
            for schedule in schedules:
                self._upsert_schedule(connection, schedule)
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'configuration.imported', ?, 'success', ?, ?)""",
                (actor, "configuration", now, f"replace={int(replace)}"),
            )

    @staticmethod
    def _upsert_site(connection: sqlite3.Connection, site: Site) -> None:
        connection.execute(
            """INSERT INTO site(id, name, description, enabled) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                                             enabled=excluded.enabled""",
            (site.id, site.name, site.description, int(site.enabled)),
        )

    @staticmethod
    def _upsert_area(connection: sqlite3.Connection, area: Area) -> None:
        connection.execute(
            """INSERT INTO area(id, site_id, parent_id, name, geometry, enabled)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET site_id=excluded.site_id, parent_id=excluded.parent_id,
                                             name=excluded.name, geometry=excluded.geometry,
                                             enabled=excluded.enabled""",
            (
                area.id,
                area.site_id,
                area.parent_id,
                area.name,
                json.dumps(area.geometry),
                int(area.enabled),
            ),
        )

    @staticmethod
    def _upsert_device(connection: sqlite3.Connection, device: VideoDevice) -> None:
        connection.execute(
            """INSERT INTO video_device
               (id, site_id, area_id, name, protocol, endpoint, vendor, model, firmware,
                serial_number, device_id, credential_ref, capabilities, analysis_policy, enabled,
                last_probe_at, last_ok_at, health_status, health_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET site_id=excluded.site_id, area_id=excluded.area_id,
                 name=excluded.name, protocol=excluded.protocol, endpoint=excluded.endpoint,
                 vendor=excluded.vendor, model=excluded.model, firmware=excluded.firmware,
                 serial_number=excluded.serial_number, device_id=excluded.device_id,
                 credential_ref=excluded.credential_ref, capabilities=excluded.capabilities,
                 analysis_policy=excluded.analysis_policy, enabled=excluded.enabled,
                 last_probe_at=excluded.last_probe_at, last_ok_at=excluded.last_ok_at,
                 health_status=excluded.health_status, health_detail=excluded.health_detail""",
            (
                device.id,
                device.site_id,
                device.area_id,
                device.name,
                str(device.protocol),
                _redact_url(device.endpoint),
                device.vendor,
                device.model,
                device.firmware,
                device.serial_number,
                device.device_id,
                device.credentials_ref,
                json.dumps(
                    device.capabilities.as_dict(), ensure_ascii=False, separators=(",", ":")
                ),
                str(device.analysis_policy),
                int(device.enabled),
                device.last_probe_at,
                device.last_ok_at,
                device.health_status,
                device.health_detail,
            ),
        )

    @staticmethod
    def _upsert_camera(connection: sqlite3.Connection, camera: CameraDevice) -> None:
        connection.execute(
            """INSERT INTO camera_device(id, area_id, name, vendor, address, enabled, device_id, channel_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET area_id=excluded.area_id, name=excluded.name,
                 vendor=excluded.vendor, address=excluded.address, enabled=excluded.enabled,
                 device_id=excluded.device_id, channel_id=excluded.channel_id""",
            (
                camera.id,
                camera.area_id,
                camera.name,
                camera.vendor,
                camera.address,
                int(camera.enabled),
                camera.device_id,
                camera.channel_id,
            ),
        )

    @staticmethod
    def _upsert_stream(connection: sqlite3.Connection, stream: VideoStream) -> None:
        connection.execute(
            # Legacy schemas retain model/confidence/input_size as NOT NULL
            # columns.  They are scrubbed with fixed compatibility values and
            # are no longer part of the stream domain or user configuration.
            """INSERT INTO video_stream
               (id, camera_id, name, url, role, width, height, fps, model, confidence,
                enabled, input_size, device_id, analysis_policy, native_event_codes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0.25, ?, 640, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET camera_id=excluded.camera_id, name=excluded.name,
                 url=excluded.url, role=excluded.role, width=excluded.width, height=excluded.height,
                 fps=excluded.fps, model=NULL, confidence=0.25, enabled=excluded.enabled,
                 input_size=640, device_id=excluded.device_id,
                 analysis_policy=excluded.analysis_policy,
                 native_event_codes=excluded.native_event_codes""",
            (
                stream.id,
                stream.camera_id,
                stream.name,
                stream.url,
                str(stream.role),
                stream.width,
                stream.height,
                stream.fps,
                int(stream.enabled),
                stream.device_id,
                str(stream.analysis_policy) if stream.analysis_policy is not None else None,
                json.dumps(list(stream.native_event_codes), separators=(",", ":")),
            ),
        )

    @staticmethod
    def _upsert_rule(connection: sqlite3.Connection, rule: Rule) -> None:
        connection.execute(
            """INSERT INTO detection_rule
               (id, stream_id, site_id, area_id, camera_id, name, type, geometry,
                target_labels, min_confidence, min_box_height, confirm_frames,
                cooldown_seconds, direction, condition, count_threshold,
                window_seconds, dwell_seconds, actions, http_action_url,
                http_action_timeout, ack_deadline_seconds, escalation_level,
                destination_ids, assignees, condition_nodes, trigger_mode,
                native_event_codes, dedup_window_seconds, enabled, publication_status, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET stream_id=excluded.stream_id,
                 site_id=excluded.site_id, area_id=excluded.area_id,
                 camera_id=excluded.camera_id, name=excluded.name,
                 type=excluded.type, geometry=excluded.geometry,
                 target_labels=excluded.target_labels,
                 min_confidence=excluded.min_confidence,
                 min_box_height=excluded.min_box_height,
                 confirm_frames=excluded.confirm_frames,
                 cooldown_seconds=excluded.cooldown_seconds,
                 direction=excluded.direction, condition=excluded.condition,
                 count_threshold=excluded.count_threshold,
                 window_seconds=excluded.window_seconds,
                 dwell_seconds=excluded.dwell_seconds,
                 actions=excluded.actions,
                 http_action_url=excluded.http_action_url,
                 http_action_timeout=excluded.http_action_timeout,
                 ack_deadline_seconds=excluded.ack_deadline_seconds,
                 escalation_level=excluded.escalation_level,
                 destination_ids=excluded.destination_ids,
                 assignees=excluded.assignees,
                 condition_nodes=excluded.condition_nodes,
                 trigger_mode=excluded.trigger_mode,
                 native_event_codes=excluded.native_event_codes,
                 dedup_window_seconds=excluded.dedup_window_seconds,
                 enabled=excluded.enabled,
                 publication_status=excluded.publication_status,
                 version=excluded.version""",
            (
                rule.id,
                rule.stream_id,
                rule.site_id,
                rule.area_id,
                rule.camera_id,
                rule.name,
                "",
                "[]",
                "[]",
                0.0,
                0.0,
                0,
                0.0,
                "",
                "",
                0,
                0.0,
                0.0,
                json.dumps(list(rule.actions), separators=(",", ":")),
                rule.http_action_url,
                rule.http_action_timeout,
                rule.ack_deadline_seconds,
                rule.escalation_level,
                json.dumps(list(rule.destination_ids), separators=(",", ":")),
                json.dumps(list(rule.assignees), separators=(",", ":")),
                "[]",
                "native_event",
                json.dumps(list(rule.native_event_codes), separators=(",", ":")),
                rule.dedup_window_seconds,
                int(rule.enabled),
                rule.publication_status,
                rule.version,
            ),
        )

    @staticmethod
    def _upsert_schedule(connection: sqlite3.Connection, schedule: ArmingSchedule) -> None:
        payload = schedule.as_dict()
        connection.execute(
            """INSERT INTO arming_schedule
               (stream_id, timezone, weekly, holidays, enabled, default_armed,
                override_armed, override_until)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(stream_id) DO UPDATE SET timezone=excluded.timezone,
                 weekly=excluded.weekly, holidays=excluded.holidays,
                 enabled=excluded.enabled, default_armed=excluded.default_armed,
                 override_armed=excluded.override_armed,
                 override_until=excluded.override_until""",
            (
                schedule.stream_id,
                schedule.timezone,
                json.dumps(payload["weekly"], separators=(",", ":")),
                json.dumps(payload["holidays"], separators=(",", ":")),
                int(schedule.enabled),
                int(schedule.default_armed),
                None if schedule.override_armed is None else int(schedule.override_armed),
                schedule.override_until.isoformat() if schedule.override_until else None,
            ),
        )

    def create_event_with_outbox(
        self,
        event: Event,
        *,
        channel: str = "webhook",
        channels: Sequence[str] | None = None,
        actor: str = "system",
        enqueue: bool = True,
    ) -> bool:
        """Persist one event and enqueue one delivery row per enabled channel.

        ``channel`` remains the backwards-compatible single-channel form.  New
        callers can pass ``channels`` to fan the same event out to several
        locally configured adapters without duplicating the event record.
        """
        connection = self._connection_or_raise()
        raw_channels = (
            (channels,)
            if isinstance(channels, str)
            else (channels if channels is not None else (channel,))
        )
        normalized_channels: list[str] = []
        for value in raw_channels:
            normalized = str(value).strip().lower()
            if not normalized:
                raise ValueError("notification channel must not be empty")
            if normalized not in normalized_channels:
                normalized_channels.append(normalized)
        if not normalized_channels and enqueue:
            raise ValueError("at least one notification channel is required")
        payload = {
            "event_id": event.id,
            "dedup_key": event.dedup_key,
            "level": event.level,
            "rule_id": event.rule_id,
            "channel": event.channel_id,
            "stream_id": event.stream_id or event.channel_id,
            "site_id": event.site_id,
            "area_id": event.area_id,
            "camera_id": event.camera_id,
            "entity": {
                "site_id": event.site_id,
                "area_id": event.area_id,
                "camera_id": event.camera_id,
                "stream_id": event.stream_id or event.channel_id,
            },
            "target": dict(event.target),
            "occurred_at": event.occurred_at.isoformat(),
            "nvr_time": event.nvr_time.isoformat() if event.nvr_time else None,
            "clock_delta_ms": event.clock_delta_ms,
            "snapshot_uri": event.snapshot_uri,
            "clip_uri": event.clip_uri,
            "clip_status": event.clip_status,
            "status": event.status.value,
            "device_id": event.device_id,
            "source": str(event.source),
            "source_event_id": event.source_event_id,
            "native": event.native,
            "analysis_policy": str(event.analysis_policy),
            "correlation_key": event.correlation_key,
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with connection:
            duplicate_window = max(1.0, float(event.dedup_window_seconds))
            cross_source = connection.execute(
                """SELECT id FROM event
                   WHERE correlation_key = ? AND source != ?
                     AND ABS(julianday(occurred_at) - julianday(?)) * 86400 <= ?
                   LIMIT 1""",
                (
                    event.correlation_key,
                    str(event.source),
                    event.occurred_at.isoformat(),
                    duplicate_window,
                ),
            ).fetchone()
            if cross_source is not None:
                return False
            inserted = connection.execute(
                """INSERT OR IGNORE INTO event
                (id, dedup_key, rule_id, channel_id, stream_id, site_id, area_id, camera_id,
                 level, occurred_at, nvr_time, clock_delta_ms, snapshot_uri, clip_uri,
                 clip_status, status, payload, device_id, source, source_event_id, native,
                 analysis_policy, correlation_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.dedup_key,
                    event.rule_id,
                    event.channel_id,
                    event.stream_id or event.channel_id,
                    event.site_id,
                    event.area_id,
                    event.camera_id,
                    event.level,
                    event.occurred_at.isoformat(),
                    event.nvr_time.isoformat() if event.nvr_time else None,
                    event.clock_delta_ms,
                    event.snapshot_uri,
                    event.clip_uri,
                    event.clip_status,
                    event.status.value,
                    payload_json,
                    event.device_id,
                    str(event.source),
                    event.source_event_id,
                    int(event.native),
                    str(event.analysis_policy),
                    event.correlation_key,
                ),
            )
            if inserted.rowcount != 1:
                return False
            if enqueue:
                connection.executemany(
                    """INSERT INTO notification_outbox
                    (id, event_id, channel, payload_hash, status)
                    VALUES (lower(hex(randomblob(16))), ?, ?, ?, 'pending')""",
                    [(event.id, value, _sha256(payload_json)) for value in normalized_channels],
                )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                VALUES (?, 'event.created', ?, 'success', ?, ?)""",
                (actor, event.id, event.occurred_at.isoformat(), payload_json),
            )
        return True

    def create_event(self, event: Event, *, actor: str = "system") -> bool:
        """Persist an event without creating notification outbox rows."""
        return self.create_event_with_outbox(event, actor=actor, enqueue=False)

    def escalate_overdue_events(
        self,
        *,
        channels: Sequence[str],
        now: datetime | None = None,
        destination_channels: Mapping[str, str] | None = None,
        actor: str = "system",
    ) -> int:
        """Re-enqueue open events whose acknowledgement deadline has elapsed."""
        normalized_channels: list[str] = []
        for channel in channels:
            value = str(channel).strip().lower()
            if value and value not in normalized_channels:
                normalized_channels.append(value)
        destination_mapping = dict(destination_channels or {})
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("escalation clock must be timezone-aware")
        connection = self._connection_or_raise()
        rows = connection.execute(
            """SELECT e.*, r.ack_deadline_seconds, r.escalation_level, r.actions, r.destination_ids
               FROM event AS e
               JOIN detection_rule AS r ON r.id = e.rule_id
               WHERE e.status = 'open' AND e.escalated_at IS NULL
                 AND r.ack_deadline_seconds > 0
               ORDER BY e.occurred_at"""
        ).fetchall()
        escalated = 0
        with connection:
            for row in rows:
                try:
                    occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
                except ValueError:
                    continue
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
                deadline = float(row["ack_deadline_seconds"] or 0)
                if (current - occurred_at).total_seconds() < deadline:
                    continue
                new_level = str(row["escalation_level"] or row["level"])
                updated = connection.execute(
                    """UPDATE event SET escalated_at = ?, level = ?
                       WHERE id = ? AND escalated_at IS NULL""",
                    (current.isoformat(), new_level, row["id"]),
                )
                if updated.rowcount != 1:
                    continue
                try:
                    actions = set(json.loads(row["actions"] or "[]"))
                except (TypeError, json.JSONDecodeError):
                    actions = {"event"}
                if "notify" in actions:
                    try:
                        destination_ids = tuple(json.loads(row["destination_ids"] or "[]"))
                    except (TypeError, json.JSONDecodeError):
                        destination_ids = ()
                    if destination_ids:
                        row_channels = [
                            destination_mapping[str(destination_id)]
                            for destination_id in destination_ids
                            if str(destination_id) in destination_mapping
                        ]
                    else:
                        row_channels = list(normalized_channels)
                    row_channels = list(dict.fromkeys(value for value in row_channels if value))
                    if row_channels:
                        payload_hash = _sha256(str(row["payload"]))
                        connection.executemany(
                            """INSERT INTO notification_outbox
                            (id, event_id, channel, payload_hash, status)
                            VALUES (lower(hex(randomblob(16))), ?, ?, ?, 'pending')""",
                            [(row["id"], channel, payload_hash) for channel in row_channels],
                        )
                connection.execute(
                    """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                       VALUES (?, 'event.escalated', ?, 'success', ?, ?)""",
                    (
                        actor,
                        row["id"],
                        current.isoformat(),
                        f"deadline={deadline:g}; level={new_level}",
                    ),
                )
                escalated += 1
        return escalated

    def list_events(self) -> list[sqlite3.Row]:
        return list(self._connection_or_raise().execute("SELECT * FROM event ORDER BY occurred_at"))

    def list_audit(self, *, limit: int = 1000) -> list[sqlite3.Row]:
        """Return recent audit records for local review/export."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("audit limit must be a positive integer")
        return list(
            self._connection_or_raise().execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            )
        )

    def create_incident(self, incident: Incident, *, actor: str = "system") -> bool:
        """Persist one operator-facing incident and its audit trail."""
        if not isinstance(incident, Incident):
            raise TypeError("incident must be an Incident")
        if not actor.strip():
            raise ValueError("incident actor is required")
        connection = self._connection_or_raise()
        with connection:
            try:
                connection.execute(
                    """INSERT INTO incident(
                           id, title, description, severity, status, site_id, area_id,
                           camera_id, site_name_snapshot, area_name_snapshot,
                           camera_name_snapshot,
                           source, reporter, occurred_at, created_at,
                           updated_at, ai_summary, metadata
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        incident.id,
                        incident.title,
                        incident.description,
                        incident.severity.value,
                        incident.status.value,
                        incident.site_id,
                        incident.area_id,
                        incident.camera_id,
                        incident.site_name_snapshot,
                        incident.area_name_snapshot,
                        incident.camera_name_snapshot,
                        incident.source.value,
                        incident.reporter,
                        incident.occurred_at.astimezone(UTC).isoformat(),
                        incident.created_at.astimezone(UTC).isoformat(),
                        incident.updated_at.astimezone(UTC).isoformat(),
                        incident.ai_summary,
                        json.dumps(dict(incident.metadata), ensure_ascii=False, sort_keys=True),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'incident.created', ?, 'success', ?, ?)""",
                (
                    actor,
                    incident.id,
                    datetime.now(UTC).isoformat(),
                    f"severity={incident.severity.value}; status={incident.status.value}",
                ),
            )
        return True

    def list_incidents(
        self,
        *,
        status: IncidentStatus | str | None = None,
        severity: IncidentSeverity | str | None = None,
        site_id: str | None = None,
        area_id: str | None = None,
        camera_id: str | None = None,
        query: str = "",
        limit: int = 500,
    ) -> list[Incident]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("incident limit must be a positive integer")
        clauses: list[str] = []
        values: list[object] = []
        for column, value in (
            ("status", status),
            ("severity", severity),
            ("site_id", site_id),
            ("area_id", area_id),
            ("camera_id", camera_id),
        ):
            if value is None:
                continue
            normalized = (
                value.value if isinstance(value, (IncidentStatus, IncidentSeverity)) else str(value)
            )
            clauses.append(f"{column} = ?")
            values.append(normalized)
        if query.strip():
            clauses.append(
                "(title LIKE ? OR description LIKE ? OR ai_summary LIKE ? OR id LIKE ?"
                " OR reporter LIKE ? OR area_name_snapshot LIKE ? OR camera_name_snapshot LIKE ?"
                " OR source LIKE ? OR severity LIKE ? OR status LIKE ?)"
            )
            pattern = f"%{query.strip()}%"
            values.extend((pattern,) * 10)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        rows = self._connection_or_raise().execute(
            f"SELECT * FROM incident {where} ORDER BY occurred_at DESC LIMIT ?", values
        )
        return [_incident_from_row(row) for row in rows]

    def list_operational_incidents(self, *, recent_limit: int = 100) -> list[Incident]:
        """Return every unresolved incident plus a bounded recent history."""
        if isinstance(recent_limit, bool) or not isinstance(recent_limit, int) or recent_limit < 1:
            raise ValueError("incident recent limit must be a positive integer")
        rows = self._connection_or_raise().execute(
            """SELECT * FROM incident
               WHERE status IN ('open', 'acknowledged')
                  OR id IN (
                      SELECT id FROM incident
                      ORDER BY occurred_at DESC
                      LIMIT ?
                  )
               ORDER BY occurred_at DESC""",
            (recent_limit,),
        )
        return [_incident_from_row(row) for row in rows]

    def get_incident(self, incident_id: str) -> Incident | None:
        if not incident_id.strip():
            raise ValueError("incident_id is required")
        row = (
            self._connection_or_raise()
            .execute("SELECT * FROM incident WHERE id = ?", (incident_id,))
            .fetchone()
        )
        return _incident_from_row(row) if row is not None else None

    def record_incident_notification(
        self,
        incident_id: str,
        channel: str,
        *,
        success: bool,
        detail: str = "",
    ) -> None:
        """Persist the latest delivery state for one incident/channel."""
        if not incident_id.strip() or not channel.strip():
            raise ValueError("incident_id and channel are required")
        connection = self._connection_or_raise()
        now = datetime.now(UTC).isoformat()
        with connection:
            incident = connection.execute(
                "SELECT 1 FROM incident WHERE id = ?",
                (incident_id,),
            ).fetchone()
            if incident is None:
                raise KeyError(f"incident not found: {incident_id}")
            connection.execute(
                """INSERT INTO incident_notification_delivery(
                       incident_id, channel, status, attempts, detail, created_at, updated_at
                   ) VALUES (?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(incident_id, channel) DO UPDATE SET
                     status=excluded.status,
                     attempts=incident_notification_delivery.attempts + 1,
                     detail=excluded.detail,
                     updated_at=excluded.updated_at""",
                (
                    incident_id,
                    channel.strip().lower(),
                    "sent" if success else "failed",
                    detail[:1000],
                    now,
                    now,
                ),
            )

    def list_incident_notifications(self, incident_id: str) -> list[dict[str, Any]]:
        if not incident_id.strip():
            raise ValueError("incident_id is required")
        return [
            {
                "channel": str(row["channel"]),
                "status": str(row["status"]),
                "attempts": int(row["attempts"]),
                "detail": str(row["detail"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in self._connection_or_raise().execute(
                """SELECT channel, status, attempts, detail, updated_at
                   FROM incident_notification_delivery
                   WHERE incident_id = ? ORDER BY id""",
                (incident_id,),
            )
        ]

    def failed_incident_notification_channels(self, incident_id: str) -> tuple[str, ...]:
        return tuple(
            item["channel"]
            for item in self.list_incident_notifications(incident_id)
            if item["status"] == "failed"
        )

    def incident_notification_retry_candidates(
        self,
        *,
        max_attempts: int | None = None,
        min_age_seconds: float = 60.0,
        limit: int = 20,
        backoff_seconds: float = 60.0,
        max_backoff_seconds: float = 3600.0,
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return failed channels grouped by incident when their backoff elapsed.

        A safety alert must not be dropped because the network was down for a
        while, so the attempt limit is optional and the wait grows with the
        number of failures.
        """
        if limit < 1 or min_age_seconds < 0:
            raise ValueError("retry limits must be positive and min_age_seconds non-negative")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be positive when provided")
        if backoff_seconds <= 0 or max_backoff_seconds < backoff_seconds:
            raise ValueError("retry backoff must be positive and not shrink")
        now = datetime.now(UTC)
        attempt_clause = "" if max_attempts is None else " AND attempts < ?"
        parameters: list[object] = []
        if max_attempts is not None:
            parameters.append(max_attempts)
        parameters.append(limit)
        rows = self._connection_or_raise().execute(
            """SELECT d.incident_id, d.channel, d.updated_at, d.attempts
               FROM incident_notification_delivery AS d
               JOIN incident AS i ON i.id = d.incident_id
               WHERE d.status = 'failed'
                 AND i.status IN ('open', 'acknowledged')"""
            + attempt_clause
            + """
               ORDER BY d.updated_at, d.id LIMIT ?""",
            parameters,
        )
        grouped: dict[str, list[str]] = {}
        for row in rows:
            try:
                updated = datetime.fromisoformat(str(row["updated_at"]))
            except ValueError:
                updated = datetime.min.replace(tzinfo=UTC)
            attempts = max(int(row["attempts"] or 0), 1)
            wait = float(min_age_seconds) + min(
                backoff_seconds * (2 ** (attempts - 1)) - backoff_seconds,
                max_backoff_seconds,
            )
            if updated > now - timedelta(seconds=wait):
                continue
            grouped.setdefault(str(row["incident_id"]), []).append(str(row["channel"]))
        return tuple((incident_id, tuple(channels)) for incident_id, channels in grouped.items())

    def update_incident_status(
        self,
        incident_id: str,
        status: IncidentStatus | str,
        *,
        actor: str = "operator",
        note: str = "",
    ) -> bool:
        if not incident_id.strip():
            raise ValueError("incident_id is required")
        if not actor.strip():
            raise ValueError("incident actor is required")
        try:
            normalized = (
                status if isinstance(status, IncidentStatus) else IncidentStatus(str(status))
            )
        except ValueError as exc:
            raise ValueError("incident status is invalid") from exc
        connection = self._connection_or_raise()
        row = connection.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return False
        current = IncidentStatus(str(row["status"]))
        if normalized == current:
            return False
        if normalized not in ALLOWED_INCIDENT_STATUS_TRANSITIONS[current]:
            raise ValueError(
                f"险情状态不能从“{STATUS_LABELS[current]}”直接变更为“{STATUS_LABELS[normalized]}”；"
                "已关闭险情请先重新打开"
            )
        now = datetime.now(UTC).isoformat()
        with connection:
            result = connection.execute(
                "UPDATE incident SET status = ?, updated_at = ? WHERE id = ?",
                (normalized.value, now, incident_id),
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'incident.status.changed', ?, 'success', ?, ?)""",
                (
                    actor,
                    incident_id,
                    now,
                    json.dumps({"status": normalized.value, "note": note}, ensure_ascii=False),
                ),
            )
        return True

    def assign_incident_location(
        self,
        incident_id: str,
        *,
        area_id: str | None = None,
        camera_id: str | None = None,
        actor: str = "operator",
    ) -> bool:
        """Attach an area and camera to an incident and refresh its snapshots.

        Operators report incidents from the field, so the place is often
        unknown at first and only becomes clear during the follow-up.  The
        stored snapshot keeps the exported report pointing at the right
        place even after the area is renamed later.
        """
        if not incident_id.strip():
            raise ValueError("incident_id is required")
        if not actor.strip():
            raise ValueError("incident actor is required")
        connection = self._connection_or_raise()
        row = connection.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return False
        target_area = area_id if area_id is not None else str(row["area_id"] or "")
        target_camera = camera_id if camera_id is not None else str(row["camera_id"] or "")
        area_name = ""
        site_id = str(row["site_id"] or "")
        if target_area:
            area_row = connection.execute(
                "SELECT name, site_id FROM area WHERE id = ?", (target_area,)
            ).fetchone()
            if area_row is None:
                raise ValueError(f"area {target_area!r} does not exist")
            area_name = str(area_row["name"])
            site_id = str(area_row["site_id"])
        camera_name = ""
        if target_camera:
            camera_row = connection.execute(
                "SELECT name, area_id FROM camera_device WHERE id = ?", (target_camera,)
            ).fetchone()
            if camera_row is None:
                raise ValueError(f"camera {target_camera!r} does not exist")
            camera_name = str(camera_row["name"])
            camera_area = str(camera_row["area_id"])
            if target_area and camera_area != target_area:
                raise ValueError("摄像头不属于所选区域")
            if not target_area:
                target_area = camera_area
                area_row = connection.execute(
                    "SELECT name, site_id FROM area WHERE id = ?", (camera_area,)
                ).fetchone()
                if area_row is not None:
                    area_name = str(area_row["name"])
                    site_id = str(area_row["site_id"])
        now = datetime.now(UTC).isoformat()
        with connection:
            result = connection.execute(
                """UPDATE incident
                   SET area_id = ?, camera_id = ?, site_id = ?,
                       area_name_snapshot = ?, camera_name_snapshot = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    target_area or None,
                    target_camera or None,
                    site_id or None,
                    area_name,
                    camera_name,
                    now,
                    incident_id,
                ),
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'incident.location.changed', ?, 'success', ?, ?)""",
                (
                    actor,
                    incident_id,
                    now,
                    json.dumps(
                        {
                            "area_id": target_area,
                            "camera_id": target_camera,
                            "area_name": area_name,
                            "camera_name": camera_name,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        return True

    def list_incident_audit(self, incident_id: str, *, limit: int = 20) -> list[dict[str, str]]:
        """Return incident creation and status-change audit entries."""
        if not incident_id.strip():
            raise ValueError("incident_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("incident audit limit must be a positive integer")
        rows = self._connection_or_raise().execute(
            """SELECT actor, action, result, ts, detail
               FROM audit_log
               WHERE resource = ? AND action IN (?, ?, ?)
               ORDER BY id DESC LIMIT ?""",
            (
                incident_id,
                "incident.created",
                "incident.status.changed",
                "incident.location.changed",
                limit,
            ),
        )
        return [
            {
                "actor": str(row["actor"]),
                "action": str(row["action"]),
                "result": str(row["result"]),
                "ts": str(row["ts"]),
                "detail": str(row["detail"]),
            }
            for row in rows
        ]

    def append_assistant_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        provider: str = "offline",
        citations: Sequence[str] = (),
        conversation_title: str = "现场助手",
    ) -> str:
        if not conversation_id.strip():
            raise ValueError("conversation_id is required")
        normalized_role = str(role).strip().lower()
        if normalized_role not in {"user", "assistant", "system"}:
            raise ValueError("assistant message role is invalid")
        if not str(content).strip():
            raise ValueError("assistant message content is required")
        now = datetime.now(UTC).isoformat()
        message_id = f"msg-{uuid4().hex}"
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT INTO assistant_conversation(id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
                (conversation_id, conversation_title, now, now),
            )
            connection.execute(
                """INSERT INTO assistant_message(
                       id, conversation_id, role, content, provider, citations, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    conversation_id,
                    normalized_role,
                    str(content).strip(),
                    str(provider).strip() or "offline",
                    json.dumps(list(citations), ensure_ascii=False),
                    now,
                ),
            )
        return message_id

    def list_assistant_messages(
        self, conversation_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        if not conversation_id.strip():
            raise ValueError("conversation_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("assistant message limit must be a positive integer")
        rows = self._connection_or_raise().execute(
            """SELECT * FROM assistant_message
               WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?""",
            (conversation_id, limit),
        )
        messages = []
        for row in reversed(list(rows)):
            messages.append(
                {
                    "id": str(row["id"]),
                    "conversation_id": str(row["conversation_id"]),
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "provider": str(row["provider"]),
                    "citations": json.loads(row["citations"] or "[]"),
                    "created_at": str(row["created_at"]),
                }
            )
        return messages

    def search_events(
        self,
        *,
        query: str = "",
        level: str | None = None,
        channel_id: str | None = None,
        stream_id: str | None = None,
        site_id: str | None = None,
        area_id: str | None = None,
        camera_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        """Return event records for the control-panel search view.

        Search is deliberately kept at the storage boundary so the UI never
        interpolates user text into SQL.  The payload contains target labels,
        business metadata and media paths, making it useful for a small local review
        queue without another indexing service.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("event search limit must be a positive integer")
        clauses: list[str] = []
        values: list[object] = []
        if query.strip():
            clauses.append(
                "(id LIKE ? OR channel_id LIKE ? OR stream_id LIKE ? OR site_id LIKE ? "
                "OR area_id LIKE ? OR camera_id LIKE ? OR level LIKE ? OR payload LIKE ?)"
            )
            pattern = f"%{query.strip()}%"
            values.extend((pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern))
        if level:
            clauses.append("level = ?")
            values.append(level)
        if channel_id:
            clauses.append("channel_id = ?")
            values.append(channel_id)
        if stream_id:
            clauses.append("COALESCE(stream_id, channel_id) = ?")
            values.append(stream_id)
        if site_id:
            clauses.append("site_id = ?")
            values.append(site_id)
        if area_id:
            clauses.append("area_id = ?")
            values.append(area_id)
        if camera_id:
            clauses.append("camera_id = ?")
            values.append(camera_id)
        if start is not None:
            clauses.append("occurred_at >= ?")
            values.append(_timestamp(start))
        if end is not None:
            clauses.append("occurred_at <= ?")
            values.append(_timestamp(end))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        return list(
            self._connection_or_raise().execute(
                f"SELECT * FROM event {where} ORDER BY occurred_at DESC LIMIT ?", values
            )
        )

    def get_event(self, event_id: str) -> sqlite3.Row | None:
        if not event_id:
            raise ValueError("event_id is required")
        return (
            self._connection_or_raise()
            .execute("SELECT * FROM event WHERE id = ?", (event_id,))
            .fetchone()
        )

    def update_event_media(
        self,
        event_id: str,
        *,
        snapshot_uri: str | None = None,
        clip_uri: str | None = None,
        clip_status: str | None = None,
        actor: str = "system",
    ) -> bool:
        """Attach locally generated snapshot/clip paths to an event."""

        if not event_id:
            raise ValueError("event_id is required")
        if not actor.strip():
            raise ValueError("media actor is required")
        connection = self._connection_or_raise()
        row = connection.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return False
        payload = json.loads(row["payload"])
        assignments: list[str] = []
        values: list[object] = []
        if snapshot_uri is not None:
            assignments.append("snapshot_uri = ?")
            values.append(snapshot_uri)
            payload["snapshot_uri"] = snapshot_uri
        if clip_uri is not None:
            assignments.append("clip_uri = ?")
            values.append(clip_uri)
            payload["clip_uri"] = clip_uri
        if clip_status is not None:
            assignments.append("clip_status = ?")
            values.append(clip_status)
            payload["clip_status"] = clip_status
        if not assignments:
            return True
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        assignments.append("payload = ?")
        values.append(payload_json)
        values.append(event_id)
        with connection:
            result = connection.execute(
                f"UPDATE event SET {', '.join(assignments)} WHERE id = ?", values
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """UPDATE notification_outbox
                   SET payload_hash = ?
                   WHERE event_id = ? AND status IN ('pending', 'retry', 'sending')""",
                (_sha256(payload_json), event_id),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'event.media.updated', ?, 'success', ?, ?)""",
                (actor, event_id, datetime.now(UTC).isoformat(), payload_json),
            )
        return True

    def clear_event_media(
        self,
        event_id: str,
        *,
        snapshot: bool = False,
        clip: bool = False,
        actor: str = "system",
    ) -> bool:
        """Clear expired media references while retaining the event record."""

        if not event_id:
            raise ValueError("event_id is required")
        if not snapshot and not clip:
            raise ValueError("at least one media field must be selected")
        if not actor.strip():
            raise ValueError("media actor is required")
        connection = self._connection_or_raise()
        row = connection.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return False
        payload = json.loads(row["payload"] or "{}")
        clear_snapshot = snapshot and bool(row["snapshot_uri"])
        clear_clip = clip and bool(row["clip_uri"])
        if not clear_snapshot and not clear_clip:
            return True
        assignments: list[str] = []
        values: list[object] = []
        cleared: list[str] = []
        if clear_snapshot:
            assignments.append("snapshot_uri = NULL")
            payload["snapshot_uri"] = None
            cleared.append("snapshot")
        if clear_clip:
            assignments.append("clip_uri = NULL")
            payload["clip_uri"] = None
            cleared.append("clip")
        remaining_snapshot = bool(row["snapshot_uri"]) and not clear_snapshot
        remaining_clip = bool(row["clip_uri"]) and not clear_clip
        clip_status = (
            "ready" if remaining_clip else "snapshot_only" if remaining_snapshot else "expired"
        )
        assignments.append("clip_status = ?")
        values.append(clip_status)
        payload["clip_status"] = clip_status
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        assignments.append("payload = ?")
        values.append(payload_json)
        values.append(event_id)
        with connection:
            result = connection.execute(
                f"UPDATE event SET {', '.join(assignments)} WHERE id = ?", values
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """UPDATE notification_outbox
                   SET payload_hash = ?
                   WHERE event_id = ? AND status IN ('pending', 'retry', 'sending')""",
                (_sha256(payload_json), event_id),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'event.media.expired', ?, 'success', ?, ?)""",
                (actor, event_id, datetime.now(UTC).isoformat(), ",".join(cleared)),
            )
        return True

    def update_event_status(
        self,
        event_id: str,
        status: EventStatus | str,
        *,
        actor: str = "system",
        detail: str = "",
    ) -> bool:
        """Transition an event and append an immutable review audit entry."""
        if not event_id:
            raise ValueError("event_id is required")
        try:
            status_text = (
                status.value if isinstance(status, EventStatus) else EventStatus(str(status)).value
            )
        except ValueError as exc:
            raise ValueError(
                "event status must be open, confirmed, false_positive, or closed"
            ) from exc
        if not actor.strip():
            raise ValueError("event actor is required")
        connection = self._connection_or_raise()
        row = connection.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return False
        payload = json.loads(row["payload"] or "{}")
        payload["status"] = status_text
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with connection:
            result = connection.execute(
                "UPDATE event SET status = ?, payload = ? WHERE id = ?",
                (status_text, payload_json, event_id),
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, ?, ?, 'success', ?, ?)""",
                (
                    actor,
                    f"event.status.{status_text}",
                    event_id,
                    datetime.now(UTC).isoformat(),
                    detail,
                ),
            )
        return True

    def list_pending_outbox(self) -> list[sqlite3.Row]:
        return list(
            self._connection_or_raise().execute(
                """SELECT * FROM notification_outbox
                   WHERE status IN ('pending', 'retry', 'sending')
                   ORDER BY rowid"""
            )
        )

    def list_outbox(self, *, limit: int = 500, status: str | None = None) -> list[sqlite3.Row]:
        """Return notification queue rows for the local operations console."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("outbox limit must be a positive integer")
        params: list[object] = []
        where = ""
        if status:
            if status not in {"pending", "retry", "sending", "sent", "failed"}:
                raise ValueError("unsupported outbox status")
            where = "WHERE o.status = ?"
            params.append(status)
        params.append(limit)
        return list(
            self._connection_or_raise().execute(
                f"""SELECT o.*, e.level AS event_level, e.occurred_at AS event_occurred_at,
                          e.channel_id AS event_channel_id, e.stream_id AS event_stream_id,
                          e.site_id AS event_site_id, e.area_id AS event_area_id,
                          e.camera_id AS event_camera_id
                   FROM notification_outbox AS o
                   LEFT JOIN event AS e ON e.id = o.event_id
                   {where}
                   ORDER BY COALESCE(o.next_retry_at, o.sent_at, e.occurred_at) DESC, o.rowid DESC
                   LIMIT ?""",
                params,
            )
        )

    def outbox_counts(self) -> dict[str, int]:
        """Return queue counts grouped by delivery status."""
        rows = self._connection_or_raise().execute(
            "SELECT status, COUNT(*) AS count FROM notification_outbox GROUP BY status"
        )
        return {str(row["status"]): int(row["count"]) for row in rows}

    def requeue_outbox(self, outbox_id: str, *, actor: str = "ui") -> bool:
        """Put one failed notification back into the due retry queue."""
        if not outbox_id:
            raise ValueError("outbox_id is required")
        if not actor.strip():
            raise ValueError("outbox actor is required")
        connection = self._connection_or_raise()
        now = _timestamp(None)
        with connection:
            result = connection.execute(
                """UPDATE notification_outbox
                   SET status = 'retry', next_retry_at = ?, last_error = NULL
                   WHERE id = ? AND status = 'failed'""",
                (now, outbox_id),
            )
            if result.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'notification.requeue', ?, 'success', ?, ?)""",
                (actor, outbox_id, now, "failed notification requeued"),
            )
        return True

    def claim_pending_outbox(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
        lease_seconds: float = 60.0,
    ) -> list[sqlite3.Row]:
        """Atomically lease due notifications for one delivery worker.

        A lease prevents two workers from sending the same row concurrently.
        ``recover_expired_outbox`` makes rows visible again after a crashed
        worker.  Attempts are incremented at claim time so a failed process is
        still observable in the audit data.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("outbox limit must be a positive integer")
        if not isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("outbox lease_seconds must be positive")
        connection = self._connection_or_raise()
        now_text = _timestamp(now)
        lease_until = _timestamp(_parse_timestamp(now_text) + timedelta(seconds=lease_seconds))
        # Lock before selecting IDs.  A deferred transaction would allow two
        # workers to read the same pending rows before either UPDATE commits.
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = list(
                connection.execute(
                    """SELECT id FROM notification_outbox
                       WHERE status IN ('pending', 'retry')
                         AND (next_retry_at IS NULL OR next_retry_at <= ?)
                       ORDER BY rowid LIMIT ?""",
                    (now_text, limit),
                )
            )
            ids = [str(row[0]) for row in rows]
            if not ids:
                connection.commit()
                return []
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"""UPDATE notification_outbox
                    SET status = 'sending', attempts = attempts + 1,
                        next_retry_at = ?, last_error = NULL
                    WHERE id IN ({placeholders})""",
                (lease_until, *ids),
            )
            claimed = list(
                connection.execute(
                    f"SELECT * FROM notification_outbox WHERE id IN ({placeholders}) ORDER BY rowid",
                    ids,
                )
            )
            connection.commit()
            return claimed
        except Exception:
            connection.rollback()
            raise

    def recover_expired_outbox(self, *, now: datetime | None = None) -> int:
        """Return abandoned ``sending`` rows to the retry queue."""
        connection = self._connection_or_raise()
        now_text = _timestamp(now)
        with connection:
            result = connection.execute(
                """UPDATE notification_outbox
                   SET status = 'retry', next_retry_at = ?
                   WHERE status = 'sending' AND next_retry_at IS NOT NULL AND next_retry_at <= ?""",
                (now_text, now_text),
            )
        return result.rowcount

    def mark_outbox_sent(self, outbox_id: str, *, sent_at: datetime | None = None) -> bool:
        """Mark one leased notification as delivered."""
        if not outbox_id:
            raise ValueError("outbox_id is required")
        connection = self._connection_or_raise()
        with connection:
            result = connection.execute(
                """UPDATE notification_outbox
                   SET status = 'sent', sent_at = ?, next_retry_at = NULL, last_error = NULL
                   WHERE id = ? AND status = 'sending'""",
                (_timestamp(sent_at), outbox_id),
            )
        return result.rowcount == 1

    def mark_outbox_failed(
        self,
        outbox_id: str,
        error: str,
        *,
        retry_at: datetime | None = None,
    ) -> bool:
        """Record a delivery failure and optionally schedule another attempt."""
        if not outbox_id:
            raise ValueError("outbox_id is required")
        if not error:
            raise ValueError("outbox error is required")
        connection = self._connection_or_raise()
        status = "retry" if retry_at is not None else "failed"
        retry_text = _timestamp(retry_at) if retry_at is not None else None
        with connection:
            result = connection.execute(
                """UPDATE notification_outbox
                   SET status = ?, next_retry_at = ?, last_error = ?
                   WHERE id = ? AND status = 'sending'""",
                (status, retry_text, error[-1000:], outbox_id),
            )
        return result.rowcount == 1

    def integrity_check(self) -> str:
        return str(self._connection_or_raise().execute("PRAGMA integrity_check").fetchone()[0])

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent SQLite snapshot using SQLite's online backup API.

        The destination is written through a temporary sibling file and then
        replaced atomically, so a failed backup cannot leave a half-written
        configuration file that looks usable to an operator.
        """
        if self.path == ":memory:":
            raise ValueError("in-memory databases cannot be backed up")
        source = Path(self.path).expanduser().resolve()
        target = Path(destination).expanduser().resolve()
        if source == target:
            raise ValueError("backup destination must differ from the active database")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.unlink(missing_ok=True)
        connection = self._connection_or_raise()
        try:
            backup = sqlite3.connect(temporary)
            try:
                with backup:
                    connection.backup(backup)
                    result = str(backup.execute("PRAGMA integrity_check").fetchone()[0])
                    if result != "ok":
                        raise sqlite3.DatabaseError(f"backup integrity check failed: {result}")
            finally:
                backup.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def save_device_credential(
        self,
        credential_ref: str,
        ciphertext: str,
        *,
        actor: str = "system",
    ) -> None:
        if not credential_ref.strip() or not ciphertext.strip():
            raise ValueError("credential_ref and ciphertext are required")
        now = datetime.now(UTC).isoformat()
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT INTO device_credential(credential_ref, ciphertext, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(credential_ref) DO UPDATE SET ciphertext=excluded.ciphertext,
                     updated_at=excluded.updated_at""",
                (credential_ref, ciphertext, now, now),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'device.credential.updated', ?, 'success', ?, 'secret not logged')""",
                (actor, credential_ref, now),
            )

    def get_device_credential_blob(self, credential_ref: str) -> str | None:
        row = (
            self._connection_or_raise()
            .execute(
                "SELECT ciphertext FROM device_credential WHERE credential_ref = ?",
                (credential_ref,),
            )
            .fetchone()
        )
        return str(row["ciphertext"]) if row else None

    def delete_device_credential(self, credential_ref: str, *, actor: str = "system") -> bool:
        connection = self._connection_or_raise()
        with connection:
            result = connection.execute(
                "DELETE FROM device_credential WHERE credential_ref = ?", (credential_ref,)
            )
            if result.rowcount:
                connection.execute(
                    """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                       VALUES (?, 'device.credential.deleted', ?, 'success', ?, 'secret deleted')""",
                    (actor, credential_ref, datetime.now(UTC).isoformat()),
                )
            return bool(result.rowcount)

    def set_device_subscription(
        self,
        device_id: str,
        *,
        source: str,
        state: str,
        detail: str = "",
        last_event_at: datetime | None = None,
        actor: str = "device-service",
    ) -> None:
        if not device_id or not source.strip() or not state.strip():
            raise ValueError("device_id, source and state are required")
        now = datetime.now(UTC).isoformat()
        safe_detail = _redact_url(str(detail))[-2000:]
        connection = self._connection_or_raise()
        with connection:
            connection.execute(
                """INSERT INTO device_subscription
                   (device_id, source, state, detail, updated_at, last_event_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET source=excluded.source,
                     state=excluded.state, detail=excluded.detail,
                     updated_at=excluded.updated_at,
                     last_event_at=COALESCE(excluded.last_event_at, device_subscription.last_event_at)""",
                (
                    device_id,
                    source,
                    state,
                    safe_detail,
                    now,
                    last_event_at.astimezone(UTC).isoformat() if last_event_at else None,
                ),
            )
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'device.subscription.changed', ?, 'success', ?, ?)""",
                (actor, device_id, now, f"source={source}; state={state}; detail={safe_detail}"),
            )

    def list_device_subscriptions(self) -> dict[str, dict[str, Any]]:
        return {
            str(row["device_id"]): {
                "source": str(row["source"]),
                "state": str(row["state"]),
                "detail": str(row["detail"]),
                "updated_at": str(row["updated_at"]),
                "last_event_at": str(row["last_event_at"]) if row["last_event_at"] else None,
            }
            for row in self._connection_or_raise().execute("SELECT * FROM device_subscription")
        }

    def save_recordings(self, device_id: str, recordings: Sequence[Recording]) -> int:
        if not device_id:
            raise ValueError("device_id is required")
        connection = self._connection_or_raise()
        with connection:
            connection.execute("DELETE FROM recording_index WHERE device_id = ?", (device_id,))
            for item in recordings:
                connection.execute(
                    """INSERT OR IGNORE INTO recording_index
                       (id, device_id, channel_id, start_at, end_at, source, description)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        device_id,
                        item.channel_id,
                        item.start_at.astimezone(UTC).isoformat(),
                        item.end_at.astimezone(UTC).isoformat(),
                        item.source,
                        item.description,
                    ),
                )
        return len(recordings)

    def search_recordings(
        self,
        *,
        device_id: str | None = None,
        channel_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("recording search limit must be positive")
        clauses: list[str] = []
        values: list[Any] = []
        if device_id:
            clauses.append("device_id = ?")
            values.append(device_id)
        if channel_id:
            clauses.append("channel_id = ?")
            values.append(channel_id)
        if start_at:
            clauses.append("end_at >= ?")
            values.append(start_at.astimezone(UTC).isoformat())
        if end_at:
            clauses.append("start_at <= ?")
            values.append(end_at.astimezone(UTC).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        return [
            dict(row)
            for row in self._connection_or_raise().execute(
                f"SELECT * FROM recording_index {where} ORDER BY start_at DESC LIMIT ?", values
            )
        ]

    def has_recent_correlated_event(
        self,
        correlation_key: str,
        *,
        within_seconds: float,
        exclude_source: str | None = None,
        at: datetime | None = None,
    ) -> bool:
        if not correlation_key or within_seconds < 0:
            raise ValueError("correlation_key is required and within_seconds must be non-negative")
        clauses = ["correlation_key = ?", "ABS(julianday(occurred_at) - julianday(?)) * 86400 <= ?"]
        values: list[Any] = [correlation_key, _timestamp(at), within_seconds]
        if exclude_source:
            clauses.append("source != ?")
            values.append(exclude_source)
        row = (
            self._connection_or_raise()
            .execute(f"SELECT id FROM event WHERE {' AND '.join(clauses)} LIMIT 1", values)
            .fetchone()
        )
        return row is not None

    def _connection_or_raise(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("SQLiteStore is closed")
        return self._connection


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp(value: datetime | None) -> str:
    timestamp = datetime.now(UTC) if value is None else value
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _rule_to_dict(rule: Rule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "stream_id": rule.stream_id,
        "channel_id": rule.channel_id,
        "site_id": rule.site_id,
        "area_id": rule.area_id,
        "camera_id": rule.camera_id,
        "name": rule.name,
        "native_event_codes": list(rule.native_event_codes),
        "dedup_window_seconds": rule.dedup_window_seconds,
        "actions": list(rule.actions),
        "http_action_url": rule.http_action_url,
        "http_action_timeout": rule.http_action_timeout,
        "ack_deadline_seconds": rule.ack_deadline_seconds,
        "escalation_level": rule.escalation_level,
        "destination_ids": list(rule.destination_ids),
        "assignees": list(rule.assignees),
        "enabled": rule.enabled,
        "publication_status": rule.publication_status,
        "version": rule.version,
    }


def _rule_from_dict(value: Mapping[str, Any]) -> Rule:
    if not isinstance(value, Mapping):
        raise TypeError("configuration rule must be a mapping")
    return Rule(
        id=str(value["id"]),
        channel_id=str(value.get("channel_id") or value.get("stream_id") or ""),
        stream_id=str(value.get("stream_id") or value.get("channel_id") or ""),
        site_id=str(value["site_id"]) if value.get("site_id") is not None else None,
        area_id=str(value["area_id"]) if value.get("area_id") is not None else None,
        camera_id=str(value["camera_id"]) if value.get("camera_id") is not None else None,
        name=str(value.get("name", "")),
        actions=tuple(value.get("actions", ("event", "notify"))),
        http_action_url=str(value.get("http_action_url", "")),
        http_action_timeout=float(value.get("http_action_timeout", 5.0)),
        ack_deadline_seconds=float(value.get("ack_deadline_seconds", 0.0)),
        escalation_level=str(value.get("escalation_level", "")),
        destination_ids=tuple(str(item) for item in value.get("destination_ids", ())),
        assignees=tuple(str(item) for item in value.get("assignees", ())),
        native_event_codes=tuple(str(item) for item in value.get("native_event_codes", ())),
        dedup_window_seconds=float(value.get("dedup_window_seconds", 5.0)),
        enabled=bool(value.get("enabled", True)),
        publication_status=str(value.get("publication_status", "published")),
        version=int(value.get("version", 1)),
    )


def _incident_from_row(row: sqlite3.Row) -> Incident:
    try:
        metadata = json.loads(row["metadata"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return Incident(
        id=str(row["id"]),
        title=str(row["title"]),
        description=str(row["description"] or ""),
        severity=IncidentSeverity(str(row["severity"])),
        status=IncidentStatus(str(row["status"])),
        site_id=str(row["site_id"]) if row["site_id"] else None,
        area_id=str(row["area_id"]) if row["area_id"] else None,
        camera_id=str(row["camera_id"]) if row["camera_id"] else None,
        site_name_snapshot=str(row["site_name_snapshot"] or ""),
        area_name_snapshot=str(row["area_name_snapshot"] or ""),
        camera_name_snapshot=str(row["camera_name_snapshot"] or ""),
        source=IncidentSource(str(row["source"])),
        reporter=str(row["reporter"] or ""),
        occurred_at=_parse_timestamp(str(row["occurred_at"])),
        created_at=_parse_timestamp(str(row["created_at"])),
        updated_at=_parse_timestamp(str(row["updated_at"])),
        ai_summary=str(row["ai_summary"] or ""),
        metadata=metadata if isinstance(metadata, Mapping) else {},
    )


def _redact_url(value: str) -> str:
    return re.sub(r"((?:rtsp|rtsps|https?)://)[^/@\s]+@", r"\1***:***@", value, flags=re.IGNORECASE)


def _endpoint_without_credentials(value: str) -> str:
    return re.sub(r"((?:rtsp|rtsps|https?)://)[^/@\s]+@", r"\1", value, flags=re.IGNORECASE)
