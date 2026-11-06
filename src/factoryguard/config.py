"""Persistent software configuration backed by a local JSON file.

Business records remain in SQLite.  This store only owns application settings
and credential references used by the merchant console.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

DEFAULT_CONFIG_PATH = Path("config") / "factoryguard.json"


@dataclass(frozen=True, slots=True)
class MerchantConfig:
    """Minimal runtime paths for the merchant desktop application."""

    db: str = "data/factoryguard.db"
    config_path: str = "config/factoryguard.json"


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "llm": {
        "enabled": False,
        "provider": "",
        "endpoint": "",
        "model": "",
        "timeout": 35,
        "api_key": "",
    },
    "gb28181": {
        "enabled": False,
        "platform_id": "",
        "domain": "",
        "port": 5060,
        "password": "",
    },
    "branding": {
        "banner_svg": "",
        "banner_height": 82,
    },
    "notifications": {
        "enabled": False,
        "timeout": 10,
        "webhook": {
            "enabled": False,
            "url": "",
            "secret": "",
        },
        "email": {
            "enabled": False,
            "host": "",
            "port": 587,
            "username": "",
            "password": "",
            "sender": "",
            "recipients": [],
            "security": "starttls",
        },
        "sms": {
            "enabled": False,
            "endpoint": "",
            "provider": "custom_http",
            "token": "",
            "sender": "",
            "template_id": "",
            "recipients": [],
        },
    },
    "credentials": {},
}


class ConfigFileError(RuntimeError):
    """The local configuration file is unreadable or has an invalid shape."""


class AppConfigStore:
    """Small thread-safe JSON configuration and credential repository.

    Stores that point at the same file share one in-memory snapshot and one
    lock.  Without this, a background worker holding its own copy could
    overwrite settings the UI had just written.
    """

    _shared_state: ClassVar[dict[str, tuple[threading.RLock, dict[str, Any]]]] = {}
    _shared_lock: ClassVar[threading.RLock] = threading.RLock()
    MAX_SHARED_STORES: ClassVar[int] = 32

    def __init__(self, path: str | Path | None = None) -> None:
        configured = os.environ.get("FACTORYGUARD_CONFIG", "").strip()
        self.path = Path(path or configured or DEFAULT_CONFIG_PATH).expanduser()
        key = str(self.path.resolve()) if self.path.exists() else str(self.path.absolute())
        with AppConfigStore._shared_lock:
            shared = AppConfigStore._shared_state.get(key)
            if shared is None:
                if len(AppConfigStore._shared_state) >= AppConfigStore.MAX_SHARED_STORES:
                    AppConfigStore._shared_state.clear()
                shared = (threading.RLock(), self._read())
                AppConfigStore._shared_state[key] = shared
            self._lock, self._data = shared

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def reload(self) -> dict[str, Any]:
        with self._lock:
            self._data = self._read()
            return deepcopy(self._data)

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            value = self._data.get(name, {})
            return deepcopy(value) if isinstance(value, Mapping) else {}

    def value(self, section: str, key: str, default: Any = None) -> Any:
        values = self.section(section)
        return values.get(key, default)

    def update_section(self, name: str, values: Mapping[str, Any], *, save: bool = True) -> None:
        if not name.strip() or not isinstance(values, Mapping):
            raise ValueError("configuration section and values are required")
        with self._lock:
            previous = deepcopy(self._data)
            current = self._data.get(name)
            section = (
                _merge_mapping(current, values) if isinstance(current, Mapping) else dict(values)
            )
            self._data[name] = section
            if save:
                try:
                    self._write()
                except ConfigFileError:
                    self._data = previous
                    raise

    def ensure_file(self) -> None:
        """Persist defaults when the configuration file does not exist yet."""
        with self._lock:
            if not self.path.exists():
                self._write()

    def get_device_credential_blob(self, credential_ref: str) -> str | None:
        if not credential_ref.strip():
            return None
        with self._lock:
            credentials = self._data.get("credentials", {})
            if not isinstance(credentials, Mapping):
                return None
            value = credentials.get(credential_ref)
            if isinstance(value, Mapping):
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            return str(value) if value else None

    def save_device_credential(
        self,
        credential_ref: str,
        ciphertext: str,
        *,
        actor: str = "system",
    ) -> None:
        del actor  # Kept for compatibility with the SQLite credential interface.
        reference = credential_ref.strip()
        protected = ciphertext.strip()
        if not reference or not protected:
            raise ValueError("credential_ref and ciphertext are required")
        with self._lock:
            previous = deepcopy(self._data)
            credentials = self._data.get("credentials")
            if not isinstance(credentials, dict):
                credentials = {}
                self._data["credentials"] = credentials
            try:
                decoded = json.loads(protected)
            except json.JSONDecodeError:
                decoded = protected
            credentials[reference] = decoded
            try:
                self._write()
            except ConfigFileError:
                self._data = previous
                raise

    def prune_unused_credentials(self, referenced: set[str] | frozenset[str]) -> tuple[str, ...]:
        """Drop credential entries that no physical device references any more.

        Removing a camera can leave its credential behind when the config file
        could not be updated at that moment.  This keeps the local file from
        growing with stale passwords.
        """
        with self._lock:
            credentials = self._data.get("credentials")
            if not isinstance(credentials, dict) or not credentials:
                return ()
            stale = tuple(
                sorted(reference for reference in credentials if str(reference) not in referenced)
            )
            if not stale:
                return ()
            previous = deepcopy(self._data)
            for reference in stale:
                credentials.pop(reference, None)
            try:
                self._write()
            except ConfigFileError:
                self._data = previous
                raise
        return stale

    def delete_device_credential(self, credential_ref: str, *, actor: str = "system") -> bool:
        del actor
        reference = credential_ref.strip()
        if not reference:
            return False
        with self._lock:
            credentials = self._data.get("credentials")
            if not isinstance(credentials, dict) or reference not in credentials:
                return False
            previous = deepcopy(self._data)
            credentials.pop(reference, None)
            try:
                self._write()
            except ConfigFileError:
                self._data = previous
                raise
        return True

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return deepcopy(DEFAULT_CONFIG)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigFileError(f"无法读取配置文件 {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigFileError(f"配置文件顶层必须是 JSON 对象: {self.path}")
        return _merge_defaults(payload)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A unique temporary name keeps concurrent writers from replacing each
        # other's half-written file.
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConfigFileError(f"无法写入配置文件 {self.path}: {exc}") from exc


def _merge_mapping(default: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(default))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = _merge_mapping(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _merge_defaults(payload: Mapping[str, Any]) -> dict[str, Any]:
    merged = _merge_mapping(DEFAULT_CONFIG, payload)
    if not isinstance(merged.get("credentials"), dict):
        merged["credentials"] = {}
    return merged


def migrate_legacy_merchant_config(store: Any, config_store: AppConfigStore) -> bool:
    """Move old registry/SQLite merchant settings into the JSON config once."""
    if bool(config_store.value("migration", "legacy_imported", False)):
        return False
    snapshot = config_store.data
    llm_existing = snapshot.get("llm", {}) if isinstance(snapshot.get("llm"), Mapping) else {}
    gb_existing = (
        snapshot.get("gb28181", {}) if isinstance(snapshot.get("gb28181"), Mapping) else {}
    )
    credentials_existing = (
        snapshot.get("credentials", {}) if isinstance(snapshot.get("credentials"), Mapping) else {}
    )
    try:
        from PySide6.QtCore import QSettings

        from factoryguard.security.credentials import SecretProtectionError, default_vault

        legacy = QSettings("FactoryGuard", "MerchantConsole")
        vault = default_vault()
    except Exception:
        return False

    llm_endpoint = str(legacy.value("llm/endpoint", ""))
    llm_model = str(legacy.value("llm/model", ""))
    llm_provider = str(legacy.value("llm/provider", "openai"))
    llm_enabled = bool(legacy.value("llm/enabled", False, type=bool))
    llm_timeout = int(legacy.value("llm/timeout", 35))
    gb_enabled = bool(legacy.value("gb/enabled", False, type=bool))
    gb_platform = str(legacy.value("gb/platform_id", ""))
    gb_domain = str(legacy.value("gb/domain", ""))
    gb_port = int(legacy.value("gb/port", 5060))
    legacy_found = bool(
        llm_endpoint
        or llm_model
        or llm_provider != "openai"
        or llm_enabled
        or gb_enabled
        or bool(gb_platform)
        or bool(gb_domain)
        or gb_port != 5060
    )

    migrated_secret_refs: list[str] = []

    def decoded_secret(reference: str, field_name: str) -> str:
        blob = store.get_device_credential_blob(reference)
        if not blob:
            return ""
        try:
            value = str(getattr(vault.decode(blob), field_name))
        except (SecretProtectionError, ValueError):
            return ""
        migrated_secret_refs.append(reference)
        return value

    llm_api_key = decoded_secret("llm-api-key", "token")
    gb_password = decoded_secret("gb-sip-password", "password")
    topology = store.load_topology()
    migrated_device_refs: list[str] = []
    for device in topology.devices:
        reference = device.credentials_ref
        if not reference:
            continue
        blob = store.get_device_credential_blob(reference)
        if not blob:
            continue
        if reference not in credentials_existing:
            try:
                credentials = vault.decode(blob)
            except SecretProtectionError:
                continue
            config_store.save_device_credential(
                reference,
                json.dumps(
                    {
                        "username": credentials.username,
                        "password": credentials.password,
                        "token": credentials.token,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        migrated_device_refs.append(reference)

    if not legacy_found and not llm_api_key and not gb_password and not migrated_device_refs:
        return False

    llm_configured = bool(
        any(
            str(llm_existing.get(key, "")).strip()
            for key in ("provider", "endpoint", "model", "api_key")
        )
        or llm_existing.get("enabled")
        or llm_existing.get("timeout") != DEFAULT_CONFIG["llm"]["timeout"]
    )
    if llm_configured:
        llm_payload = {
            "enabled": bool(llm_existing.get("enabled", False)),
            "provider": str(llm_existing.get("provider", "")),
            "endpoint": str(llm_existing.get("endpoint", "")),
            "model": str(llm_existing.get("model", "")),
            "timeout": int(llm_existing.get("timeout", DEFAULT_CONFIG["llm"]["timeout"])),
            "api_key": str(llm_existing.get("api_key", "")) or llm_api_key,
        }
    else:
        llm_payload = {
            "enabled": llm_enabled,
            "provider": llm_provider,
            "endpoint": llm_endpoint or DEFAULT_CONFIG["llm"]["endpoint"],
            "model": llm_model,
            "timeout": llm_timeout,
            "api_key": llm_api_key,
        }

    gb_configured = bool(
        gb_existing.get("enabled")
        or str(gb_existing.get("password", "")).strip()
        or str(gb_existing.get("platform_id", "")).strip()
        or str(gb_existing.get("domain", "")).strip()
        or gb_existing.get("port") != DEFAULT_CONFIG["gb28181"]["port"]
    )
    if gb_configured:
        gb_payload = {
            "enabled": bool(gb_existing.get("enabled", False)),
            "platform_id": str(gb_existing.get("platform_id", "")),
            "domain": str(gb_existing.get("domain", "")),
            "port": int(gb_existing.get("port", DEFAULT_CONFIG["gb28181"]["port"])),
            "password": str(gb_existing.get("password", "")) or gb_password,
        }
    else:
        gb_payload = {
            "enabled": gb_enabled,
            "platform_id": gb_platform,
            "domain": gb_domain,
            "port": gb_port,
            "password": gb_password,
        }

    try:
        config_store.update_section("llm", llm_payload, save=False)
        config_store.update_section("gb28181", gb_payload, save=False)
        config_store.update_section("migration", {"legacy_imported": True}, save=True)
    except ConfigFileError:
        return False

    for reference in dict.fromkeys((*migrated_secret_refs, *migrated_device_refs)):
        try:
            store.delete_device_credential(reference, actor="config-migration")
        except Exception:
            pass
    try:
        legacy.clear()
        legacy.sync()
    except Exception:
        pass
    return True


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_CONFIG_PATH",
    "AppConfigStore",
    "ConfigFileError",
    "MerchantConfig",
    "migrate_legacy_merchant_config",
]
