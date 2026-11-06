"""Consistent local backup bundles for the merchant console."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from factoryguard import __version__

MAX_BACKUP_BYTES = 2 * 1024**3
RESTORE_MARKER_NAME = ".factoryguard-restore.json"
BRANDING_MEMBER = "branding/banner.svg"


def create_backup(
    db_path: str | Path,
    config_path: str | Path,
    destination: str | Path,
) -> Path:
    """Create a ZIP containing a consistent SQLite copy and JSON settings."""
    database = Path(db_path).expanduser().resolve()
    config = Path(config_path).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"业务数据库不存在：{database}")
    if target in {database, config}:
        raise ValueError("备份文件不能覆盖业务数据库或配置文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    created_at = datetime.now(UTC)
    try:
        with tempfile.TemporaryDirectory(prefix="factoryguard-backup-") as temporary:
            work = Path(temporary)
            snapshot = work / "factoryguard.db"
            _backup_sqlite(database, snapshot)
            config_included = config.is_file()
            if config_included:
                shutil.copy2(config, work / "factoryguard.json")
            branding = config.parent / "branding" / "banner.svg"
            branding_included = branding.is_file()
            if branding_included:
                staged_branding = work / "branding" / "banner.svg"
                staged_branding.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(branding, staged_branding)
            manifest = {
                "product": "FactoryGuard",
                "version": __version__,
                "created_at": created_at.isoformat(),
                "database": "factoryguard.db",
                "config_included": config_included,
                "branding_included": branding_included,
            }
            (work / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in (
                    "manifest.json",
                    "factoryguard.db",
                    "factoryguard.json",
                    BRANDING_MEMBER,
                ):
                    path = work / name
                    if path.is_file():
                        archive.write(path, arcname=name)
        os.replace(temporary_zip, target)
    except Exception:
        temporary_zip.unlink(missing_ok=True)
        raise
    return target


def _backup_sqlite(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


@dataclass(frozen=True, slots=True)
class BackupInfo:
    archive: Path
    version: str
    created_at: str
    config_included: bool
    database_size: int
    branding_included: bool = False


@dataclass(frozen=True, slots=True)
class RestoreResult:
    backup: BackupInfo
    safety_backup: Path | None


def inspect_backup(archive_path: str | Path) -> BackupInfo:
    """Validate a backup archive and its embedded SQLite database."""
    archive_path = Path(archive_path).expanduser().resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"备份包不存在：{archive_path}")
    with tempfile.TemporaryDirectory(prefix="factoryguard-inspect-") as temporary:
        work = Path(temporary)
        manifest = _extract_backup_members(archive_path, work)
        database = work / "factoryguard.db"
        _validate_database(database)
        config_included = (work / "factoryguard.json").is_file()
        branding_included = (work / "branding" / "banner.svg").is_file()
        database_size = database.stat().st_size
    return BackupInfo(
        archive=archive_path,
        version=str(manifest.get("version", "")),
        created_at=str(manifest.get("created_at", "")),
        config_included=config_included,
        database_size=database_size,
        branding_included=branding_included,
    )


def stage_restore(archive_path: str | Path, db_path: str | Path) -> BackupInfo:
    """Validate a backup and schedule it for the next process start."""
    info = inspect_backup(archive_path)
    target_db = Path(db_path).expanduser().resolve()
    marker = pending_restore_path(target_db)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    payload = {
        "archive": str(info.archive),
        "version": info.version,
        "created_at": info.created_at,
        "config_included": info.config_included,
        "branding_included": info.branding_included,
        "staged_at": datetime.now(UTC).isoformat(),
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, marker)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return info


def pending_restore_path(db_path: str | Path) -> Path:
    database = Path(db_path).expanduser().resolve()
    return database.parent / RESTORE_MARKER_NAME


def apply_pending_restore(
    db_path: str | Path,
    config_path: str | Path,
) -> RestoreResult | None:
    """Apply a staged restore before the application opens its database."""
    database = Path(db_path).expanduser().resolve()
    marker = pending_restore_path(database)
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not str(payload.get("archive", "")).strip():
            raise ValueError("恢复请求缺少备份包路径")
        result = restore_backup(payload["archive"], database, config_path)
        marker.unlink(missing_ok=True)
        return result
    except Exception:
        marker.unlink(missing_ok=True)
        raise


def restore_backup(
    archive_path: str | Path,
    db_path: str | Path,
    config_path: str | Path,
    *,
    safety_dir: str | Path | None = None,
) -> RestoreResult:
    """Replace current data with a validated backup while the app is closed."""
    info = inspect_backup(archive_path)
    database = Path(db_path).expanduser().resolve()
    config = Path(config_path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    safety_backup: Path | None = None
    if database.is_file():
        target_dir = (
            Path(safety_dir).expanduser().resolve() if safety_dir else database.parent / "backups"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        safety_backup = target_dir / (
            f"pre-restore-{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond // 1000:03d}.zip"
        )
        create_backup(database, config, safety_backup)
    token = uuid4().hex
    sidecars = (Path(f"{database}-wal"), Path(f"{database}-shm"))
    db_old = database.with_name(f".{database.name}.restore-old-{token}")
    config_old = config.with_name(f".{config.name}.restore-old-{token}")
    branding = config.parent / "branding" / "banner.svg"
    branding_old = branding.with_name(f".{branding.name}.restore-old-{token}")
    sidecar_old = tuple(path.with_name(f".{path.name}.restore-old-{token}") for path in sidecars)
    db_placed = False
    config_placed = False
    branding_placed = False
    pending_new_config: Path | None = None
    pending_new_branding: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="factoryguard-restore-", dir=database.parent
        ) as temporary:
            work = Path(temporary)
            _extract_backup_members(info.archive, work)
            staged_db = work / "factoryguard.db"
            staged_config = work / "factoryguard.json"
            if database.is_file():
                os.replace(database, db_old)
            for path, old_path in zip(sidecars, sidecar_old, strict=True):
                if path.exists():
                    os.replace(path, old_path)
            os.replace(staged_db, database)
            db_placed = True
            if staged_config.is_file():
                pending_new_config = config.with_name(f".{config.name}.restore-new-{token}")
                shutil.copy2(staged_config, pending_new_config)
                if config.is_file():
                    os.replace(config, config_old)
                os.replace(pending_new_config, config)
                config_placed = True
                pending_new_config = None
            staged_branding = work / BRANDING_MEMBER
            if staged_branding.is_file():
                branding.parent.mkdir(parents=True, exist_ok=True)
                pending_new_branding = branding.with_name(f".{branding.name}.restore-new-{token}")
                shutil.copy2(staged_branding, pending_new_branding)
                if branding.is_file():
                    os.replace(branding, branding_old)
                os.replace(pending_new_branding, branding)
                branding_placed = True
                pending_new_branding = None
        db_old.unlink(missing_ok=True)
        config_old.unlink(missing_ok=True)
        branding_old.unlink(missing_ok=True)
        for path in sidecar_old:
            path.unlink(missing_ok=True)
    except Exception as exc:
        if pending_new_config is not None:
            pending_new_config.unlink(missing_ok=True)
        if pending_new_branding is not None:
            pending_new_branding.unlink(missing_ok=True)
        if db_placed:
            database.unlink(missing_ok=True)
        if config_placed:
            config.unlink(missing_ok=True)
        if branding_placed:
            branding.unlink(missing_ok=True)
        if db_old.exists():
            os.replace(db_old, database)
        if config_old.exists():
            os.replace(config_old, config)
        if branding_old.exists():
            os.replace(branding_old, branding)
        for path, old_path in zip(sidecars, sidecar_old, strict=True):
            if old_path.exists():
                os.replace(old_path, path)
        raise RuntimeError(f"恢复备份失败，原数据已保留：{exc}") from exc
    return RestoreResult(info, safety_backup)


def _extract_backup_members(archive_path: Path, work: Path) -> dict[str, object]:
    required = {"manifest.json", "factoryguard.db"}
    optional = {"factoryguard.json", BRANDING_MEMBER}
    total_size = 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("备份包不是有效的 ZIP 文件") from exc
    with archive:
        names = [info.filename for info in archive.infolist()]
        if len(names) != len(set(names)):
            raise ValueError("备份包包含重复文件")
        if not required.issubset(names):
            raise ValueError("备份包缺少数据库或清单文件")
        for info in archive.infolist():
            if info.filename not in required | optional or info.is_dir():
                continue
            total_size += int(info.file_size)
            if total_size > MAX_BACKUP_BYTES:
                raise ValueError("备份包解压后超过允许大小")
            target = work / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        try:
            manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("备份清单无法读取") from exc
        if not isinstance(manifest, dict) or manifest.get("product") != "FactoryGuard":
            raise ValueError("备份清单不是 FactoryGuard 格式")
        backup_version = str(manifest.get("version", "")).strip()
        try:
            backup_major = int(backup_version.split(".", 1)[0])
            current_major = int(__version__.split(".", 1)[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("备份版本信息无效") from exc
        if backup_major > current_major:
            raise ValueError(
                f"备份由更高主版本 FactoryGuard {backup_version} 创建，当前版本为 {__version__}"
            )
        config = work / "factoryguard.json"
        branding = work / BRANDING_MEMBER
        config_present = config.is_file()
        branding_present = branding.is_file()
        config_declared = manifest.get("config_included")
        branding_declared = manifest.get("branding_included")
        if config_declared is not None and bool(config_declared) != config_present:
            raise ValueError("备份清单与配置文件状态不一致")
        if branding_declared is not None and bool(branding_declared) != branding_present:
            raise ValueError("备份清单与客户横幅状态不一致")
        if config_present:
            try:
                config_payload = json.loads(config.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("备份配置文件无法读取") from exc
            if not isinstance(config_payload, dict):
                raise ValueError("备份配置文件顶层必须是 JSON 对象")
    return manifest


def _validate_database(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result is None or str(result[0]).lower() != "ok":
            raise ValueError("备份数据库完整性检查失败")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        required = {"site", "area", "camera_device", "incident"}
        if not required.issubset(tables):
            raise ValueError("备份数据库缺少 FactoryGuard 业务表")
    except sqlite3.Error as exc:
        raise ValueError(f"备份数据库无法读取：{exc}") from exc
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "BackupInfo",
    "RestoreResult",
    "apply_pending_restore",
    "create_backup",
    "inspect_backup",
    "pending_restore_path",
    "restore_backup",
    "stage_restore",
]
