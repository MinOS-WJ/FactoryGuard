from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from factoryguard.app.device_manager import DeviceManager
from factoryguard.config import AppConfigStore, ConfigFileError, migrate_legacy_merchant_config
from factoryguard.devices.base import DeviceCredentials
from factoryguard.domain.models import AnalysisPolicy, DeviceProtocol, VideoDevice
from factoryguard.security.credentials import PlaintextCredentialVault


def test_config_file_merges_defaults_and_persists_sections(tmp_path):
    path = tmp_path / "factoryguard.json"
    path.write_text('{"llm": {"model": "demo-model"}}', encoding="utf-8")
    store = AppConfigStore(path)
    assert store.value("llm", "model") == "demo-model"
    assert store.value("llm", "timeout") == 35
    store.update_section("gb28181", {"enabled": True, "port": 15060})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["llm"]["model"] == "demo-model"
    assert payload["gb28181"]["enabled"] is True
    assert payload["gb28181"]["port"] == 15060


def test_nested_config_sections_keep_all_defaults(tmp_path):
    path = tmp_path / "factoryguard.json"
    path.write_text(
        '{"notifications": {"email": {"host": "smtp.example.test"}}}',
        encoding="utf-8",
    )

    store = AppConfigStore(path)
    notifications = store.section("notifications")

    assert notifications["enabled"] is False
    assert notifications["timeout"] == 10
    assert notifications["email"]["host"] == "smtp.example.test"
    assert notifications["email"]["port"] == 587
    assert notifications["email"]["security"] == "starttls"
    assert notifications["email"]["recipients"] == []
    assert notifications["webhook"]["enabled"] is False
    assert notifications["sms"]["provider"] == "custom_http"

    store.update_section("notifications", {"email": {"host": "smtp.updated.test"}})
    updated = store.section("notifications")
    assert updated["email"]["host"] == "smtp.updated.test"
    assert updated["email"]["port"] == 587
    assert updated["email"]["security"] == "starttls"
    assert updated["email"]["recipients"] == []
    assert updated["webhook"]["enabled"] is False


def test_new_config_does_not_preconfigure_customer_vendor_details(tmp_path):
    store = AppConfigStore(tmp_path / "factoryguard.json")
    assert store.value("llm", "enabled") is False
    assert store.value("llm", "provider") == ""
    assert store.value("llm", "endpoint") == ""
    assert store.value("llm", "model") == ""
    assert store.value("llm", "api_key") == ""
    assert store.value("gb28181", "enabled") is False
    assert store.value("gb28181", "platform_id") == ""
    assert store.value("gb28181", "domain") == ""
    assert store.value("gb28181", "password") == ""
    assert store.value("branding", "banner_svg") == ""
    assert store.value("branding", "banner_height") == 82
    assert store.value("notifications", "enabled") is False
    notifications = store.section("notifications")
    assert notifications["webhook"]["url"] == ""
    assert notifications["email"]["host"] == ""
    assert notifications["sms"]["endpoint"] == ""


def test_config_file_stores_protected_credentials_without_plaintext(tmp_path):
    path = tmp_path / "factoryguard.json"
    store = AppConfigStore(path)
    store.save_device_credential("llm-api-key", "protected-hex-value")
    assert store.get_device_credential_blob("llm-api-key") == "protected-hex-value"
    text = path.read_text(encoding="utf-8")
    assert "protected-hex-value" in text
    reloaded = AppConfigStore(path)
    assert reloaded.get_device_credential_blob("llm-api-key") == "protected-hex-value"
    assert reloaded.delete_device_credential("llm-api-key")
    assert reloaded.get_device_credential_blob("llm-api-key") is None


def test_invalid_config_file_raises_clear_error(tmp_path):
    path = tmp_path / "factoryguard.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigFileError, match="无法读取配置文件"):
        AppConfigStore(path)


def test_device_manager_close_closes_its_store():
    class Store:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    store = Store()
    manager = DeviceManager(store)
    manager.close()
    assert store.closed == 1


