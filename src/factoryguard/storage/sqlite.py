from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

from factoryguard.domain.models import Area, CameraDevice, Event, EventStatus, Rule, RuleType, Site, VideoStream
from factoryguard.domain.schedules import ArmingSchedule, TimeWindow
from factoryguard.domain.topology import EntityRegistry

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
CREATE TABLE IF NOT EXISTS camera_device (
  id TEXT PRIMARY KEY,
  area_id TEXT NOT NULL REFERENCES area(id),
  name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_camera_area ON camera_device(area_id);
CREATE VIEW IF NOT EXISTS camera AS SELECT id, area_id, name, vendor, address, enabled FROM camera_device;
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
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_stream_camera ON video_stream(camera_id);
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
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_rule_stream ON detection_rule(stream_id, enabled);
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
  payload TEXT NOT NULL
);
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
        """Add topology/event columns to databases created by older builds."""
        connection = self._connection_or_raise()
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(event)")}
        additions = {
            "stream_id": "TEXT",
            "site_id": "TEXT",
            "area_id": "TEXT",
            "camera_id": "TEXT",
        }
        with connection:
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE event ADD COLUMN {name} {definition}")
            connection.execute("CREATE INDEX IF NOT EXISTS ix_event_stream_time ON event(stream_id, occurred_at)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_event_entity_time ON event(site_id, area_id, camera_id, occurred_at)"
            )

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
            for camera in topology.cameras:
                self._upsert_camera(connection, camera)
            for stream in topology.streams:
                self._upsert_stream(connection, stream)

    def save_site(self, site: Site, *, actor: str = "system") -> None:
        self._save_topology_entity(
            kind="site",
            entity_id=site.id,
            actor=actor,
            detail={"name": site.name, "description": site.description, "enabled": site.enabled},
            upsert=lambda connection: self._upsert_site(connection, site),
        )

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
            "SELECT camera_id, name, url, role, width, height, fps, model, confidence, enabled "
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
            "model": stream.model,
            "confidence": stream.confidence,
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

    def set_entity_enabled(self, kind: str, entity_id: str, enabled: bool, *, actor: str = "system") -> bool:
        """Enable or disable one topology entity without deleting it."""
        tables = {
            "site": "site",
            "area": "area",
            "camera": "camera_device",
            "stream": "video_stream",
        }
        table = tables.get(kind)
        if table is None:
            raise ValueError("entity kind must be site, area, camera, or stream")
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
                (actor, entity_id, datetime.now(UTC).isoformat(), f"kind={kind}; enabled={int(enabled)}"),
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
        """Load all topology entities and re-validate their parent links."""
        connection = self._connection_or_raise()
        sites = [
            Site(str(row["id"]), str(row["name"]), str(row["description"] or ""), bool(row["enabled"]))
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
        cameras = [
            CameraDevice(
                id=str(row["id"]),
                area_id=str(row["area_id"]),
                name=str(row["name"]),
                vendor=str(row["vendor"] or ""),
                address=str(row["address"] or ""),
                enabled=bool(row["enabled"]),
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
                model=str(row["model"]) if row["model"] is not None else None,
                confidence=float(row["confidence"]),
                enabled=bool(row["enabled"]),
            )
            for row in connection.execute("SELECT * FROM video_stream ORDER BY rowid")
        ]
        return EntityRegistry(sites=sites, areas=areas, cameras=cameras, streams=streams)

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
        if not status.strip():
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
        if status in {"degraded", "offline"} and (previous is None or str(previous["status"]) != status):
            reconnect_count += 1
        last_ok_at = previous["last_ok_at"] if previous is not None else None
        if status in {"ready", "running"}:
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
                (stream_id, status, safe_detail, now, last_ok_at, reconnect_count),
            )
            if previous is None or str(previous["status"]) != status:
                connection.execute(
                    """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                       VALUES (?, 'stream.health.changed', ?, 'success', ?, ?)""",
                    (actor, stream_id, now, f"status={status}; detail={safe_detail}"),
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
            for row in self._connection_or_raise().execute("SELECT * FROM stream_health ORDER BY stream_id")
        }

    def save_rule(self, rule: Rule, *, actor: str = "system") -> None:
        """Insert or update a persisted detection rule and audit the change."""
        if not actor.strip():
            raise ValueError("rule actor is required")
        connection = self._connection_or_raise()
        detail = json.dumps(_rule_to_dict(rule), ensure_ascii=False, sort_keys=True)
        with connection:
            self._upsert_rule(connection, rule)
            connection.execute(
                """INSERT INTO audit_log(actor, action, resource, result, ts, detail)
                   VALUES (?, 'rule.updated', ?, 'success', ?, ?)""",
                (actor, rule.id, datetime.now(UTC).isoformat(), detail),
            )

    def load_rules(self, *, stream_id: str | None = None, enabled_only: bool = False) -> list[Rule]:
        """Load persisted rules, optionally scoped to one stream."""
        connection = self._connection_or_raise()
        clauses: list[str] = []
        values: list[object] = []
        if stream_id:
            clauses.append("stream_id = ?")
            values.append(stream_id)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = connection.execute(f"SELECT * FROM detection_rule {where} ORDER BY rowid", values).fetchall()
        rules: list[Rule] = []
        for row in rows:
            rules.append(
                Rule(
                    id=str(row["id"]),
                    stream_id=str(row["stream_id"]),
                    site_id=str(row["site_id"]) if row["site_id"] is not None else None,
                    area_id=str(row["area_id"]) if row["area_id"] is not None else None,
                    camera_id=str(row["camera_id"]) if row["camera_id"] is not None else None,
                    name=str(row["name"]),
                    type=RuleType(str(row["type"])),
                    geometry=tuple(tuple(point) for point in json.loads(row["geometry"])),
                    target_labels=frozenset(json.loads(row["target_labels"])),
                    min_confidence=float(row["min_confidence"]),
                    min_box_height=float(row["min_box_height"]),
                    confirm_frames=int(row["confirm_frames"]),
                    cooldown_seconds=float(row["cooldown_seconds"]),
                    direction=str(row["direction"]),
                    enabled=bool(row["enabled"]),
                    version=int(row["version"]),
                )
            )
        return rules

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
        row = self._connection_or_raise().execute(
            "SELECT armed FROM stream_arming WHERE stream_id = ?", (stream_id,)
        ).fetchone()
        return bool(row["armed"]) if row is not None else default

    def list_stream_arming(self) -> dict[str, bool]:
        """Return all explicitly persisted stream arming states."""
        return {
            str(row["stream_id"]): bool(row["armed"])
            for row in self._connection_or_raise().execute("SELECT stream_id, armed FROM stream_arming")
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
                (actor, schedule.stream_id, now, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )

    def get_arming_schedule(self, stream_id: str) -> ArmingSchedule | None:
        if not stream_id:
            raise ValueError("stream_id is required")
        row = self._connection_or_raise().execute(
            "SELECT * FROM arming_schedule WHERE stream_id = ?", (stream_id,)
        ).fetchone()
        if row is None:
            return None
        weekly = json.loads(row["weekly"])
        holidays = json.loads(row["holidays"] or "[]")
        override_until = row["override_until"]
        return ArmingSchedule(
            stream_id=str(row["stream_id"]),
            weekly=tuple(
                tuple(
                    TimeWindow(int(pair[0]), int(pair[1]))
                    for pair in day
                )
                for day in weekly
            ),
            holidays=frozenset(str(item) for item in holidays),
            enabled=bool(row["enabled"]),
            default_armed=bool(row["default_armed"]),
            override_armed=bool(row["override_armed"]) if row["override_armed"] is not None else None,
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
            "schema_version": 1,
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
        if int(payload.get("schema_version", 0)) != 1:
            raise ValueError("unsupported configuration schema version")
        if not actor.strip():
            raise ValueError("configuration actor is required")
        topology = EntityRegistry.from_dict(dict(payload.get("topology") or {}))
        streams = {stream.id for stream in topology.streams}
        rules = [_rule_from_dict(item) for item in payload.get("rules", ())]
        if any(rule.stream_id not in streams for rule in rules):
            raise ValueError("configuration rule references an unknown stream")
        schedules = [ArmingSchedule.from_dict(dict(item)) for item in payload.get("arming_schedules", ())]
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
                connection.execute("DELETE FROM area")
                connection.execute("DELETE FROM site")
            for site in topology.sites:
                self._upsert_site(connection, site)
            for area in topology.areas:
                self._upsert_area(connection, area)
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
            (area.id, area.site_id, area.parent_id, area.name, json.dumps(area.geometry), int(area.enabled)),
        )

    @staticmethod
    def _upsert_camera(connection: sqlite3.Connection, camera: CameraDevice) -> None:
        connection.execute(
            """INSERT INTO camera_device(id, area_id, name, vendor, address, enabled)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET area_id=excluded.area_id, name=excluded.name,
                                             vendor=excluded.vendor, address=excluded.address,
                                             enabled=excluded.enabled""",
            (camera.id, camera.area_id, camera.name, camera.vendor, camera.address, int(camera.enabled)),
        )

    @staticmethod
    def _upsert_stream(connection: sqlite3.Connection, stream: VideoStream) -> None:
        connection.execute(
            """INSERT INTO video_stream(id, camera_id, name, url, role, width, height, fps, model, confidence, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET camera_id=excluded.camera_id, name=excluded.name,
                 url=excluded.url, role=excluded.role, width=excluded.width, height=excluded.height,
                 fps=excluded.fps, model=excluded.model, confidence=excluded.confidence,
                 enabled=excluded.enabled""",
            (
                stream.id,
                stream.camera_id,
                stream.name,
                stream.url,
                str(stream.role),
                stream.width,
                stream.height,
                stream.fps,
                stream.model,
                stream.confidence,
                int(stream.enabled),
            ),
        )

    @staticmethod
    def _upsert_rule(connection: sqlite3.Connection, rule: Rule) -> None:
        connection.execute(
            """INSERT INTO detection_rule
               (id, stream_id, site_id, area_id, camera_id, name, type, geometry,
                target_labels, min_confidence, min_box_height, confirm_frames,
                cooldown_seconds, direction, enabled, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET stream_id=excluded.stream_id,
                 site_id=excluded.site_id, area_id=excluded.area_id,
                 camera_id=excluded.camera_id, name=excluded.name,
                 type=excluded.type, geometry=excluded.geometry,
                 target_labels=excluded.target_labels,
                 min_confidence=excluded.min_confidence,
                 min_box_height=excluded.min_box_height,
                 confirm_frames=excluded.confirm_frames,
                 cooldown_seconds=excluded.cooldown_seconds,
                 direction=excluded.direction, enabled=excluded.enabled,
                 version=excluded.version""",
            (
                rule.id,
                rule.stream_id,
                rule.site_id,
                rule.area_id,
                rule.camera_id,
                rule.name,
                rule.type.value,
                json.dumps(rule.geometry, separators=(",", ":")),
                json.dumps(sorted(rule.target_labels), separators=(",", ":")),
                rule.min_confidence,
                rule.min_box_height,
                rule.confirm_frames,
                rule.cooldown_seconds,
                rule.direction,
                int(rule.enabled),
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
    ) -> bool:
        """Persist one event and enqueue one delivery row per enabled channel.

        ``channel`` remains the backwards-compatible single-channel form.  New
        callers can pass ``channels`` to fan the same event out to several
        locally configured adapters without duplicating the event record.
        """
        connection = self._connection_or_raise()
        raw_channels = (channels,) if isinstance(channels, str) else (channels if channels is not None else (channel,))
        normalized_channels: list[str] = []
        for value in raw_channels:
            normalized = str(value).strip().lower()
            if not normalized:
                raise ValueError("notification channel must not be empty")
            if normalized not in normalized_channels:
                normalized_channels.append(normalized)
        if not normalized_channels:
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
            "track": event.track,
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with connection:
            inserted = connection.execute(
                """INSERT OR IGNORE INTO event
                (id, dedup_key, rule_id, channel_id, stream_id, site_id, area_id, camera_id,
                 level, occurred_at, nvr_time, clock_delta_ms, snapshot_uri, clip_uri,
                 clip_status, status, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )
            if inserted.rowcount != 1:
                return False
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
        track IDs and media paths, making it useful for a small local review
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
        return self._connection_or_raise().execute(
            "SELECT * FROM event WHERE id = ?", (event_id,)
        ).fetchone()

    def update_event_media(
        self,
        event_id: str,
        *,
        snapshot_uri: str | None = None,
        clip_uri: str | None = None,
        clip_status: str | None = None,
    ) -> bool:
        """Attach locally generated snapshot/clip paths to an event."""
        if not event_id:
            raise ValueError("event_id is required")
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
        assignments.append("payload = ?")
        values.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        values.append(event_id)
        with connection:
            result = connection.execute(
                f"UPDATE event SET {', '.join(assignments)} WHERE id = ?", values
            )
        return result.rowcount == 1

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
        clip_status = "expired" if not remaining_snapshot and not remaining_clip else "snapshot_only"
        assignments.append("clip_status = ?")
        values.append(clip_status)
        payload["clip_status"] = clip_status
        assignments.append("payload = ?")
        values.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        values.append(event_id)
        with connection:
            result = connection.execute(
                f"UPDATE event SET {', '.join(assignments)} WHERE id = ?", values
            )
            if result.rowcount != 1:
                return False
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
            status_text = status.value if isinstance(status, EventStatus) else EventStatus(str(status)).value
        except ValueError as exc:
            raise ValueError("event status must be open, confirmed, false_positive, or closed") from exc
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
                (actor, f"event.status.{status_text}", event_id, datetime.now(UTC).isoformat(), detail),
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
        "site_id": rule.site_id,
        "area_id": rule.area_id,
        "camera_id": rule.camera_id,
        "name": rule.name,
        "type": rule.type.value,
        "geometry": [list(point) for point in rule.geometry],
        "target_labels": sorted(rule.target_labels),
        "min_confidence": rule.min_confidence,
        "min_box_height": rule.min_box_height,
        "confirm_frames": rule.confirm_frames,
        "cooldown_seconds": rule.cooldown_seconds,
        "direction": rule.direction,
        "enabled": rule.enabled,
        "version": rule.version,
    }


def _rule_from_dict(value: Mapping[str, Any]) -> Rule:
    if not isinstance(value, Mapping):
        raise TypeError("configuration rule must be a mapping")
    return Rule(
        id=str(value["id"]),
        stream_id=str(value.get("stream_id") or value.get("channel_id") or ""),
        site_id=str(value["site_id"]) if value.get("site_id") is not None else None,
        area_id=str(value["area_id"]) if value.get("area_id") is not None else None,
        camera_id=str(value["camera_id"]) if value.get("camera_id") is not None else None,
        name=str(value.get("name", "")),
        type=RuleType(str(value["type"])),
        geometry=tuple((float(point[0]), float(point[1])) for point in value["geometry"]),
        target_labels=frozenset(str(label) for label in value.get("target_labels", ("person",))),
        min_confidence=float(value.get("min_confidence", 0.45)),
        min_box_height=float(value.get("min_box_height", 32.0)),
        confirm_frames=int(value.get("confirm_frames", 3)),
        cooldown_seconds=float(value.get("cooldown_seconds", 60.0)),
        direction=str(value.get("direction", "both")),
        enabled=bool(value.get("enabled", True)),
        version=int(value.get("version", 1)),
    )


def _redact_url(value: str) -> str:
    return re.sub(r"((?:rtsp|https?)://)[^/@\s]+@", r"\1***:***@", value, flags=re.IGNORECASE)
