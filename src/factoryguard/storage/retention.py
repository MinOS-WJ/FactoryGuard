from __future__ import annotations

"""Safe retention cleanup for locally stored event media."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Any

from .sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class RetentionReport:
    scanned: int
    eligible: int
    removed: int
    bytes_freed: int
    skipped: int
    errors: int


class MediaRetentionManager:
    """Remove expired snapshot/clip files without deleting event history."""

    def __init__(self, *, media_root: str | Path, clock: Any = None) -> None:
        self.media_root = Path(media_root).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(UTC))

    def cleanup(
        self,
        store: SQLiteStore,
        *,
        retention_days: float,
        dry_run: bool = False,
    ) -> RetentionReport:
        if not isfinite(retention_days) or retention_days < 0:
            raise ValueError("retention_days must be finite and non-negative")
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("retention clock must return an aware datetime")
        cutoff = now.timestamp() - retention_days * 86400
        scanned = eligible = removed = skipped = errors = bytes_freed = 0
        if not self.media_root.exists():
            return RetentionReport(0, 0, 0, 0, 0, 0)
        for row in store.list_events():
            for field in ("snapshot_uri", "clip_uri"):
                value = row[field]
                if not value:
                    continue
                scanned += 1
                path = Path(str(value)).expanduser()
                if not path.is_absolute():
                    database_parent = (
                        Path(store.path).expanduser().resolve().parent
                        if store.path != ":memory:"
                        else self.media_root
                    )
                    path = (database_parent / path).resolve()
                else:
                    path = path.resolve()
                if not _within(path, self.media_root) or not path.is_file():
                    skipped += 1
                    continue
                try:
                    stat = path.stat()
                except (OSError, sqlite3.Error):
                    errors += 1
                    continue
                if stat.st_mtime > cutoff:
                    continue
                eligible += 1
                if dry_run:
                    continue
                try:
                    size = stat.st_size
                    path.unlink()
                    removed += 1
                    bytes_freed += size
                    store.clear_event_media(
                        str(row["id"]),
                        snapshot=field == "snapshot_uri",
                        clip=field == "clip_uri",
                        actor="retention",
                    )
                except OSError:
                    errors += 1
        return RetentionReport(scanned, eligible, removed, bytes_freed, skipped, errors)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = ["MediaRetentionManager", "RetentionReport"]