def test_device_credentials_use_config_file_repository(tmp_path):
    class Store:
        def __init__(self) -> None:
            self.saved = None

        def save_device(self, device, *, actor="system"):
            self.saved = device

    config = AppConfigStore(tmp_path / "factoryguard.json")
    store = Store()
    manager = DeviceManager(
        store,
        vault=PlaintextCredentialVault(),
        credential_store=config,
    )
    device = VideoDevice(
        "device-1",
        "site-1",
        "area-1",
        "测试摄像头",
        DeviceProtocol.RTSP,
        "rtsp://camera/live",
        analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        last_probe_at="2026-11-05T10:00:00+00:00",
        last_ok_at="2026-11-05T09:59:00+00:00",
        health_status="degraded",
        health_detail="码流连接超时",
    )
    updated = manager.save_credentials(
        device,
        DeviceCredentials(username="operator", password="plain-secret"),
    )
    assert updated.last_probe_at == device.last_probe_at
    assert updated.last_ok_at == device.last_ok_at
    assert updated.health_status == "degraded"
    assert updated.health_detail == "码流连接超时"
    loaded = manager.credentials_for(updated)
    assert loaded.username == "operator"
    assert loaded.password == "plain-secret"
    blob = config.get_device_credential_blob(updated.credentials_ref or "")
    assert blob is not None
    assert json.loads(blob)["password"] == "plain-secret"


def test_prune_unused_credentials_keeps_referenced_entries(tmp_path):
    path = tmp_path / "factoryguard.json"
    store = AppConfigStore(path)
    store.save_device_credential("cred-used", '{"username": "a", "password": "b"}')
    store.save_device_credential("cred-stale", '{"username": "c", "password": "d"}')

    stale = store.prune_unused_credentials({"cred-used"})

    assert stale == ("cred-stale",)
    assert store.get_device_credential_blob("cred-used") is not None
    assert store.get_device_credential_blob("cred-stale") is None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload["credentials"]) == {"cred-used"}


def test_prune_unused_credentials_is_a_noop_when_all_are_referenced(tmp_path):
    path = tmp_path / "factoryguard.json"
    store = AppConfigStore(path)
    store.save_device_credential("cred-a", '{"username": "a"}')
    store.save_device_credential("cred-b", '{"username": "b"}')

    assert store.prune_unused_credentials({"cred-a", "cred-b"}) == ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload["credentials"]) == {"cred-a", "cred-b"}


def test_stores_for_same_file_share_state(tmp_path):
    path = tmp_path / "factoryguard.json"
    first = AppConfigStore(path)
    second = AppConfigStore(path)

    first.update_section("llm", {"model": "shared-model"})
    assert second.value("llm", "model") == "shared-model"

    second.update_section("gb28181", {"enabled": True})
    assert first.value("gb28181", "enabled") is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["llm"]["model"] == "shared-model"
    assert payload["gb28181"]["enabled"] is True


def test_concurrent_store_writes_do_not_lose_settings(tmp_path):
    path = tmp_path / "factoryguard.json"
    ui_store = AppConfigStore(path)
    worker_store = AppConfigStore(path)

    ui_store.update_section("llm", {"model": "ui-model"})
    worker_store.update_section("gb28181", {"enabled": True, "port": 15060})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["llm"]["model"] == "ui-model"
    assert payload["gb28181"]["enabled"] is True
    assert payload["gb28181"]["port"] == 15060


def test_config_update_rolls_back_memory_when_write_fails(tmp_path, monkeypatch):
    store = AppConfigStore(tmp_path / "factoryguard.json")
    before = store.data

    def fail_write():
        raise ConfigFileError("disk unavailable")

    monkeypatch.setattr(store, "_write", fail_write)
    with pytest.raises(ConfigFileError, match="disk unavailable"):
        store.update_section("gb28181", {"enabled": True, "port": 15060})
    assert store.data == before

    with pytest.raises(ConfigFileError, match="disk unavailable"):
        store.save_device_credential("camera-1", '{"password":"secret"}')
    assert store.get_device_credential_blob("camera-1") is None
    assert store.data == before


def test_device_manager_reports_gb_service_startup_failure():
    class Store:
        def close(self) -> None:
            return None

    class FailingPlatform:
        def __init__(self) -> None:
            self.closed = False

        async def start(self, *, host: str, port: int) -> None:
            raise OSError(f"cannot bind {host}:{port}")

        async def close(self) -> None:
            self.closed = True

    platform = FailingPlatform()
    manager = DeviceManager(Store(), gb_platform=platform)
    with pytest.raises(RuntimeError, match=r"启动失败.*cannot bind"):
        manager.start_gb_service(host="127.0.0.1", port=5060)
    assert platform.closed
    assert manager._gb_thread is None


