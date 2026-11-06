from __future__ import annotations

import json
import zipfile

import pytest

from factoryguard.domain.incidents import Incident, IncidentSeverity
from factoryguard.domain.models import Area, CameraDevice, Site
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.backup import (
    apply_pending_restore,
    create_backup,
    inspect_backup,
    pending_restore_path,
    stage_restore,
)
from factoryguard.storage.sqlite import SQLiteStore


def test_create_backup_contains_consistent_database_and_manifest(tmp_path):
    database = tmp_path / "data" / "factoryguard.db"
    database.parent.mkdir()
    config = tmp_path / "config" / "factoryguard.json"
    config.parent.mkdir()
    config.write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            cameras=(CameraDevice("camera-1", "area-1", "仓库北门"),),
        )
    )
    store.create_incident(
        Incident.create(
            title="备份测试险情",
            severity=IncidentSeverity.HIGH,
            site_id="site-1",
            area_id="area-1",
            camera_id="camera-1",
        )
    )
    destination = tmp_path / "exports" / "factoryguard-backup.zip"

    try:
        result = create_backup(database, config, destination)
    finally:
        store.close()

    assert result == destination.resolve()
    assert destination.is_file()
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "factoryguard.db",
            "factoryguard.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["product"] == "FactoryGuard"
        assert manifest["config_included"] is True
        assert manifest["branding_included"] is False
        restored_path = tmp_path / "restored.db"
        restored_path.write_bytes(archive.read("factoryguard.db"))

    restored = SQLiteStore(restored_path)
    try:
        assert [incident.title for incident in restored.list_incidents()] == ["备份测试险情"]
        topology = restored.load_topology()
        assert [camera.name for camera in topology.cameras] == ["仓库北门"]
    finally:
        restored.close()


def test_backup_restore_preserves_customer_branding(tmp_path):
    database = tmp_path / "data" / "factoryguard.db"
    database.parent.mkdir()
    config = tmp_path / "config" / "factoryguard.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "branding": {
                    "banner_svg": "branding/banner.svg",
                    "banner_height": 96,
                }
            }
        ),
        encoding="utf-8",
    )
    branding = config.parent / "branding" / "banner.svg"
    branding.parent.mkdir()
    original_svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>客户横幅</text></svg>'
    branding.write_text(original_svg, encoding="utf-8")
    store = SQLiteStore(database)
    store.save_topology(EntityRegistry(sites=(Site("site-1", "星河园区"),)))
    store.close()
    archive = tmp_path / "backup.zip"

    create_backup(database, config, archive)
    info = inspect_backup(archive)
    assert info.config_included
    assert info.branding_included
    with zipfile.ZipFile(archive) as bundle:
        assert "branding/banner.svg" in bundle.namelist()

    branding.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    stage_restore(archive, database)
    result = apply_pending_restore(database, config)

    assert result is not None
    assert branding.read_text(encoding="utf-8") == original_svg
    assert result.safety_backup is not None
    assert inspect_backup(result.safety_backup).branding_included


def test_backup_includes_branding_without_config_file(tmp_path):
    database = tmp_path / "data" / "factoryguard.db"
    database.parent.mkdir()
    store = SQLiteStore(database)
    store.save_topology(EntityRegistry(sites=(Site("site-1", "测试园区"),)))
    store.close()
    config = tmp_path / "config" / "factoryguard.json"
    branding = config.parent / "branding" / "banner.svg"
    branding.parent.mkdir(parents=True)
    original_svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#123456"/></svg>'
    branding.write_text(original_svg, encoding="utf-8")
    archive = tmp_path / "branding-only.zip"

    create_backup(database, config, archive)
    info = inspect_backup(archive)

    assert info.config_included is False
    assert info.branding_included is True
    with zipfile.ZipFile(archive) as bundle:
        assert "branding/banner.svg" in bundle.namelist()
        assert "factoryguard.json" not in bundle.namelist()


def test_create_backup_rejects_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError, match="业务数据库不存在"):
        create_backup(
            tmp_path / "missing.db",
            tmp_path / "factoryguard.json",
            tmp_path / "backup.zip",
        )


def test_staged_restore_replaces_data_and_keeps_safety_backup(tmp_path):
    database = tmp_path / "data" / "factoryguard.db"
    database.parent.mkdir()
    config = tmp_path / "config" / "factoryguard.json"
    config.parent.mkdir()
    config.write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            cameras=(CameraDevice("camera-1", "area-1", "仓库北门"),),
        )
    )
    store.create_incident(Incident.create(title="原始险情", severity=IncidentSeverity.HIGH))
    store.close()
    archive = tmp_path / "backup.zip"
    create_backup(database, config, archive)

    store = SQLiteStore(database)
    store.create_incident(Incident.create(title="恢复前新增险情", severity=IncidentSeverity.LOW))
    store.close()
    config.write_text('{"llm": {"enabled": true}}', encoding="utf-8")

    info = stage_restore(archive, database)
    assert info.database_size > 0
    marker = pending_restore_path(database)
    assert marker.is_file()
    result = apply_pending_restore(database, config)

    assert result is not None
    assert not marker.exists()
    assert result.safety_backup is not None and result.safety_backup.is_file()
    restored = SQLiteStore(database)
    try:
        assert [incident.title for incident in restored.list_incidents()] == ["原始险情"]
    finally:
        restored.close()
    assert json.loads(config.read_text(encoding="utf-8"))["llm"]["enabled"] is False
    safety = inspect_backup(result.safety_backup)
    assert safety.database_size > 0


def test_backup_validation_rejects_manifest_member_mismatch(tmp_path):
    database = tmp_path / "data" / "factoryguard.db"
    database.parent.mkdir()
    config = tmp_path / "config" / "factoryguard.json"
    config.parent.mkdir()
    config.write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    branding = config.parent / "branding" / "banner.svg"
    branding.parent.mkdir()
    branding.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    store = SQLiteStore(database)
    store.save_topology(EntityRegistry(sites=(Site("site-1", "测试园区"),)))
    store.close()
    source = tmp_path / "source.zip"
    create_backup(database, config, source)
    mismatched = tmp_path / "mismatched.zip"

    with zipfile.ZipFile(source) as original, zipfile.ZipFile(mismatched, "w") as target:
        for info in original.infolist():
            payload = original.read(info)
            if info.filename == "manifest.json":
                manifest = json.loads(payload)
                manifest["config_included"] = False
                manifest["branding_included"] = False
                payload = json.dumps(manifest).encode("utf-8")
            target.writestr(info, payload)

    with pytest.raises(ValueError, match="清单与配置文件状态不一致"):
        inspect_backup(mismatched)


def test_backup_validation_rejects_missing_database(tmp_path):
    archive = tmp_path / "invalid.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps({"product": "FactoryGuard", "version": "2.0.0"}),
        )
    with pytest.raises(ValueError, match="缺少数据库"):
        inspect_backup(archive)


def test_backup_validation_rejects_future_major_version(tmp_path):
    database = tmp_path / "data" / "factoryguard.db"
    database.parent.mkdir()
    store = SQLiteStore(database)
    store.save_topology(EntityRegistry(sites=(Site("site-1", "测试园区"),)))
    store.close()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    original = tmp_path / "original.zip"
    create_backup(database, config, original)
    future = tmp_path / "future.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(future, "w") as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "manifest.json":
                manifest = json.loads(payload)
                manifest["version"] = "99.0.0"
                payload = json.dumps(manifest).encode("utf-8")
            target.writestr(info, payload)

    with pytest.raises(ValueError, match="更高主版本"):
        inspect_backup(future)
