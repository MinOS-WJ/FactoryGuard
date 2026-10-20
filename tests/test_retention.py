from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from factoryguard.domain.models import Detection, Event, Frame, Rule, RuleType
from factoryguard.storage.retention import MediaRetentionManager
from factoryguard.storage.sqlite import SQLiteStore


def test_media_retention_removes_old_files_but_keeps_event(tmp_path):
    database = tmp_path / "events.db"
    media = tmp_path / "events-media"
    media.mkdir()
    snapshot = media / "old.png"
    clip = media / "old.mp4"
    snapshot.write_bytes(b"snapshot")
    clip.write_bytes(b"clip")
    old_time = (datetime.now(UTC) - timedelta(days=10)).timestamp()
    os.utime(snapshot, (old_time, old_time))
    os.utime(clip, (old_time, old_time))

    store = SQLiteStore(database)
    event = Event.create(
        rule=Rule("r1", "stream-1", "test", RuleType.POLYGON, ((0, 0), (10, 0), (10, 10))),
        frame=Frame("stream-1", 1, datetime.now(UTC), 20, 20),
        detection=Detection("person", 0.9, (1, 1, 4, 8)),
    )
    assert store.create_event_with_outbox(event)
    assert store.update_event_media(event.id, snapshot_uri=str(snapshot), clip_uri=str(clip), clip_status="ready")
    report = MediaRetentionManager(media_root=media).cleanup(store, retention_days=3)

    assert report.removed == 2
    assert report.bytes_freed == len(b"snapshot") + len(b"clip")
    assert not snapshot.exists()
    assert not clip.exists()
    row = store.get_event(event.id)
    assert row is not None
    assert row["snapshot_uri"] is None
    assert row["clip_uri"] is None
    assert row["clip_status"] == "expired"
    assert len(store.list_events()) == 1
    store.close()


def test_media_retention_dry_run_does_not_delete(tmp_path):
    media = tmp_path / "events-media"
    media.mkdir()
    path = media / "old.png"
    path.write_bytes(b"x")
    old_time = (datetime.now(UTC) - timedelta(days=4)).timestamp()
    os.utime(path, (old_time, old_time))
    store = SQLiteStore(tmp_path / "events.db")
    report = MediaRetentionManager(media_root=media).cleanup(store, retention_days=1, dry_run=True)
    assert report.eligible == 0
    assert path.exists()
    store.close()