def test_device_manager_waits_for_gb_service_to_bind():
    class Store:
        def close(self) -> None:
            return None

    class StartingPlatform:
        def __init__(self) -> None:
            self.start_calls = 0
            self.close_calls = 0

        async def start(self, *, host: str, port: int) -> None:
            del host, port
            self.start_calls += 1

        async def close(self) -> None:
            self.close_calls += 1

    platform = StartingPlatform()
    manager = DeviceManager(Store(), gb_platform=platform)
    manager.start_gb_service(host="127.0.0.1", port=5060)
    assert platform.start_calls == 1
    assert manager.gb_service_running is True
    manager.stop_gb_service()
    assert manager.gb_service_running is False
    assert platform.close_calls == 1

    manager.start_gb_service(host="127.0.0.1", port=5060)
    assert manager.gb_service_running is True
    manager.stop_gb_service()
    assert platform.start_calls == 2
    assert platform.close_calls == 2


def test_config_ensure_file_persists_defaults(tmp_path):
    path = tmp_path / "factoryguard.json"
    store = AppConfigStore(path)
    assert not path.exists()
    store.ensure_file()
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["notifications"]["enabled"] is False


def test_legacy_migration_preserves_new_settings_and_moves_device_credentials(
    tmp_path, monkeypatch
):
    vault = PlaintextCredentialVault()

    class LegacyStore:
        def __init__(self):
            self.deleted = []
            self.blobs = {
                "llm-api-key": vault.encode(DeviceCredentials(token="legacy-api-key")),
                "device-cred": vault.encode(
                    DeviceCredentials(username="operator", password="device-password")
                ),
                "gb-sip-password": "not-valid-json",
            }

        def get_device_credential_blob(self, reference):
            return self.blobs.get(reference)

        def load_topology(self):
            return SimpleNamespace(devices=(SimpleNamespace(credentials_ref="device-cred"),))

        def delete_device_credential(self, reference, *, actor="system"):
            self.deleted.append((reference, actor))
            return True

    class LegacySettings:
        cleared = False

        def __init__(self, *_args, **_kwargs):
            self.values = {
                "llm/model": "legacy-model",
                "llm/provider": "openai",
                "llm/timeout": 35,
                "gb/port": 5060,
            }

        def value(self, key, default=None, type=None):
            del type
            return self.values.get(key, default)

        def clear(self):
            self.cleared = True

        def sync(self):
            return None

    monkeypatch.setattr("factoryguard.security.credentials.default_vault", lambda: vault)
    monkeypatch.setattr("PySide6.QtCore.QSettings", LegacySettings)
    config = AppConfigStore(tmp_path / "factoryguard.json")
    config.update_section(
        "llm",
        {
            "enabled": False,
            "provider": "deepseek",
            "endpoint": "https://merchant.example/v1",
            "model": "merchant-model",
            "timeout": 22,
        },
        save=False,
    )
    legacy_store = LegacyStore()

    assert migrate_legacy_merchant_config(legacy_store, config) is True

    llm = config.section("llm")
    assert llm["provider"] == "deepseek"
    assert llm["endpoint"] == "https://merchant.example/v1"
    assert llm["model"] == "merchant-model"
    assert llm["timeout"] == 22
    assert llm["api_key"] == "legacy-api-key"
    migrated = json.loads(config.get_device_credential_blob("device-cred"))
    assert migrated == {"username": "operator", "password": "device-password", "token": ""}
    assert [item[0] for item in legacy_store.deleted] == ["llm-api-key", "device-cred"]
    assert config.value("migration", "legacy_imported") is True


def test_concurrent_config_writes_keep_every_section(tmp_path):
    """Parallel writers must not drop each other's settings."""
    import threading

    path = tmp_path / "factoryguard.json"
    store = AppConfigStore(path)
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            for _ in range(5):
                store.update_section(
                    f"section-{index}",
                    {"value": index},
                )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20.0)

    assert errors == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    for index in range(4):
        assert payload[f"section-{index}"]["value"] == index


def test_config_write_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "factoryguard.json"
    store = AppConfigStore(path)
    store.update_section("llm", {"model": "demo"})

    leftovers = [item.name for item in tmp_path.iterdir() if item.name != "factoryguard.json"]
    assert leftovers == []
