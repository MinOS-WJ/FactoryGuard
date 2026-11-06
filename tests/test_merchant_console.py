from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FACTORYGUARD_DISABLE_TRAY", "1")

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
)

from factoryguard.agent import LLMConfig
from factoryguard.agent.context import CameraView
from factoryguard.config import AppConfigStore, MerchantConfig
from factoryguard.devices.base import DeviceCredentials
from factoryguard.domain.incidents import Incident, IncidentSeverity, IncidentStatus
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
    EventSource,
    NativeEvent,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.notifications.incidents import DeliveryResult, IncidentNotificationConfig
from factoryguard.security.credentials import PlaintextCredentialVault
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.ui.merchant_console import (
    MerchantMainWindow,
    StartupSplash,
    _acquire_single_instance_lock,
    _coerce_event_timestamp,
    _format_local_timestamp,
    _gb_area_choices,
    _gb_imported_channel_keys,
    _launch_unlocked,
)
from factoryguard.ui.merchant_dialogs import (
    AreaDialog,
    CameraAccessDialog,
    CameraEditDialog,
    CameraPreviewWorker,
    LivePreviewDialog,
    LiveWallDialog,
    LiveWallPickerDialog,
    QuickIncidentDialog,
    ResolvedStream,
    StreamHealthWorker,
    _split_url_credentials,
)
from factoryguard.ui.merchant_widgets import AreaCard, BrandBanner, IncidentRow


def _simulator_creation_flags() -> int:
    """Give the simulated camera its own process group on Windows."""
    import os
    import subprocess

    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _stop_simulator_process(process: object) -> None:
    """Stop a simulated camera and the FFmpeg helpers it started."""
    import os
    import signal
    import subprocess

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=10)
            return
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    try:
        process.terminate()
        process.wait(timeout=10)
        return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _free_port() -> int:
    """Reserve an unused local port so parallel tests never collide."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in tuple(application.topLevelWidgets()):
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            continue
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def _seed(database):
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"), Area("area-warehouse", "site-1", "仓库")),
            cameras=(CameraDevice("camera-1", "area-gate", "东门摄像机"),),
        )
    )
    store.create_incident(
        Incident.create(
            title="仓库北门人员闯入",
            description="请安保人员现场确认",
            severity=IncidentSeverity.HIGH,
            site_id="site-1",
            area_id="area-warehouse",
        )
    )
    store.close()


def test_merchant_console_builds_dashboard_and_all_primary_pages(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    )
    app.processEvents()
    assert len(window._page_widgets) == 6
    assert window.page_title.text() == "工作台"
    assert window.metric_cameras.value_label.text() == "0/1"
    assert window.metric_incidents.value_label.text() == "1"
    assert window.page_title.text() == "工作台"
    assert {button.text() for button in window._nav_buttons.values()} == {
        "工作台",
        "实时画面",
        "现场问询",
        "险情中心",
        "区域与设备",
        "系统设置",
    }
    assert "border-radius: 12px" in app.styleSheet()
    for key in ("live", "assistant", "incidents", "devices", "settings"):
        window._switch_page(key)
        app.processEvents()
        assert window.page_title.text() == window.PAGE_META[key][0]
    assert window.incident_list_layout.count() >= 1
    window.close()
    app.processEvents()


def test_area_dialog_edits_name_without_changing_identity(app):
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"),),
    )
    original = topology.areas[0]
    dialog = AreaDialog(topology, area=original)
    dialog.area_edit.setText("东门入口")
    dialog._accept()
    assert dialog.area is not None
    assert dialog.area.id == original.id
    assert dialog.area.site_id == original.site_id
    assert dialog.area.name == "东门入口"
    assert not dialog.site_combo.isEnabled()
    dialog.close()
    app.processEvents()


def test_edit_area_uses_atomic_bundle(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-area-edit.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    area = window._context.topology.get_area("area-gate")
    site = window._context.topology.get_site("site-1")
    saved = []

    class FakeAreaDialog:
        def __init__(self, _topology, _parent, *, area=None):
            self.area = replace(area, name="东门入口") if area is not None else None
            self.site = site

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr("factoryguard.ui.merchant_console.AreaDialog", FakeAreaDialog)
    monkeypatch.setattr(
        window.store,
        "save_area_bundle",
        lambda saved_site, saved_area, **kwargs: saved.append(
            (saved_site.id, saved_area.name, kwargs)
        ),
    )
    monkeypatch.setattr(window, "_refresh_all", lambda *_args, **_kwargs: None)

    window._edit_area(area)

    assert saved == [("site-1", "东门入口", {"actor": "merchant-console"})]
    window.quit_application()
    app.processEvents()


def test_area_dialog_rejects_duplicate_name_within_site(app, monkeypatch):
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(
            Area("area-1", "site-1", "仓库"),
            Area("area-2", "site-1", "东门"),
        ),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    create_dialog = AreaDialog(topology)
    create_dialog.area_edit.setText("仓库")
    create_dialog._accept()
    assert create_dialog.area is None
    assert "同名区域" in warnings[-1]
    create_dialog.close()

    edit_dialog = AreaDialog(topology, area=topology.areas[1])
    edit_dialog.area_edit.setText(" 仓库 ")
    edit_dialog._accept()
    assert edit_dialog.area is None
    assert "同名区域" in warnings[-1]
    edit_dialog.close()
    app.processEvents()


def test_camera_edit_dialog_preserves_ids_and_updates_rtsp_stream(app):
    device = VideoDevice(
        "device-1",
        "site-1",
        "area-gate",
        "东门摄像机",
        DeviceProtocol.RTSP,
        "rtsp://10.0.0.8/old",
        analysis_policy=AnalysisPolicy.NATIVE_ONLY,
    )
    camera = CameraDevice(
        "camera-1",
        "area-gate",
        "东门摄像机",
        address="rtsp://10.0.0.8/old",
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "子码流",
        "rtsp://10.0.0.8/old",
        role=StreamRole.SUB,
        device_id=device.id,
    )
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"),),
        devices=(device,),
        cameras=(camera,),
        streams=(stream,),
    )
    view = CameraView(
        camera=camera,
        site=topology.sites[0],
        area=topology.areas[0],
        device=device,
        streams=(stream,),
        health_status="online",
    )
    dialog = CameraEditDialog(topology, view)
    dialog.name_edit.setText("东门入口摄像机")
    dialog.endpoint_edit.setText("rtsp://10.0.0.9/new")
    dialog.stream_name_edit.setText("主码流")
    dialog._accept()
    assert dialog.device is not None
    assert dialog.camera is not None
    assert dialog.stream is not None
    assert dialog.device.id == device.id
    assert dialog.camera.id == camera.id
    assert dialog.stream.id == stream.id
    assert dialog.camera.name == "东门入口摄像机"
    assert dialog.device.endpoint == "rtsp://10.0.0.9/new"
    assert dialog.stream.url == "rtsp://10.0.0.9/new"
    assert dialog.stream.name == "主码流"
    dialog.close()
    app.processEvents()


def test_onvif_direct_rtsp_edit_updates_the_playable_stream(app):
    device = VideoDevice(
        "device-1",
        "site-1",
        "area-gate",
        "东门摄像机",
        DeviceProtocol.ONVIF,
        "rtsp://10.0.0.8/old",
        analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        health_status="ready",
        last_probe_at="2026-01-01T00:00:00+00:00",
    )
    camera = CameraDevice(
        "camera-1",
        "area-gate",
        "东门摄像机",
        address="rtsp://10.0.0.8/old",
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "设备实时码流",
        "rtsp://10.0.0.8/old",
        role=StreamRole.SUB,
        device_id=device.id,
    )
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"),),
        devices=(device,),
        cameras=(camera,),
        streams=(stream,),
    )
    view = CameraView(
        camera=camera,
        site=topology.sites[0],
        area=topology.areas[0],
        device=device,
        streams=(stream,),
        health_status="online",
    )
    dialog = CameraEditDialog(topology, view)
    dialog.endpoint_edit.setText("rtsp://10.0.0.9/new")

    dialog._accept()

    assert dialog.device is not None
    assert dialog.stream is not None
    assert dialog.device.endpoint == "rtsp://10.0.0.9/new"
    assert dialog.stream.id == stream.id
    assert dialog.stream.url == "rtsp://10.0.0.9/new"
    assert dialog.stream.name == "设备实时码流"
    assert dialog.device.health_status == "unknown"
    assert dialog.device.last_probe_at is None
    assert "等待重新检测" in dialog.device.health_detail
    dialog.close()
    app.processEvents()


def test_camera_edit_dialog_locks_shared_device_area(app):
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"),),
        cameras=(CameraDevice("camera-1", "area-gate", "东门摄像机"),),
    )
    view = CameraView(
        camera=topology.cameras[0],
        site=topology.sites[0],
        area=topology.areas[0],
        device=None,
        streams=(),
    )
    dialog = CameraEditDialog(topology, view, allow_area_change=False)
    assert not dialog.area_combo.isEnabled()
    assert dialog.endpoint_edit.isReadOnly()
    assert dialog.device_id_edit.isReadOnly()
    dialog.close()
    app.processEvents()


def test_background_actions_ignore_reentry_while_thread_is_active(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-background-actions.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    discovery_thread = object()
    window._discovery_thread = discovery_thread  # type: ignore[assignment]
    window.onvif_scan_button.setEnabled(False)
    window._scan_onvif()
    assert window._discovery_thread is discovery_thread

    llm_thread = object()
    window._llm_test_thread = llm_thread  # type: ignore[assignment]
    window.llm_test_button.setEnabled(False)
    window._test_llm()
    assert window._llm_test_thread is llm_thread

    window._discovery_thread = None
    window._llm_test_thread = None
    window.quit_application()
    app.processEvents()


def test_switching_to_data_pages_refreshes_current_view(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-switch-refresh.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    calls: list[str] = []
    monkeypatch.setattr(window, "_refresh_dashboard", lambda: calls.append("dashboard"))
    monkeypatch.setattr(window, "_refresh_live", lambda: calls.append("live"))
    monkeypatch.setattr(window, "_refresh_runtime_labels", lambda: calls.append("runtime"))

    window._switch_page("dashboard")
    window._switch_page("live")
    window._switch_page("settings")

    assert calls == ["dashboard", "live", "runtime"]
    window.quit_application()
    app.processEvents()


def test_periodic_refresh_targets_visible_page_and_preserves_status(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-periodic-refresh.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    calls: list[str] = []
    monkeypatch.setattr(window, "_drain_native_incidents", lambda: None)
    monkeypatch.setattr(window, "_sync_native_subscriptions", lambda: None)
    monkeypatch.setattr(window, "_retry_failed_notifications", lambda: None)
    monkeypatch.setattr(window, "_refresh_live", lambda: calls.append("live"))
    monkeypatch.setattr(window, "_refresh_dashboard", lambda: calls.append("dashboard"))
    monkeypatch.setattr(window, "_refresh_devices", lambda: calls.append("devices"))
    monkeypatch.setattr(window, "_refresh_runtime_labels", lambda: calls.append("runtime"))
    window._current_page = "live"
    window.statusBar().showMessage("险情通知失败，请检查 Webhook", 0)

    window._periodic_refresh()

    assert calls == ["live", "dashboard", "runtime"]
    assert window.statusBar().currentMessage() == "险情通知失败，请检查 Webhook"
    window.quit_application()
    app.processEvents()


def test_merchant_console_has_no_horizontal_overflow_at_supported_sizes(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-layout.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window.show()
    for width, height in ((1120, 640), (1280, 720), (1366, 768), (1440, 900)):
        window.resize(width, height)
        app.processEvents()
        for page in ("dashboard", "live", "assistant", "incidents", "devices", "settings"):
            window._switch_page(page)
            app.processEvents()
            scroll = window._page_widgets[page]
            assert scroll.horizontalScrollBar().maximum() == 0, (
                f"{page} overflows horizontally at {width}x{height}"
            )
    window.quit_application()
    app.processEvents()


def test_merchant_console_close_hides_to_tray_and_quit_is_explicit(app, tmp_path, monkeypatch):
    class FakeTray:
        instance = None

        def __init__(self, **callbacks):
            self.callbacks = callbacks
            self.notifications: list[tuple[str, str]] = []
            self.hidden = False
            FakeTray.instance = self

        @staticmethod
        def available() -> bool:
            return True

        def show(self) -> None:
            return None

        def hide(self) -> None:
            self.hidden = True

        def notify(self, title: str, message: str) -> None:
            self.notifications.append((title, message))

    monkeypatch.delenv("FACTORYGUARD_DISABLE_TRAY", raising=False)
    monkeypatch.setattr("factoryguard.ui.merchant_console.MerchantTrayController", FakeTray)
    database = tmp_path / "merchant-tray.db"
    _seed(database)
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    )
    window.show()
    app.processEvents()
    assert window.can_background()
    window.close()
    app.processEvents()
    assert not window.isVisible()
    assert FakeTray.instance.notifications
    window.quit_application()
    app.processEvents()
    assert FakeTray.instance.hidden


def test_quit_waits_for_running_background_thread(app, tmp_path, monkeypatch):
    class RunningThread:
        def __init__(self) -> None:
            self.running = True
            self.quit_calls = 0

        def isRunning(self) -> bool:
            return self.running

        def quit(self) -> None:
            self.quit_calls += 1

    database = tmp_path / "merchant-thread-shutdown.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    thread = RunningThread()
    window._resolve_thread = thread  # type: ignore[assignment]
    window._allow_quit = True
    window.show()
    app.processEvents()

    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window._quit_pending
    assert thread.quit_calls == 1
    assert window.store._connection is not None

    thread.running = False
    window._poll_background_shutdown()
    app.processEvents()
    assert not window.isVisible()
    assert window.store._connection is None


def test_startup_splash_renders_and_updates_progress(app):
    splash = StartupSplash()
    splash.set_progress("加载区域与设备…", 62)
    image = splash.grab()
    assert not image.isNull()
    assert image.width() == 640
    assert image.height() == 360
    splash.close()
    app.processEvents()


def test_user_svg_banner_is_loaded_from_config(app, tmp_path):
    database = tmp_path / "merchant-banner.db"
    config_path = tmp_path / "factoryguard.json"
    svg_path = tmp_path / "brand.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="100">'
        '<rect width="800" height="100" fill="#123456"/></svg>',
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "branding": {
                    "banner_svg": str(svg_path),
                    "banner_height": 96,
                }
            }
        ),
        encoding="utf-8",
    )
    _seed(database)
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window.show()
    app.processEvents()
    assert window.banner.isVisible()
    assert window.banner.height() == 96
    assert window.banner.source_path == str(svg_path)
    window.quit_application()
    app.processEvents()


def test_default_brand_banner_is_always_visible(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-default-banner.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window.show()
    app.processEvents()
    assert window.banner.isVisible()
    assert not window.banner.is_custom
    assert window.banner.height() == 82
    window.quit_application()
    app.processEvents()


def test_invalid_banner_height_uses_default_height(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-invalid-banner-height.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    config_path.write_text(
        json.dumps({"branding": {"banner_svg": "", "banner_height": "invalid"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window.show()
    app.processEvents()
    assert window.banner.isVisible()
    assert not window.banner.is_custom
    assert window.banner.height() == 82
    window.quit_application()
    app.processEvents()


def test_unsafe_svg_banner_is_rejected_and_default_is_restored(app, tmp_path):
    database = tmp_path / "merchant-unsafe-banner.db"
    config_path = tmp_path / "factoryguard.json"
    svg_path = tmp_path / "unsafe.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="100">'
        '<script>alert(1)</script><rect width="800" height="100"/></svg>',
        encoding="utf-8",
    )
    _seed(database)
    config_path.write_text(
        json.dumps(
            {
                "branding": {
                    "banner_svg": str(svg_path),
                    "banner_height": 96,
                }
            }
        ),
        encoding="utf-8",
    )
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window.show()
    app.processEvents()
    assert window.banner.isVisible()
    assert not window.banner.is_custom
    assert window.banner.source_path == ""
    window.quit_application()
    app.processEvents()


def test_svg_banner_rejects_event_handlers_and_external_resources(app, tmp_path):
    banner = BrandBanner()
    banner.show()
    unsafe_documents = (
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        '<rect width="800" height="100"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<image xlink:href="https://example.test/tracker.png"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<rect style="fill:url(https://example.test/paint)"/></svg>',
    )
    for index, document in enumerate(unsafe_documents):
        path = tmp_path / f"unsafe-{index}.svg"
        path.write_text(document, encoding="utf-8")
        assert not banner.load_svg(str(path))
        assert not banner.is_custom

    safe = tmp_path / "safe.svg"
    safe.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#176B5B"/>'
        '<stop offset="1" stop-color="#17261F"/></linearGradient></defs>'
        '<rect width="800" height="100" fill="url(#g)"/></svg>',
        encoding="utf-8",
    )
    assert banner.load_svg(str(safe))
    banner.close()
    banner.deleteLater()
    app.processEvents()


def test_banner_can_be_configured_from_settings(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-banner-settings.db"
    config_path = tmp_path / "factoryguard.json"
    source_svg = tmp_path / "customer-banner.svg"
    source_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="120">'
        '<rect width="1200" height="120" fill="#111827"/></svg>',
        encoding="utf-8",
    )
    _seed(database)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(source_svg), "SVG 文件 (*.svg)"),
    )
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window.show()
    window._switch_page("settings")
    window.banner_height_spin.setValue(110)
    existing = config_path.parent / "branding" / "banner.svg"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#000000"/></svg>',
        encoding="utf-8",
    )
    window._choose_banner_svg()
    assert window.banner_path_edit.text() == "customer-banner.svg"
    assert existing.read_text(encoding="utf-8").startswith(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#000000"/>'
    )
    window._save_banner_settings()
    app.processEvents()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["branding"]["banner_svg"] == "branding/banner.svg"
    assert payload["branding"]["banner_height"] == 110
    assert window.banner.isVisible()
    assert window.banner.height() == 110
    assert window.banner.is_custom
    assert existing.read_text(encoding="utf-8") == source_svg.read_text(encoding="utf-8")
    window._clear_banner_settings()
    app.processEvents()
    assert window.banner.isVisible()
    assert not window.banner.is_custom
    assert window.banner.height() == 110
    window.quit_application()
    app.processEvents()


def test_settings_numeric_inputs_keep_values_visible(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-spinbox-visible.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("settings")
    window.resize(1280, 720)
    window.show()
    app.processEvents()

    expected = {
        "banner_height_spin": "82 px",
        "notification_email_port_spin": "587",
        "llm_timeout_spin": "35 秒",
        "gb_port_spin": "5060",
    }
    for name, text in expected.items():
        spinbox = getattr(window, name)
        assert spinbox.text() == text
        assert spinbox.lineEdit().width() > 40

    window.quit_application()
    app.processEvents()


def test_notification_fields_follow_switch_hierarchy(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-notification-switches.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("settings")

    assert not window.webhook_enabled_check.isEnabled()
    assert not window.webhook_url_edit.isEnabled()
    window.notification_enabled_check.setChecked(True)
    assert window.webhook_enabled_check.isEnabled()
    assert not window.webhook_url_edit.isEnabled()
    window.webhook_enabled_check.setChecked(True)
    assert window.webhook_url_edit.isEnabled()
    assert not window.notification_email_host_edit.isEnabled()
    window.notification_enabled_check.setChecked(False)
    assert not window.webhook_enabled_check.isEnabled()
    assert not window.webhook_url_edit.isEnabled()

    window.quit_application()
    app.processEvents()


def test_invalid_notification_config_is_visible_and_can_be_corrected(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-invalid-notifications.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    config_path.write_text(
        json.dumps(
            {
                "notifications": {
                    "enabled": True,
                    "webhook": {
                        "enabled": True,
                        "url": "http://hooks.example.test/incident",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._switch_page("settings")

    assert "https" in window._notification_config_error
    assert window.notification_status_badge.text() == "配置错误"
    assert "当前不会发送" in window.notification_status_label.text()

    window.notification_enabled_check.setChecked(True)
    window.webhook_enabled_check.setChecked(True)
    window.webhook_url_edit.setText("https://hooks.example.test/incident")
    window._save_notification_settings()

    assert window._notification_config_error == ""
    assert window.notification_status_badge.text() == "已启用"
    window.quit_application()
    app.processEvents()


def test_master_switch_can_always_disable_notifications(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-disable-invalid-notifications.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._switch_page("settings")
    window.notification_enabled_check.setChecked(True)
    window.webhook_enabled_check.setChecked(True)
    window.webhook_url_edit.setText("http://draft.example.test/hook")
    window.notification_enabled_check.setChecked(False)

    window._save_notification_settings()

    payload = json.loads(config_path.read_text(encoding="utf-8"))["notifications"]
    assert payload["enabled"] is False
    assert payload["webhook"]["enabled"] is True
    assert window._notification_config.available is False
    assert "通知已关闭" in window.notification_status_label.text()
    assert "启用渠道" not in window.notification_status_label.text()
    window.quit_application()
    app.processEvents()


def test_notification_master_without_channels_is_marked_pending(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-notification-pending.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("settings")
    window.notification_enabled_check.setChecked(True)

    window._save_notification_settings()

    assert window.notification_status_badge.text() == "待配置"
    assert "尚未选择通知渠道" in window.notification_status_label.text()
    window.quit_application()
    app.processEvents()


def test_notification_settings_persist_and_keep_secrets(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-notifications.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._switch_page("settings")
    window.notification_enabled_check.setChecked(True)
    window.webhook_enabled_check.setChecked(True)
    window.webhook_url_edit.setText("https://hooks.example.test/factoryguard")
    window.webhook_secret_edit.setText("webhook-secret")
    window.email_enabled_check.setChecked(True)
    window.notification_email_host_edit.setText("smtp.example.test")
    window.notification_email_port_spin.setValue(465)
    window.notification_email_username_edit.setText("factoryguard@example.test")
    window.notification_email_password_edit.setText("mail-secret")
    window.notification_email_sender_edit.setText("factoryguard@example.test")
    window.notification_email_recipients_edit.setText(
        "operator@example.test; security@example.test"
    )
    window.notification_email_security_combo.setCurrentIndex(
        window.notification_email_security_combo.findData("ssl")
    )
    window.sms_enabled_check.setChecked(True)
    window.sms_endpoint_edit.setText("https://sms.example.test/send")
    window.sms_token_edit.setText("sms-secret")
    window.sms_sender_edit.setText("FactoryGuard")
    window.sms_template_edit.setText("incident-alert")
    window.sms_recipients_edit.setText("13800138000; 13900139000")
    window._save_notification_settings()
    payload = json.loads(config_path.read_text(encoding="utf-8"))["notifications"]
    assert payload["enabled"] is True
    assert payload["webhook"]["secret"] == "webhook-secret"
    assert payload["email"]["password"] == "mail-secret"
    assert payload["email"]["recipients"] == [
        "operator@example.test",
        "security@example.test",
    ]
    assert payload["sms"]["token"] == "sms-secret"
    assert payload["sms"]["recipients"] == ["13800138000", "13900139000"]
    assert window._notification_config.available is True
    window.quit_application()
    app.processEvents()

    reopened = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    assert reopened._notification_config.available is True
    assert reopened._notification_config.webhook_secret == "webhook-secret"
    assert reopened._notification_config.email_password == "mail-secret"
    assert reopened._notification_config.sms_token == "sms-secret"
    assert reopened.webhook_secret_edit.text() == ""
    assert reopened.notification_email_password_edit.text() == ""
    reopened.quit_application()
    app.processEvents()


def test_notification_result_is_visible_and_retry_targets_failed_channel(
    app, tmp_path, monkeypatch
):
    database = tmp_path / "merchant-notification-history.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._notification_config = IncidentNotificationConfig(
        enabled=True,
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/factoryguard",
    )
    incident = window.store.list_incidents()[0]
    window.store.record_incident_notification(
        incident.id, "webhook", success=False, detail="timeout"
    )
    window._switch_page("incidents")
    window._selected_incident_id = incident.id
    window._refresh_incident_detail()
    assert "Webhook 失败" in window.incident_notification_label.text()
    window.show()
    app.processEvents()
    assert window.incident_retry_notification_button.isVisibleTo(window)
    assert "timeout" in window.incident_notification_detail_label.text()
    assert window.incident_notification_detail_label.isVisibleTo(window)
    dispatched = []
    monkeypatch.setattr(
        window,
        "_dispatch_incident_notification",
        lambda current, **kwargs: dispatched.append((current, kwargs)),
    )
    window._retry_incident_notification()
    assert dispatched == [(incident, {"channels": ("webhook",)})]
    window.store._connection_or_raise().execute(
        "UPDATE incident_notification_delivery SET updated_at = ? WHERE incident_id = ?",
        ("2026-01-01T00:00:00+00:00", incident.id),
    )
    window.store._connection_or_raise().commit()
    window._retry_failed_notifications()
    assert len(dispatched) == 2
    assert dispatched[1] == (incident, {"channels": ("webhook",)})
    window.quit_application()
    app.processEvents()


def test_disabled_failed_channel_is_not_retried_and_hides_retry_button(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-notification-disabled-retry.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._notification_config = IncidentNotificationConfig(
        enabled=True,
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/factoryguard",
    )
    incident = window.store.list_incidents()[0]
    window.store.record_incident_notification(
        incident.id, "email", success=False, detail="SMTP disabled"
    )
    window._switch_page("incidents")
    window._selected_incident_id = incident.id
    window._refresh_incident_detail()
    window.show()
    app.processEvents()

    assert "邮件 已停用" in window.incident_notification_label.text()
    assert window.incident_retry_notification_button.isHidden()

    window._notification_config = IncidentNotificationConfig()
    window._refresh_incident_notification_status(incident.id)
    assert "通知已停用" in window.incident_notification_label.text()
    assert "历史记录" in window.incident_notification_label.text()
    assert "SMTP disabled" in window.incident_notification_detail_label.text()
    assert window.incident_retry_notification_button.isHidden()

    dispatched = []
    monkeypatch.setattr(
        window,
        "_dispatch_incident_notification",
        lambda current, **kwargs: dispatched.append((current, kwargs)),
    )
    window._retry_incident_notification()
    window._retry_failed_notifications()
    assert dispatched == []
    window.quit_application()
    app.processEvents()


def test_notification_worker_error_is_recorded_for_retry(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-notification-worker-error.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._notification_config = IncidentNotificationConfig(
        enabled=True,
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/factoryguard",
    )
    incident = window.store.list_incidents()[0]
    window._on_notification_finished(incident.id, (), "worker crashed")

    assert window.store.failed_incident_notification_channels(incident.id) == ("webhook",)
    assert window.store.incident_notification_retry_candidates(
        max_attempts=3, min_age_seconds=0
    ) == ((incident.id, ("webhook",)),)
    window.quit_application()
    app.processEvents()


def test_notification_worker_error_only_marks_requested_retry_channels(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-notification-retry-scope.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._notification_config = IncidentNotificationConfig(
        enabled=True,
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/factoryguard",
        email_enabled=True,
        email_host="smtp.example.test",
        email_sender="factoryguard@example.test",
        email_recipients=("operator@example.test",),
    )
    incident = window.store.list_incidents()[0]
    window._notification_channels_inflight[incident.id] = ("webhook",)

    window._on_notification_finished(incident.id, (), "worker crashed")

    assert window.store.failed_incident_notification_channels(incident.id) == ("webhook",)
    deliveries = window.store.list_incident_notifications(incident.id)
    assert [item["channel"] for item in deliveries] == ["webhook"]
    window.quit_application()
    app.processEvents()


def test_native_incident_dispatches_configured_notifications(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-native-notify.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    dispatched = []
    monkeypatch.setattr(
        window,
        "_start_notification_worker",
        lambda config, **kwargs: dispatched.append((config, kwargs)),
    )
    window._notification_config = IncidentNotificationConfig(
        enabled=True,
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/factoryguard",
    )
    event = NativeEvent(
        device_id="camera-1",
        channel_id="camera-1",
        event_code="fielddetection",
        occurred_at=datetime.now(UTC),
        source=EventSource.ONVIF,
        title="仓库区域入侵",
        level="high",
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()
    assert len(dispatched) == 1
    assert dispatched[0][1]["incident"].title == "仓库区域入侵"
    window.quit_application()
    app.processEvents()


def test_dashboard_warns_about_invalid_notification_configuration(app, tmp_path, monkeypatch):
    """A broken webhook address must be visible where operators look first."""
    database = tmp_path / "merchant-dashboard-notify.db"
    config_path = tmp_path / "factoryguard.json"
    config_path.write_text(
        json.dumps(
            {
                "notifications": {
                    "enabled": True,
                    "webhook": {"enabled": True, "url": "http://hooks.example.test/factoryguard"},
                }
            }
        ),
        encoding="utf-8",
    )
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._switch_page("dashboard")
    app.processEvents()

    text = window.hero_text.text()
    assert "通知配置无效" in text
    assert "https://" in text
    assert "系统设置" in text
    window.quit_application()
    app.processEvents()


def test_llm_endpoint_is_configurable_from_settings_page(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-llm-settings.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    )
    window._switch_page("settings")
    assert window.llm_provider_combo.count() >= 4
    assert window.llm_provider_combo.currentData() == ""
    assert window.llm_endpoint_edit.text() == ""
    assert window.llm_endpoint_edit.isVisibleTo(window)
    assert window.llm_model_edit.isVisibleTo(window)
    assert window.llm_key_edit.isVisibleTo(window)
    deepseek = window.llm_provider_combo.findData("deepseek")
    window.llm_provider_combo.setCurrentIndex(deepseek)
    app.processEvents()
    assert "deepseek.com" in window.llm_endpoint_edit.text()
    window.close()
    app.processEvents()


def test_llm_settings_explain_text_only_boundary(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-llm-boundary.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    )
    window._switch_page("settings")
    labels = [label.text() for label in window.findChildren(QLabel)]

    assert any("不读取、不上传视频画面" in text for text in labels)
    assert any("不读取摄像头画面或设备凭据" in text for text in labels)
    assert not any("大模型" in text for text in labels)
    window.quit_application()
    app.processEvents()


def test_invalid_manual_llm_config_does_not_block_startup(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-invalid-llm.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "enabled": True,
                    "endpoint": "https://llm.example.test/v1",
                    "model": "demo-model",
                    "api_key": "secret",
                    "timeout": "invalid",
                }
            }
        ),
        encoding="utf-8",
    )
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._switch_page("settings")
    assert "文字问询配置无效" in window._llm_config_error
    assert window.llm_status_badge.text() == "配置错误"

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    window.llm_endpoint_edit.setText("llm.example.test")
    window.llm_model_edit.setText("demo-model")
    window.llm_key_edit.setText("secret")
    window._test_llm()
    assert "http" in warnings[-1]
    assert window._llm_test_thread is None
    window.quit_application()
    app.processEvents()


def test_llm_settings_persist_in_json_config_file(app, tmp_path):
    database = tmp_path / "merchant-save-llm.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window.llm_provider_combo.setCurrentIndex(window.llm_provider_combo.findData("deepseek"))
    window.llm_model_edit.setText("deepseek-chat")
    window.llm_key_edit.setText("test-secret")
    window.llm_enabled_check.setChecked(True)
    window._save_llm_settings()
    assert window._llm_config.available
    assert window._llm_config.model == "deepseek-chat"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["provider"] == "deepseek"
    assert payload["llm"]["model"] == "deepseek-chat"
    assert payload["llm"]["api_key"] == "test-secret"
    assert payload["llm"]["enabled"] is True
    window.close()
    app.processEvents()
    reopened = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    assert reopened._llm_config.available
    assert reopened._llm_config.model == "deepseek-chat"
    assert reopened._llm_config.api_key == "test-secret"
    reopened.quit_application()
    app.processEvents()


def test_merchant_chat_can_create_incident_without_external_llm(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-chat.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    )
    notifications = []
    monkeypatch.setattr(
        window,
        "_dispatch_incident_notification",
        lambda incident: notifications.append(incident.id),
    )
    window._switch_page("assistant")
    report = "上报险情：东门有人翻越围栏，目前仍在现场"
    window.assistant_input.setPlainText(report)
    window._send_assistant_question()
    QTest.qWait(80)
    app.processEvents()
    window.assistant_input.setPlainText(report)
    window._send_assistant_question()
    QTest.qWait(80)
    app.processEvents()
    incidents = window.store.list_incidents()
    assert len(incidents) == 2
    assert any("东门有人翻越围栏" in incident.title for incident in incidents)
    assert len(notifications) == 1
    assert "未重复通知" in window.statusBar().currentMessage()
    assert "现场问询" in window.incident_audit_label.text()
    assert not window.assistant_send.isEnabled()
    window.assistant_input.setPlainText("继续查询")
    assert window.assistant_send.isEnabled()
    assert window.assistant_send.text() == "发送"
    window.close()
    app.processEvents()


def test_console_camera_delete_refreshes_topology_and_preserves_incidents(
    app, tmp_path, monkeypatch
):
    database = tmp_path / "merchant-delete-camera.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    camera = window._context.cameras[0]
    incident_count = len(window.store.list_incidents())
    window._delete_camera(camera)
    app.processEvents()
    assert window.store.load_topology().cameras == ()
    assert len(window.store.list_incidents()) == incident_count
    assert "已删除" in window.statusBar().currentMessage()
    window.close()
    app.processEvents()


def test_shared_device_delete_keeps_subscription_until_last_channel(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-shared-delete.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=(
                VideoDevice(
                    "device-1",
                    "site-1",
                    "area-1",
                    "NVR",
                    DeviceProtocol.ONVIF,
                    "http://nvr.example.test/onvif",
                ),
            ),
            cameras=(
                CameraDevice("camera-1", "area-1", "通道一", device_id="device-1"),
                CameraDevice("camera-2", "area-1", "通道二", device_id="device-1"),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    stopped = []
    window._native_subscription_manager = SimpleNamespace(
        stop_native_subscription=lambda device_id: stopped.append(device_id),
        close=lambda: None,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)

    first = next(item for item in window._context.cameras if item.camera.id == "camera-1")
    window._delete_camera(first)
    assert stopped == []
    assert [item.id for item in window.store.load_topology().cameras] == ["camera-2"]

    second = next(item for item in window._context.cameras if item.camera.id == "camera-2")
    window._delete_camera(second)
    assert stopped == ["device-1"]
    assert window.store.load_topology().cameras == ()
    window.quit_application()
    app.processEvents()


def test_camera_bundle_rolls_back_credential_when_topology_save_fails(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-camera-bundle.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    device = VideoDevice(
        "device-new",
        "site-1",
        "area-gate",
        "新摄像头",
        DeviceProtocol.RTSP,
        "rtsp://camera/new",
    )
    camera = CameraDevice("camera-new", "area-gate", "新摄像头", device_id=device.id)
    stream = VideoStream("stream-new", "camera-new", "主码流", "rtsp://camera/new")
    monkeypatch.setattr(
        window.store,
        "save_camera_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bundle failed")),
    )

    with pytest.raises(RuntimeError, match="bundle failed"):
        window._save_camera_bundle_with_credentials(
            device,
            camera,
            stream,
            DeviceCredentials(username="operator", password="secret"),
        )

    assert window._config_store.get_device_credential_blob("cred-device-new") is None
    window.quit_application()
    app.processEvents()


def test_camera_url_credentials_are_separated_from_stored_endpoint():
    endpoint, username, password = _split_url_credentials("rtsp://operator:secret@10.0.0.8/live")
    assert endpoint == "rtsp://10.0.0.8/live"
    assert username == "operator"
    assert password == "secret"


def test_rtsp_credentials_are_url_encoded_and_round_trip(app):
    credentials = DeviceCredentials(
        username="operator@example.test",
        password="p@ss:/?#%",
    )

    url = credentials.with_password_in_url("rtsp://camera/live")

    assert url == ("rtsp://operator%40example.test:p%40ss%3A%2F%3F%23%25@camera/live")
    assert _split_url_credentials(url) == (
        "rtsp://camera/live",
        "operator@example.test",
        "p@ss:/?#%",
    )
    assert credentials.with_password_in_url("rtsp://camera/live?token=a@b").endswith(
        "@camera/live?token=a@b"
    )


def test_rtsp_ipv6_credentials_preserve_host_brackets(app):
    assert _split_url_credentials("rtsp://user:pass@[2001:db8::1]:554/live") == (
        "rtsp://[2001:db8::1]:554/live",
        "user",
        "pass",
    )


def test_merchant_console_starts_native_subscriptions_for_onvif_only(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-native-subscriptions.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
            devices=(
                VideoDevice(
                    "device-onvif",
                    "site-1",
                    "area-gate",
                    "ONVIF 摄像机",
                    DeviceProtocol.ONVIF,
                    "http://10.0.0.8/onvif/device_service",
                    capabilities=DeviceCapabilities(native_events=True),
                    analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                ),
                VideoDevice(
                    "device-rtsp",
                    "site-1",
                    "area-gate",
                    "RTSP 摄像机",
                    DeviceProtocol.RTSP,
                    "rtsp://10.0.0.9/live",
                    analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                ),
            ),
            cameras=(
                CameraDevice("camera-onvif", "area-gate", "ONVIF 摄像机", device_id="device-onvif"),
                CameraDevice("camera-rtsp", "area-gate", "RTSP 摄像机", device_id="device-rtsp"),
            ),
        )
    )
    store.close()
    started = []
    closed = []

    class FakeManager:
        def __init__(self, store, *_args, **kwargs):
            self.store = store
            self.on_native_event = kwargs.get("on_native_event")

        def active_native_subscription_ids(self):
            return ()

        def start_native_subscription(self, device, _rules):
            started.append(device.id)

        def stop_native_subscription(self, _device_id):
            return None

        def close(self):
            closed.append(True)
            self.store.close()

    monkeypatch.setattr("factoryguard.ui.merchant_console.DeviceManager", FakeManager)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    assert started == ["device-onvif"]
    assert window._native_subscription_manager is not None
    window.quit_application()
    app.processEvents()
    assert closed


def test_background_alarm_sends_notification_while_window_hidden(app, tmp_path, monkeypatch):
    """A hidden console must still deliver alarms to the configured channels."""
    database = tmp_path / "merchant-background-alarm.db"
    config_path = tmp_path / "factoryguard.json"
    config_path.write_text(
        json.dumps(
            {
                "notifications": {
                    "enabled": True,
                    "webhook": {"enabled": True, "url": "https://hooks.example.test/fg"},
                }
            }
        ),
        encoding="utf-8",
    )
    _seed(database)

    class FakeTray:
        instance = None

        def __init__(self, **callbacks):
            self.callbacks = callbacks
            self.notifications: list[tuple[str, str]] = []
            self.hidden = False
            FakeTray.instance = self

        @staticmethod
        def available() -> bool:
            return True

        def show(self) -> None:
            return None

        def hide(self) -> None:
            self.hidden = True

        def notify(self, title: str, message: str) -> None:
            self.notifications.append((title, message))

    monkeypatch.delenv("FACTORYGUARD_DISABLE_TRAY", raising=False)
    monkeypatch.setattr("factoryguard.ui.merchant_console.MerchantTrayController", FakeTray)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window.show()
    app.processEvents()
    assert window.can_background()

    sent: list[object] = []

    class FakeNotifier:
        def __init__(self, config):
            self.config = config

        def send(self, incident, *, site_name="", area_name="", camera_name="", channels=None):
            sent.append((incident.title, channels))
            return (DeliveryResult("webhook", True, "已发送"),)

        def send_test(self):
            return ()

    monkeypatch.setattr("factoryguard.ui.merchant_console.IncidentNotifier", FakeNotifier)

    # The operator has closed the window, so the console lives in the tray.
    window.close()
    app.processEvents()
    assert not window.isVisible()
    assert window.store._connection is not None

    event = NativeEvent(
        device_id="camera-1",
        channel_id="camera-1",
        event_code="fielddetection",
        occurred_at=datetime.now(UTC),
        source=EventSource.ONVIF,
        title="后台期间仓库有人闯入",
        level="critical",
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()

    titles = [incident.title for incident in window.store.list_incidents()]
    assert "后台期间仓库有人闯入" in titles
    assert sent and sent[0][0] == "后台期间仓库有人闯入"

    # Leaving the console in the tray keeps the store open, so the test has to
    # perform the real shutdown to release the database handle.
    window.quit_application()
    app.processEvents()
    window._poll_background_shutdown()
    app.processEvents()
    assert window.store._connection is None


def test_real_gb_alarm_reaches_the_incident_center(app, tmp_path, monkeypatch):
    """A real GB/T 28181 alarm must become a visible incident."""
    import asyncio
    import subprocess
    import sys
    import threading
    import time

    from factoryguard.devices.gb28181.server import Gb28181Platform

    database = tmp_path / "merchant-gb-e2e.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())

    platform = Gb28181Platform(
        platform_id="340200000000000000",
        domain="3402000000",
        password="secret",
        host="127.0.0.1",
        port=0,
    )
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    actual_port: dict[str, int] = {}

    def run_platform() -> None:
        asyncio.set_event_loop(loop)

        async def boot() -> None:
            _host, port = await platform.start(host="127.0.0.1", port=0)
            actual_port["value"] = port
            ready.set()

        loop.run_until_complete(boot())
        loop.run_forever()

    thread = threading.Thread(target=run_platform, daemon=True)
    thread.start()
    assert ready.wait(5.0)

    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    onvif_port = _free_port()
    rtsp_port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.camera_sim",
            "--count",
            "1",
            "--protocols",
            "gb28181",
            "--rtsp-port",
            str(rtsp_port),
            "--onvif-port",
            str(onvif_port),
            "--sip-host",
            "127.0.0.1",
            "--sip-port",
            str(actual_port["value"]),
            "--password",
            "secret",
            "--wait",
            "15",
            "--send-alarm",
            "--duration",
            "45",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_simulator_creation_flags(),
    )

    def _drain() -> None:
        assert process.stdout is not None
        process.stdout.read()

    threading.Thread(target=_drain, daemon=True).start()
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and not platform.native_events.qsize():
            time.sleep(0.1)
        assert platform.native_events.qsize(), "the simulated device sent no alarm"

        event = platform.native_events.get(timeout=10.0)
        window._handle_gb_native_event(event)
        window._drain_native_incidents()

        incidents = list(window.store.list_incidents())
        titles = [incident.title for incident in incidents]
        assert any("Simulated intrusion alarm" in title for title in titles)
        promoted = next(
            incident for incident in incidents if "Simulated intrusion alarm" in incident.title
        )
        assert promoted.reporter.startswith("GB/T 28181")
        assert promoted.severity is IncidentSeverity.CRITICAL
        # The alarm comes from a device that was never imported, so the
        # incident still names the reporting device instead of staying blank.
        assert promoted.camera_name_snapshot.startswith("3402")
    finally:
        _stop_simulator_process(process)
        if process.stdout is not None:
            process.stdout.close()
        window.quit_application()
        app.processEvents()
        window._poll_background_shutdown()
        app.processEvents()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        loop.run_until_complete(platform.close())
        loop.close()
        if window.store._connection is not None:
            window.store.close()


def test_gb_native_event_is_promoted_to_incident_on_ui_thread(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-native.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    )
    event = NativeEvent(
        device_id="34020000001320000001",
        channel_id="34020000001320000001",
        event_code="fielddetection",
        occurred_at=datetime.now(UTC),
        source=EventSource.GB28181,
        title="仓库区域入侵",
        level="critical",
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()
    incidents = window.store.list_incidents()
    gb_incident = next(incident for incident in incidents if incident.title == "仓库区域入侵")
    assert gb_incident.severity is IncidentSeverity.CRITICAL
    assert gb_incident.reporter.startswith("GB/T 28181")
    assert (
        f"险情 {len(window._context.open_incidents)} 条待处理" in window.sidebar_state_text.text()
    )
    onvif = NativeEvent(
        device_id="camera-1",
        channel_id="camera-1",
        event_code="fielddetection",
        occurred_at=datetime.now(UTC),
        source=EventSource.ONVIF,
        title="ONVIF 区域入侵",
        level="high",
        source_event_id="onvif-event-1",
    )
    window._handle_gb_native_event(onvif)
    window._drain_native_incidents()
    onvif_incident = next(
        incident for incident in window.store.list_incidents() if incident.title == "ONVIF 区域入侵"
    )
    assert onvif_incident.reporter.startswith("ONVIF")
    incident_count = len(window.store.list_incidents())
    inactive = NativeEvent(
        device_id="camera-1",
        channel_id="camera-1",
        event_code="motiondetect",
        occurred_at=datetime.now(UTC),
        source=EventSource.ONVIF,
        title="移动侦测结束",
        level="info",
    )
    window._handle_gb_native_event(inactive)
    window._drain_native_incidents()
    assert len(window.store.list_incidents()) == incident_count
    assert window._native_event_timer.interval() == 1000
    window._selected_incident_id = gb_incident.id
    window._refresh_incident_detail()
    assert "设备告警服务" in window.incident_audit_label.text()
    assert window._native_event_timer.isActive()
    assert "新险情" in window.statusBar().currentMessage()
    window.close()
    app.processEvents()


def test_native_event_dedupe_is_namespaced_per_device(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-native-device-dedupe.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=(
                VideoDevice(
                    "video-1",
                    "site-1",
                    "area-1",
                    "设备一",
                    DeviceProtocol.RTSP,
                    "rtsp://camera/1",
                    device_id="dev-1",
                ),
                VideoDevice(
                    "video-2",
                    "site-1",
                    "area-1",
                    "设备二",
                    DeviceProtocol.RTSP,
                    "rtsp://camera/2",
                    device_id="dev-2",
                ),
            ),
            cameras=(
                CameraDevice(
                    "camera-1", "area-1", "仓库北门", device_id="video-1", channel_id="ch1"
                ),
                CameraDevice(
                    "camera-2", "area-1", "仓库南门", device_id="video-2", channel_id="ch1"
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    occurred_at = datetime.now(UTC)
    for index, title in enumerate(("设备一检测到入侵", "设备二检测到入侵"), start=1):
        window._handle_gb_native_event(
            NativeEvent(
                device_id=f"dev-{index}",
                channel_id="ch1",
                event_code="fielddetection",
                occurred_at=occurred_at,
                source=EventSource.ONVIF,
                title=title,
                level="high",
                source_event_id="vendor-event-1",
            )
        )
    window._drain_native_incidents()

    titles = {incident.title for incident in window.store.list_incidents()}
    assert {"设备一检测到入侵", "设备二检测到入侵"}.issubset(titles)
    window.quit_application()
    app.processEvents()


def test_live_preview_close_ignores_already_deleted_thread(app, monkeypatch):
    class DeletedThread:
        def isRunning(self) -> bool:
            raise RuntimeError("Internal C++ object already deleted")

    class Worker:
        def request_stop(self) -> None:
            return None

    class Event:
        def __init__(self) -> None:
            self.accepted = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.accepted = False

    monkeypatch.setattr(LivePreviewDialog, "_start", lambda self, _url: None)
    dialog = LivePreviewDialog(title="测试摄像头", url="rtsp://demo/stream")
    dialog._thread = DeletedThread()
    dialog._worker = Worker()
    event = Event()
    dialog.closeEvent(event)
    assert event.accepted
    assert dialog._thread is None
    app.processEvents()


def test_live_preview_never_displays_camera_credentials(app, monkeypatch):
    monkeypatch.setattr(LivePreviewDialog, "_start", lambda self, _url: None)
    dialog = LivePreviewDialog(
        title="仓库摄像头",
        url="rtsp://operator:secret@10.0.0.8/live",
    )
    assert "secret" not in dialog.status.text()
    assert "operator" not in dialog.status.text()
    assert dialog.status.text() == "rtsp://10.0.0.8/live"
    dialog.close()
    app.processEvents()


def test_camera_access_dialog_offers_connection_test_for_onvif(app):
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"),),
    )
    dialog = CameraAccessDialog(topology)
    dialog.show()
    app.processEvents()

    assert dialog.test_button.isVisibleTo(dialog)

    dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("gb28181"))
    app.processEvents()
    assert not dialog.test_button.isVisibleTo(dialog)

    dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("rtsp"))
    app.processEvents()
    assert dialog.test_button.isVisibleTo(dialog)

    dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("onvif"))
    app.processEvents()
    assert dialog.test_button.isVisibleTo(dialog)
    assert "测试连接" in dialog.protocol_hint.text()

    dialog.close()
    app.processEvents()


def test_camera_access_dialog_only_shows_fields_relevant_to_protocol(app):
    topology = EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"),),
    )
    dialog = CameraAccessDialog(topology)
    dialog.show()
    app.processEvents()
    assert dialog.endpoint_edit.isVisibleTo(dialog)
    assert not dialog.device_id_edit.isVisibleTo(dialog)
    dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("gb28181"))
    app.processEvents()
    assert not dialog.endpoint_edit.isVisibleTo(dialog)
    assert dialog.device_id_edit.isVisibleTo(dialog)
    dialog.name_edit.setText("东门国标摄像头")
    dialog.device_id_edit.setText("34020000001320000001")
    dialog.channel_edit.setText("34020000001320000001")
    dialog._accept()
    assert dialog.device is not None
    assert dialog.device.endpoint == "gb28181://34020000001320000001"
    dialog.close()
    app.processEvents()


def test_incident_search_matches_camera_area_and_status(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-search.db"
    _seed(database)
    store = SQLiteStore(database)
    incident = Incident.create(
        title="现场异常情况",
        description="等待核实。",
        severity=IncidentSeverity.MEDIUM,
        site_id="site-1",
        area_id="area-gate",
        camera_id="camera-1",
    )
    assert store.create_incident(incident)
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")

    window.incident_search.setText("东门摄像机")
    window._refresh_incidents()
    assert set(window._incident_records) == {incident.id}

    window.incident_search.setText("待处理")
    window._refresh_incidents()
    assert incident.id in window._incident_records

    window.quit_application()
    app.processEvents()


def test_area_card_distinguishes_unknown_cameras_from_offline(app):
    card = AreaCard(
        area_id="area-1",
        name="仓库",
        site_name="星河园区",
        camera_count=2,
        online_count=0,
        unknown_count=2,
        disabled_count=0,
        open_incidents=0,
    )
    card.show()
    app.processEvents()
    labels = [label.text() for label in card.findChildren(QLabel)]
    assert "待检测" in labels
    assert "全部离线" not in labels
    card.close()
    app.processEvents()


def test_single_instance_lock_blocks_second_console(app, tmp_path):
    lock_path = tmp_path / "factoryguard-instance.lock"
    first = _acquire_single_instance_lock(lock_path, notify=False)
    assert first is not None
    try:
        assert _acquire_single_instance_lock(lock_path, notify=False) is None
    finally:
        first.unlock()

    retry = _acquire_single_instance_lock(lock_path, notify=False)
    assert retry is not None
    retry.unlock()


def test_live_wall_limits_streams_and_stops_all_workers(app, monkeypatch):
    class FakePreviewWorker(QThread):
        frame_ready = Signal(object)
        state_changed = Signal(str, bool)

        def __init__(self, _url, parent=None, *, fps=8.0, width=1280, height=720):
            super().__init__(parent)
            self.fps = fps
            self.width = width
            self.height = height
            self.stop_requested = False

        def run(self):
            self.state_changed.emit("已连接测试画面", True)
            while not self.stop_requested:
                self.msleep(10)

        def request_stop(self):
            self.stop_requested = True

    monkeypatch.setattr("factoryguard.ui.merchant_dialogs.CameraPreviewWorker", FakePreviewWorker)
    dialog = LiveWallDialog(
        tuple((f"摄像头 {index}", f"rtsp://camera/{index}") for index in range(6))
    )
    dialog.show()
    app.processEvents()

    assert len(dialog.preview_widgets) == 4
    assert len(dialog._workers) == 4
    assert len(dialog.reconnect_buttons) == 4
    assert all(worker.fps == 4.0 for worker in dialog._workers)
    assert all((worker.width, worker.height) == (960, 540) for worker in dialog._workers)
    reconnect = dialog.reconnect_buttons[0]
    pill = next(
        label for label in dialog.findChildren(QLabel) if label.objectName() == "statusPill"
    )
    dialog._set_tile_state(pill, 0, reconnect, "连接中断", False)
    assert not reconnect.isHidden()
    dialog._active_tiles.discard(0)
    calls = []
    dialog._start_preview = lambda url, preview, pill, index, button: calls.append((url, index))
    reconnect.click()
    assert calls == [("rtsp://camera/0", 0)]
    dialog.close()
    QTest.qWait(250)
    app.processEvents()
    assert not dialog._running_workers()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_live_wall_button_opens_filtered_playable_cameras(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-live-wall.db"
    _seed(database)
    store = SQLiteStore(database)
    store.save_stream(
        VideoStream(
            "stream-1",
            "camera-1",
            "子码流",
            "rtsp://10.0.0.8/sub",
            role=StreamRole.SUB,
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    captured = {}

    class FakeLiveWallDialog:
        MAX_STREAMS = 4

        def __init__(self, previews, parent=None):
            captured["previews"] = previews
            captured["parent"] = parent

        def exec(self):
            return 0

    monkeypatch.setattr("factoryguard.ui.merchant_console.LiveWallDialog", FakeLiveWallDialog)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("live")
    assert window.live_wall_button.isEnabled()
    assert "1 路" in window.live_wall_button.toolTip()

    window._open_live_wall()

    assert captured["previews"] == (("东门摄像机", "rtsp://10.0.0.8/sub"),)
    assert captured["parent"] is window
    window.quit_application()
    app.processEvents()


def test_live_wall_picker_enforces_four_streams_and_returns_selection(app):
    dialog = LiveWallPickerDialog(
        tuple(
            (f"camera-{index}", f"摄像头 {index}", "星河园区 / 重点区域 · RTSP")
            for index in range(1, 7)
        )
    )
    assert dialog.checked_ids() == ("camera-1", "camera-2", "camera-3", "camera-4")
    fifth = dialog.camera_list.item(4)
    fifth.setCheckState(Qt.Checked)
    assert fifth.checkState() == Qt.Unchecked
    assert "最多只能选择 4 路" in dialog.selection_hint.text()

    dialog.camera_list.item(0).setCheckState(Qt.Unchecked)
    fifth.setCheckState(Qt.Checked)
    assert dialog.checked_ids() == ("camera-2", "camera-3", "camera-4", "camera-5")

    dialog._accept()
    assert dialog.selected_camera_ids == ("camera-2", "camera-3", "camera-4", "camera-5")
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_live_wall_uses_user_selected_cameras_when_more_than_four(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-live-wall-picker.db"
    _seed(database)
    store = SQLiteStore(database)
    for index in range(2, 6):
        store.save_camera(CameraDevice(f"camera-{index}", "area-gate", f"摄像头 {index}"))
        store.save_stream(
            VideoStream(
                f"stream-{index}",
                f"camera-{index}",
                "子码流",
                f"rtsp://10.0.0.{index}/sub",
                role=StreamRole.SUB,
            )
        )
    store.save_stream(
        VideoStream(
            "stream-1",
            "camera-1",
            "子码流",
            "rtsp://10.0.0.1/sub",
            role=StreamRole.SUB,
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    captured = {}

    class FakePickerDialog:
        def __init__(self, options, parent=None):
            captured["options"] = options
            self.selected_camera_ids = ("camera-5", "camera-2")

        def exec(self):
            return QDialog.Accepted

    class FakeLiveWallDialog:
        MAX_STREAMS = 4

        def __init__(self, previews, parent=None):
            captured["previews"] = previews

        def exec(self):
            return 0

    monkeypatch.setattr("factoryguard.ui.merchant_console.LiveWallPickerDialog", FakePickerDialog)
    monkeypatch.setattr("factoryguard.ui.merchant_console.LiveWallDialog", FakeLiveWallDialog)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("live")

    window._open_live_wall()

    assert len(captured["options"]) == 5
    assert captured["previews"] == (
        ("摄像头 2", "rtsp://10.0.0.2/sub"),
        ("摄像头 5", "rtsp://10.0.0.5/sub"),
    )
    window.quit_application()
    app.processEvents()


def test_console_can_pause_and_resume_camera(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-camera-toggle.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    camera = window._context.cameras[0]

    window._set_camera_enabled(camera, False)
    disabled = window._context.cameras[0]
    assert disabled.is_disabled
    assert disabled.status_label == "已停用"
    assert disabled.best_stream is None
    assert window.metric_cameras.value_label.text() == "—"
    assert window.metric_cameras.hint_label.text() == "已暂停全部摄像头"
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(text),
    )
    window._scan_stream_health()
    assert "所有摄像头均已暂停接入" in messages[-1]
    window._switch_page("live")
    window.live_status_filter.setCurrentIndex(window.live_status_filter.findData("disabled"))
    window._refresh_live()
    assert window.live_count.text() == "共 1 台"
    window.live_status_filter.setCurrentIndex(window.live_status_filter.findData("unknown"))
    window._refresh_live()
    assert window.live_count.text() == "0 / 1 台"

    window._set_camera_enabled(disabled, True)
    restored = window._context.cameras[0]
    assert not restored.is_disabled
    assert restored.status_label == "待检测"
    assert window.metric_cameras.value_label.text() == "0/1"
    window.quit_application()
    app.processEvents()


def test_paused_camera_ignores_native_events(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-paused-native.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "device-1",
        "site-1",
        "area-1",
        "仓库摄像头",
        DeviceProtocol.ONVIF,
        "http://10.0.0.8/onvif",
        device_id="34020000001320000001",
        capabilities=DeviceCapabilities(native_events=True),
    )
    camera = CameraDevice(
        "camera-1",
        "area-1",
        "仓库北门",
        device_id=device.id,
        channel_id="34020000001320000001",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "子码流",
        "rtsp://10.0.0.8/sub",
        role=StreamRole.SUB,
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=(device,),
            cameras=(camera,),
            streams=(stream,),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    view = window._context.cameras[0]
    window._set_camera_enabled(view, False)

    event = NativeEvent(
        device_id=device.device_id,
        channel_id=camera.channel_id,
        event_code="fielddetection",
        occurred_at=datetime.now(UTC),
        source=EventSource.ONVIF,
        title="暂停期间的区域入侵",
        level="critical",
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()

    assert window.store.list_incidents() == []
    window.quit_application()
    app.processEvents()


def test_single_preview_uses_higher_fps_profile(app):
    worker = CameraPreviewWorker("rtsp://camera/live")
    assert worker.fps == 8.0
    assert (worker.width, worker.height) == (1280, 720)
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_native_event_dedup_cache_is_bounded(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-native-cache.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "NATIVE_EVENT_ID_CACHE_LIMIT", 3)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    assert window._remember_native_event("event-1")
    assert window._remember_native_event("event-2")
    assert window._remember_native_event("event-3")
    assert not window._remember_native_event("event-2")
    assert window._remember_native_event("event-4")
    assert len(window._native_event_key_order) == 3
    assert set(window._native_event_key_order) == {"event-2", "event-3", "event-4"}
    assert window._remember_native_event("event-1")

    window.quit_application()
    app.processEvents()


def test_launch_reports_unexpected_startup_failure(app, tmp_path, monkeypatch):
    captured = {}

    class BrokenWindow:
        def __init__(self, **_kwargs):
            raise OSError("database is locked")

    monkeypatch.setattr("factoryguard.ui.merchant_console.MerchantMainWindow", BrokenWindow)
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, text: captured.update(title=title, text=text),
    )

    code = _launch_unlocked(
        config=MerchantConfig(
            db=str(tmp_path / "broken.db"),
            config_path=str(tmp_path / "factoryguard.json"),
        ),
        argv=["factoryguard", "test"],
    )

    assert code == 2
    assert captured["title"] == "FactoryGuard 启动失败"
    assert "database is locked" in captured["text"]
    assert "被其他程序占用" in captured["text"]


def test_settings_creates_backup_bundle(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-backup.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    captured = {}

    def fake_backup(db_path, config, destination):
        captured["db_path"] = db_path
        captured["config"] = config
        captured["destination"] = destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"backup")
        return destination

    messages = []
    monkeypatch.setattr("factoryguard.ui.merchant_console.create_backup", fake_backup)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: messages.append((title, text)),
    )

    window._create_backup()

    assert captured["config"] == window._config_store.path
    assert captured["config"].is_file()
    assert captured["destination"].parent == tmp_path / "backups"
    assert captured["destination"].suffix == ".zip"
    assert messages[-1][0] == "备份完成"
    assert "明文配置凭据" in messages[-1][1]
    assert "1 个" in window.backup_status_label.text()
    assert "最近" in window.backup_status_label.text()
    window.quit_application()
    app.processEvents()


def test_automatic_health_scan_is_scheduled_and_silent_when_empty(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-auto-health.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    assert window._health_refresh_timer.isActive()
    assert window._health_refresh_timer.interval() == 10 * 60 * 1000
    assert window._initial_health_timer.isActive()
    assert window._initial_health_timer.isSingleShot()
    assert window._initial_health_timer.interval() == 60 * 1000
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(text),
    )

    window._auto_scan_stream_health()
    assert messages == []
    window._scan_stream_health()
    assert "当前没有可检测的启用码流" in messages[-1]

    window.quit_application()
    app.processEvents()
    assert not window._health_refresh_timer.isActive()
    assert not window._initial_health_timer.isActive()


def test_health_scan_does_not_report_cameras_offline_when_ffmpeg_is_missing(
    app, tmp_path, monkeypatch
):
    database = tmp_path / "merchant-health-no-ffmpeg.db"
    _seed(database)
    store = SQLiteStore(database)
    store.save_stream(
        VideoStream(
            "stream-1",
            "camera-1",
            "子码流",
            "rtsp://10.0.0.8/sub",
            role=StreamRole.SUB,
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    monkeypatch.setattr("factoryguard.media.ffmpeg.find_ffmpeg", lambda: None)
    monkeypatch.setattr("factoryguard.ui.merchant_dialogs.find_ffmpeg", lambda: None)

    window._scan_stream_health()

    assert window._health_thread is None
    assert "未找到 FFmpeg" in warnings[-1]
    statuses: list[dict[str, str]] = []
    worker = StreamHealthWorker(
        db_path=str(database),
        stream_specs=(("stream-1", "rtsp://10.0.0.8/sub"),),
    )
    worker.finished.connect(statuses.append)
    worker.run()
    assert statuses == [{"stream-1": "unknown"}]
    store = SQLiteStore(database)
    assert store.list_stream_health()["stream-1"]["status"] == "unknown"
    store.close()
    worker.deleteLater()
    window.quit_application()
    app.processEvents()


def test_restore_backup_ui_validates_stages_and_quits(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-restore-ui.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    source = tmp_path / "backup.zip"
    source.write_bytes(b"zip")
    captured = {}
    info = SimpleNamespace(
        version="2.0.0",
        created_at="2026-11-05T12:00:00+00:00",
        config_included=True,
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(source), ""))
    monkeypatch.setattr("factoryguard.ui.merchant_console.inspect_backup", lambda _path: info)
    monkeypatch.setattr(
        "factoryguard.ui.merchant_console.stage_restore",
        lambda archive, db_path: captured.update(archive=archive, db_path=db_path),
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: messages.append((title, text)),
    )
    quit_calls = []
    original_quit = window.quit_application

    def record_quit() -> None:
        quit_calls.append(True)
        original_quit()

    monkeypatch.setattr(window, "quit_application", record_quit)

    window._choose_backup_to_restore()

    assert captured == {"archive": str(source), "db_path": str(database)}
    assert messages[-1][0] == "恢复请求已保存"
    assert "重新启动" in messages[-1][1]
    assert quit_calls == [True]
    assert window.store._connection is None


def test_startup_restore_failure_preserves_current_data(app, tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "factoryguard.ui.merchant_console.apply_pending_restore",
        lambda *_args: (_ for _ in ()).throw(ValueError("损坏的备份包")),
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, text: captured.update(title=title, text=text),
    )

    code = _launch_unlocked(
        config=MerchantConfig(
            db=str(tmp_path / "restore.db"),
            config_path=str(tmp_path / "factoryguard.json"),
        ),
        argv=["factoryguard", "test"],
    )

    assert code == 2
    assert captured["title"] == "恢复备份失败"
    assert "原数据保持不变" in captured["text"]


def test_duplicate_camera_connection_is_detected_before_save(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-duplicate-camera.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "device-1",
        "site-1",
        "area-1",
        "东门摄像头",
        DeviceProtocol.RTSP,
        "rtsp://10.0.0.8/live",
    )
    camera = CameraDevice(
        "camera-1",
        "area-1",
        "东门入口",
        address=device.endpoint,
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "主码流",
        device.endpoint,
        role=StreamRole.MAIN,
        device_id=device.id,
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(device,),
            cameras=(camera,),
            streams=(stream,),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    duplicate_camera = CameraDevice(
        "camera-new",
        "area-1",
        "重复入口",
        address=device.endpoint,
        device_id="device-new",
        channel_id="ch1",
    )

    duplicate = window._find_duplicate_camera(device, duplicate_camera, stream)

    assert duplicate is not None
    assert duplicate.camera.id == "camera-1"
    second_device = VideoDevice(
        "device-2",
        "site-1",
        "area-1",
        "西门摄像头",
        DeviceProtocol.RTSP,
        "rtsp://10.0.0.9/live",
    )
    different_channel = CameraDevice(
        "camera-channel-2",
        "area-1",
        "第二通道",
        address=second_device.endpoint,
        device_id=second_device.id,
        channel_id="ch2",
    )
    assert (
        window._find_duplicate_camera(
            second_device,
            different_channel,
            VideoStream("stream-2", different_channel.id, "主码流", second_device.endpoint),
        )
        is None
    )
    window.quit_application()
    app.processEvents()


def test_closed_incident_can_be_reopened_with_confirmation(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-reopen-incident.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    incident = window.store.list_incidents()[0]
    assert window.store.update_incident_status(incident.id, IncidentStatus.RESOLVED, actor="test")
    window._refresh_all()
    window._switch_page("incidents")
    window._selected_incident_id = incident.id
    window._refresh_incident_detail()
    window.show()
    app.processEvents()
    assert window.incident_reopen_button.isVisibleTo(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)

    window._reopen_incident()

    reopened = window.store.get_incident(incident.id)
    assert reopened is not None
    assert reopened.status is IncidentStatus.OPEN
    audit = (
        window.store._connection_or_raise()
        .execute(
            """SELECT detail FROM audit_log
               WHERE action = 'incident.status.changed' AND resource = ?
               ORDER BY id DESC LIMIT 1""",
            (incident.id,),
        )
        .fetchone()
    )
    assert json.loads(audit["detail"])["note"] == "reopened from resolved"
    assert "创建险情" in window.incident_audit_label.text()
    assert "重新打开" in window.incident_audit_label.text()
    assert "当前用户" in window.incident_audit_label.text()
    assert window.incident_reopen_button.isHidden()
    window.quit_application()
    app.processEvents()


def test_timestamp_display_uses_local_timezone():
    value = "2026-11-05T12:00:00+00:00"
    expected = datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert _format_local_timestamp(value) == expected
    assert _format_local_timestamp("") == "时间未知"
    assert _format_local_timestamp("broken", fallback="未知时间") == "未知时间"


def test_live_preview_can_reconnect_after_stream_failure(app, monkeypatch):
    monkeypatch.setattr(LivePreviewDialog, "_start", lambda self, _url: None)
    dialog = LivePreviewDialog(title="仓库摄像头", url="rtsp://camera/live")
    dialog.show()
    app.processEvents()

    dialog._on_state("连接中断", False)
    assert not dialog.reconnect_button.isHidden()
    assert dialog.live_pill.text() == "连接中断"
    calls = []
    dialog._start = lambda url: calls.append(url)
    dialog._reconnect()

    assert calls == ["rtsp://camera/live"]
    assert dialog.status.text() == "正在重新连接…"
    dialog.close()
    app.processEvents()


def test_sidebar_reports_partially_disabled_cameras(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-sidebar-disabled.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            cameras=(
                CameraDevice("camera-1", "area-1", "东门入口"),
                CameraDevice("camera-2", "area-1", "西门入口"),
            ),
        )
    )
    store.set_camera_enabled("camera-2", False, actor="test")
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    sidebar_text = window.sidebar_state_text.text()
    assert "0/1 在线" in sidebar_text
    assert "1 停用" in sidebar_text
    window.quit_application()
    app.processEvents()


def test_sidebar_distinguishes_offline_and_unverified_cameras(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-sidebar-health.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            cameras=(
                CameraDevice("camera-1", "area-1", "入口摄像头"),
                CameraDevice("camera-2", "area-1", "仓库摄像头"),
                CameraDevice("camera-3", "area-1", "停车场摄像头"),
            ),
            streams=(
                VideoStream("stream-1", "camera-1", "子码流", "rtsp://camera/1"),
                VideoStream("stream-2", "camera-2", "子码流", "rtsp://camera/2"),
                VideoStream("stream-3", "camera-3", "子码流", "rtsp://camera/3"),
            ),
        )
    )
    now = datetime.now(UTC)
    store.set_stream_health("stream-1", "online", "1280×720", updated_at=now, actor="test")
    store.set_stream_health("stream-2", "offline", "连接超时", updated_at=now, actor="test")
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    sidebar_text = window.sidebar_state_text.text()
    assert "摄像头 1/3 在线" in sidebar_text
    assert "1 离线" in sidebar_text
    assert "1 待检测" in sidebar_text
    assert window.sidebar_ai_state.text() == "● 需要关注"
    window.quit_application()
    app.processEvents()


def test_sidebar_does_not_call_unverified_camera_status_normal(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-sidebar-unknown.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            cameras=(CameraDevice("camera-1", "area-1", "入口摄像头"),),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    assert "1 待检测" in window.sidebar_state_text.text()
    assert window.sidebar_ai_state.text() == "● 状态待确认"
    window.quit_application()
    app.processEvents()


def test_dashboard_summary_does_not_depend_on_optional_text_service(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-dashboard-text-service.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    local_title = window.hero_title.text()
    window._llm_config = LLMConfig(
        endpoint="https://example.test/v1/chat/completions",
        model="demo-model",
        api_key="secret",
        enabled=True,
    )
    window._refresh_dashboard()

    assert local_title == "现场已接入，安全态势实时汇总"
    assert window.hero_title.text() == local_title
    window.quit_application()
    app.processEvents()


def test_pending_low_incident_is_not_reported_as_running_normally(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-pending-incident-status.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            cameras=(CameraDevice("camera-1", "area-1", "仓库北门"),),
            streams=(VideoStream("stream-1", "camera-1", "子码流", "rtsp://camera/1"),),
        )
    )
    store.set_stream_health(
        "stream-1",
        "online",
        "1280×720",
        updated_at=datetime.now(UTC),
        actor="test",
    )
    store.create_incident(
        Incident.create(
            title="需要核实的现场情况",
            severity=IncidentSeverity.MEDIUM,
            site_id="site-1",
            area_id="area-1",
            camera_id="camera-1",
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    assert window.hero_state_pill.text() == "有待处理"
    assert window.sidebar_ai_state.text() == "● 有待处理险情"
    window.quit_application()
    app.processEvents()


def test_camera_delete_refreshes_even_when_credential_cleanup_fails(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-delete-credential-failure.db"
    config_path = tmp_path / "factoryguard.json"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(
                VideoDevice(
                    "device-1",
                    "site-1",
                    "area-1",
                    "东门摄像头",
                    DeviceProtocol.RTSP,
                    "rtsp://camera/1",
                    credentials_ref="cred-device-1",
                ),
            ),
            cameras=(
                CameraDevice(
                    "camera-1",
                    "area-1",
                    "东门摄像头",
                    device_id="device-1",
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._config_store.save_device_credential("cred-device-1", "secret")
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    monkeypatch.setattr(
        window._config_store,
        "delete_device_credential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("配置文件被占用")),
    )

    window._delete_camera(window._context.cameras[0])

    assert window.store.load_topology().cameras == ()
    assert "已删除" in window.statusBar().currentMessage()
    assert "凭据清理失败" in window.statusBar().currentMessage()
    assert warnings and "本地配置文件中的设备凭据未能清理" in warnings[-1]
    window.quit_application()
    app.processEvents()


def test_area_card_treats_nonstandard_health_as_pending(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-area-pending-health.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            cameras=(CameraDevice("camera-1", "area-1", "仓库北门"),),
            streams=(VideoStream("stream-1", "camera-1", "子码流", "rtsp://camera/1"),),
        )
    )
    store.set_stream_health(
        "stream-1",
        "probing",
        "正在确认连接",
        updated_at=datetime.now(UTC),
        actor="test",
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("devices")
    app.processEvents()

    cards = window.findChildren(AreaCard)
    labels = [label.text() for card in cards for label in card.findChildren(QLabel)]
    assert "待检测" in labels
    assert "全部离线" not in labels
    window.quit_application()
    app.processEvents()


def test_invalid_manual_gb_config_does_not_block_startup(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-invalid-gb.db"
    config_path = tmp_path / "factoryguard.json"
    _seed(database)
    config_path.write_text(
        json.dumps(
            {
                "gb28181": {
                    "enabled": True,
                    "platform_id": "34020000002000000001",
                    "domain": "3402000000",
                    "port": "invalid",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    window._switch_page("settings")

    assert "国标配置无效" in window._gb_error
    assert window.gb_status_badge.text() == "配置错误"
    assert window._device_manager is None
    window.quit_application()
    app.processEvents()


def test_gb_start_failure_survives_runtime_refresh(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-start-failure.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    monkeypatch.setattr(
        "factoryguard.ui.merchant_console.Gb28181Platform",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("端口被占用")),
    )

    window._start_gb_service(
        platform_id="34020000002000000001",
        domain="3402000000",
        port=5060,
    )
    window._refresh_runtime_labels()

    assert "端口被占用" in window._gb_error
    assert "启动失败" in window.gb_status_label.text()
    assert window.gb_status_badge.text() == "启动失败"
    assert warnings
    window.quit_application()
    app.processEvents()


def test_gb_area_choices_disambiguate_duplicate_names():
    topology = EntityRegistry(
        sites=(Site("site-a", "门店"), Site("site-b", "门店")),
        areas=(
            Area("area-a", "site-a", "东门"),
            Area("area-b", "site-b", "东门"),
        ),
    )

    choices = _gb_area_choices(topology)

    labels = [label for label, _area in choices]
    assert len(labels) == len(set(labels)) == 2
    assert [area.id for _label, area in choices] == ["area-a", "area-b"]
    assert all(area.id in label for label, area in choices)


def test_gb_import_uses_selected_area_and_atomic_batch(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-import.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "门店"), Site("site-b", "门店")),
            areas=(
                Area("area-a", "site-a", "东门"),
                Area("area-b", "site-b", "东门"),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        def gb_registered_devices(self):
            return (
                {
                    "device_id": "34020000001320000001",
                    "channels": (
                        {"id": "1", "name": "东门"},
                        {"id": "2", "name": "西门"},
                    ),
                },
            )

        def close(self):
            return None

    saved = []
    refreshed = []
    window._device_manager = FakeDeviceManager()
    monkeypatch.setattr(
        window.store,
        "save_device_camera_batch",
        lambda devices, cameras, actor: saved.append((devices, cameras, actor)),
    )
    monkeypatch.setattr(window, "_refresh_all", lambda: refreshed.append(True))
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda _parent, _title, _label, items, _index, _editable: (items[1], True),
    )

    window._import_gb_registered_devices()

    assert len(saved) == 1
    devices, cameras, actor = saved[0]
    assert actor == "merchant-console"
    assert len(devices) == 1
    assert len(cameras) == 2
    assert devices[0].area_id == "area-b"
    assert all(camera.area_id == "area-b" for camera in cameras)
    assert refreshed == [True]
    window.quit_application()
    app.processEvents()


def test_gb_status_waits_for_registration_when_no_device_joined(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-waiting.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        gb_service_running = True

        def gb_registered_devices(self):
            return ()

        def close(self):
            return None

    window._device_manager = FakeDeviceManager()
    window._gb_active_port = 5060

    window._refresh_runtime_labels()

    assert window.gb_status_badge.text() == "等待注册"
    assert window.gb_status_badge.property("state") == "unknown"
    assert "等待设备注册" in window.gb_status_label.text()
    assert "5060" in window.gb_status_label.text()
    assert not window.gb_import_button.isEnabled()
    window.quit_application()
    app.processEvents()


def test_gb_status_reports_pending_import_until_channels_are_added(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-pending.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        gb_service_running = True

        def gb_registered_devices(self):
            return (
                {
                    "device_id": "34020000001320000001",
                    "channels": (
                        {"id": "1", "name": "大门"},
                        {"id": "2", "name": "仓库"},
                    ),
                },
            )

        def close(self):
            return None

    window._device_manager = FakeDeviceManager()
    window._gb_active_port = 5060

    window._refresh_runtime_labels()

    assert window.gb_status_badge.text() == "待导入"
    assert window.gb_status_badge.property("state") == "unknown"
    assert "2 个通道" in window.gb_status_label.text()
    assert "已导入 0 个" in window.gb_status_label.text()
    assert window.gb_import_button.isEnabled()
    window.quit_application()
    app.processEvents()


def test_gb_status_confirms_imported_channels(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-imported.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "门店"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(
                VideoDevice(
                    id="device-gb-1",
                    site_id="site-1",
                    area_id="area-1",
                    name="国标 NVR",
                    protocol="gb28181",
                    endpoint="gb28181://34020000001320000001",
                    device_id="34020000001320000001",
                ),
            ),
            cameras=(
                CameraDevice(
                    id="camera-gb-1",
                    area_id="area-1",
                    name="大门",
                    device_id="device-gb-1",
                    channel_id="1",
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        gb_service_running = True

        def gb_registered_devices(self):
            return (
                {
                    "device_id": "34020000001320000001",
                    "channels": ({"id": "1", "name": "大门"},),
                },
            )

        def close(self):
            return None

    window._device_manager = FakeDeviceManager()
    window._gb_active_port = 5060

    window._refresh_runtime_labels()

    assert window.gb_status_badge.text() == "已接入"
    assert window.gb_status_badge.property("state") == "online"
    assert "1 个通道已全部导入" in window.gb_status_label.text()
    window.quit_application()
    app.processEvents()


def test_gb_imported_channel_keys_ignores_non_gb_devices():
    topology = EntityRegistry(
        sites=(Site("site-1", "门店"),),
        areas=(Area("area-1", "site-1", "东门"),),
        devices=(
            VideoDevice(
                id="device-onvif",
                site_id="site-1",
                area_id="area-1",
                name="网络摄像机",
                protocol="onvif",
                endpoint="onvif://192.168.1.10",
                device_id="34020000001320000001",
            ),
        ),
        cameras=(
            CameraDevice(
                id="camera-1",
                area_id="area-1",
                name="大门",
                device_id="device-onvif",
                channel_id="1",
            ),
        ),
    )

    assert _gb_imported_channel_keys(topology) == set()


def test_coerce_event_timestamp_accepts_string_and_datetime():
    from datetime import datetime as _dt

    aware = _dt(2026, 11, 6, 8, 0, tzinfo=UTC)
    assert _coerce_event_timestamp(aware) == aware
    assert _coerce_event_timestamp("2026-11-06T08:00:00+00:00") == aware
    naive = _dt(2026, 11, 6, 8, 0)
    assert _coerce_event_timestamp(naive).tzinfo is not None
    assert _coerce_event_timestamp("not-a-time").tzinfo is not None
    assert _coerce_event_timestamp(None).tzinfo is not None


def test_bad_native_event_does_not_drop_following_alarms(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-native-event-isolation.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    statuses: list[str] = []
    monkeypatch.setattr(
        window.statusBar(),
        "showMessage",
        lambda message, timeout=0: statuses.append(message),
    )

    broken = SimpleNamespace(
        device_id="broken-device",
        channel_id="1",
        event_code="motiondetect",
        title="坏事件",
        level="high",
        occurred_at="2026-11-06T08:00:00+00:00",
        payload={},
        source="gb28181",
        site_id=None,
        area_id=None,
    )
    broken.title = None
    broken.payload = "not-a-mapping"
    good = SimpleNamespace(
        device_id="34020000001320000001",
        channel_id="1",
        event_code="motiondetect",
        title="东门人员闯入",
        level="high",
        occurred_at="2026-11-06T08:00:00+00:00",
        payload={},
        source="gb28181",
        site_id=None,
        area_id=None,
    )
    window._handle_gb_native_event(broken)
    window._handle_gb_native_event(good)
    window._drain_native_incidents()

    titles = [incident.title for incident in window.store.list_incidents()]
    assert "东门人员闯入" in titles
    assert any("设备告警处理失败" in text for text in statuses)
    window.quit_application()
    app.processEvents()


def test_onvif_alarm_with_profile_suffix_reaches_its_camera(app, tmp_path, monkeypatch):
    """ONVIF property operations carry a ``/Profile_x`` suffix."""
    database = tmp_path / "merchant-onvif-profile-suffix.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "device-onvif",
        "site-1",
        "area-gate",
        "东门设备",
        DeviceProtocol.ONVIF,
        "http://10.0.0.8/onvif",
        device_id="sim-cam-01",
        capabilities=DeviceCapabilities(native_events=True),
    )
    camera = CameraDevice(
        "camera-onvif",
        "area-gate",
        "东门机",
        device_id=device.id,
        channel_id="sim-cam-01",
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
            devices=(device,),
            cameras=(camera,),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    event = NativeEvent(
        device_id="sim-cam-01",
        channel_id="sim-cam-01/Profile_1",
        event_code="motiondetect",
        occurred_at=datetime.now(UTC),
        source=EventSource.ONVIF,
        title="东门有人闯入",
        level="critical",
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()

    incidents = list(window.store.list_incidents())
    assert len(incidents) == 1
    assert incidents[0].camera_id == camera.id
    assert incidents[0].area_id == "area-gate"
    window.quit_application()
    app.processEvents()


def test_gb_alarm_with_channel_suffix_reaches_its_camera(app, tmp_path, monkeypatch):
    """GB alarms report ``<device-id>:<ss>``; the camera must still match."""
    database = tmp_path / "merchant-gb-channel-suffix.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "device-gb",
        "site-1",
        "area-1",
        "国标 NVR",
        DeviceProtocol.GB28181,
        "gb28181://34020000001320000001",
        device_id="34020000001320000001",
    )
    camera = CameraDevice(
        "camera-gb",
        "area-1",
        "仓库北门",
        device_id=device.id,
        channel_id="34020000001320000001",
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=(device,),
            cameras=(camera,),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    event = NativeEvent(
        device_id="34020000001320000001",
        channel_id="34020000001320000001:0",
        event_code="motiondetect",
        occurred_at=datetime.now(UTC),
        source=EventSource.GB28181,
        title="仓库北门有人闯入",
        level="critical",
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()

    incidents = list(window.store.list_incidents())
    assert len(incidents) == 1
    assert incidents[0].camera_id == camera.id
    assert incidents[0].area_id == "area-1"
    window.quit_application()
    app.processEvents()


def test_imported_gb_channel_receives_alarm_in_selected_area(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-import-alarm.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        gb_service_running = True

        def gb_registered_devices(self):
            return (
                {
                    "device_id": "34020000001320000001",
                    "channels": ({"id": "1", "name": "仓库北门"},),
                },
            )

        def close(self):
            return None

    window._device_manager = FakeDeviceManager()
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda _parent, _title, _label, items, _index, _editable: (items[0], True),
    )

    window._import_gb_registered_devices()

    imported = next(item for item in window._context.topology.cameras if item.channel_id == "1")
    assert imported.area_id == "area-1"

    event = SimpleNamespace(
        device_id="34020000001320000001",
        channel_id="1",
        event_code="motiondetect",
        title="仓库北门人员闯入",
        level="high",
        occurred_at=datetime.now(UTC).isoformat(),
        payload={},
        source="gb28181",
        site_id=None,
        area_id=None,
    )
    window._handle_gb_native_event(event)
    window._drain_native_incidents()

    incidents = list(window.store.list_incidents())
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.area_id == "area-1"
    assert incident.camera_id == imported.id
    assert incident.title == "仓库北门人员闯入"
    window.quit_application()
    app.processEvents()


def test_gb_import_rejects_device_already_in_another_area(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-import-duplicate.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(
                Area("area-1", "site-1", "仓库"),
                Area("area-2", "site-1", "东门"),
            ),
            devices=(
                VideoDevice(
                    "device-gb-existing",
                    "site-1",
                    "area-1",
                    "既有国标设备",
                    DeviceProtocol.GB28181,
                    "gb28181://34020000001320000001",
                    device_id="34020000001320000001",
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        gb_service_running = True

        def gb_registered_devices(self):
            return (
                {
                    "device_id": "34020000001320000001",
                    "channels": ({"id": "9", "name": "重复通道"},),
                },
            )

        def close(self):
            return None

    warnings = []
    saved = []
    window._device_manager = FakeDeviceManager()
    monkeypatch.setattr(
        window.store,
        "save_device_camera_batch",
        lambda devices, cameras, actor: saved.append((devices, cameras)),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda _parent, _title, _label, items, _index, _editable: (items[1], True),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )

    window._import_gb_registered_devices()

    assert saved == []
    assert len(warnings) == 1
    assert "未导入任何通道" in warnings[0]
    assert "已属于其他场所或区域" in warnings[0]
    window.quit_application()
    app.processEvents()


def test_gb_import_failure_reports_atomic_rollback(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-gb-import-failure.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeDeviceManager:
        def gb_registered_devices(self):
            return (
                {
                    "device_id": "34020000001320000001",
                    "channels": ({"id": "1", "name": "仓库"},),
                },
            )

        def close(self):
            return None

    warnings = []
    refreshed = []
    window._device_manager = FakeDeviceManager()

    def fail_batch(_devices, _cameras, *, actor):
        raise RuntimeError("数据库写入失败")

    monkeypatch.setattr(window.store, "save_device_camera_batch", fail_batch)
    monkeypatch.setattr(window, "_refresh_all", lambda: refreshed.append(True))
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda _parent, _title, _label, items, _index, _editable: (items[0], True),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )

    window._import_gb_registered_devices()

    assert len(warnings) == 1
    assert "未导入任何通道" in warnings[0]
    assert "数据库写入失败" in warnings[0]
    assert refreshed == []
    window.quit_application()
    app.processEvents()


def test_local_maintenance_prunes_unused_credentials(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-maintenance-credentials.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    monkeypatch.setattr(
        window.store,
        "prune_history",
        lambda **_kwargs: {"events": 0, "audit": 0, "outbox": 0},
    )
    monkeypatch.setattr(window.store, "checkpoint_wal", lambda: None)
    monkeypatch.setattr(
        window.store,
        "referenced_credential_refs",
        lambda: ("cred-used",),
    )
    pruned: list[set[str]] = []
    monkeypatch.setattr(
        window._config_store,
        "prune_unused_credentials",
        lambda referenced: pruned.append(set(referenced)) or ("cred-stale",),
    )

    window._run_local_maintenance()

    assert pruned == [{"cred-used"}]
    window.quit_application()
    app.processEvents()


def test_incident_list_is_bounded_and_reports_the_remainder(app, tmp_path, monkeypatch):
    """A long history must not build thousands of widgets."""
    database = tmp_path / "merchant-incident-limit.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
        )
    )
    total = MerchantMainWindow.INCIDENT_LIST_LIMIT + 25
    for index in range(total):
        store.create_incident(
            Incident.create(title=f"批量事件 {index}", severity=IncidentSeverity.LOW),
            actor="tester",
        )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    layout = window.incident_list_layout
    rows = [
        layout.itemAt(index).widget()
        for index in range(layout.count())
        if isinstance(layout.itemAt(index).widget(), IncidentRow)
    ]
    assert len(rows) == MerchantMainWindow.INCIDENT_LIST_LIMIT

    labels = [
        layout.itemAt(index).widget().text()
        for index in range(layout.count())
        if isinstance(layout.itemAt(index).widget(), QLabel)
    ]
    assert any("未显示" in text for text in labels)
    window.quit_application()
    app.processEvents()


def test_dead_alarm_subscription_is_reported_to_the_operator(app, tmp_path, monkeypatch):
    """A subscription that died must not disappear without a trace."""
    database = tmp_path / "merchant-subscription-error.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
            devices=(
                VideoDevice(
                    "device-1",
                    "site-1",
                    "area-gate",
                    "东门设备",
                    DeviceProtocol.ONVIF,
                    "http://10.0.0.8/onvif",
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    statuses: list[str] = []
    monkeypatch.setattr(
        window.statusBar(),
        "showMessage",
        lambda message, timeout=0: statuses.append(message),
    )

    window._sync_native_subscriptions()
    manager = window._native_subscription_manager
    assert manager is not None
    manager.last_subscription_error = "device-1: 设备未声明原生事件能力"
    window._sync_native_subscriptions()

    assert any("设备告警订阅异常" in text for text in statuses)
    assert any("设备未声明原生事件能力" in text for text in statuses)

    # The same failure is not reported twice in a row.
    statuses.clear()
    window._sync_native_subscriptions()
    assert statuses == []
    window.quit_application()
    app.processEvents()


def test_local_maintenance_trims_history_and_compacts_database(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-maintenance.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    calls: list[object] = []
    monkeypatch.setattr(
        window.store,
        "prune_history",
        lambda **kwargs: calls.append(kwargs) or {"events": 3, "audit": 2, "outbox": 1},
    )
    checkpoints: list[bool] = []
    monkeypatch.setattr(window.store, "checkpoint_wal", lambda: checkpoints.append(True))

    window._run_local_maintenance()

    assert calls == [{"retention_days": MerchantMainWindow.HISTORY_RETENTION_DAYS}]
    assert checkpoints == [True]
    window.quit_application()
    app.processEvents()


def test_local_maintenance_reports_failure_without_raising(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-maintenance-failure.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    def fail_prune(**_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(window.store, "prune_history", fail_prune)
    statuses: list[str] = []
    monkeypatch.setattr(
        window.statusBar(),
        "showMessage",
        lambda message, timeout=0: statuses.append(message),
    )

    window._run_local_maintenance()

    assert any("本地数据清理未完成" in text for text in statuses)
    window.quit_application()
    app.processEvents()


def test_incident_disposition_note_is_recorded_and_shown(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-note.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    incident = window._context.incidents[0]
    window._selected_incident_id = incident.id
    window.incident_note_edit.setText("已通知保安到场并登记来访人员")
    window._set_incident_status(IncidentStatus.ACKNOWLEDGED)
    app.processEvents()

    updated = window.store.get_incident(incident.id)
    assert updated is not None
    assert updated.status is IncidentStatus.ACKNOWLEDGED

    entries = window.store.list_incident_audit(incident.id, limit=10)
    notes = [entry["detail"] for entry in entries]
    assert any("已通知保安到场并登记来访人员" in detail for detail in notes)

    assert "已通知保安到场并登记来访人员" in window.incident_audit_label.text()
    assert window.incident_note_edit.text() == ""
    window.quit_application()
    app.processEvents()


def test_live_page_filters_cameras_by_site(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-live-site-filter.db"
    store = SQLiteStore(database)
    device_a = VideoDevice(
        "device-a",
        "site-a",
        "area-a",
        "一号店设备",
        DeviceProtocol.RTSP,
        "rtsp://a/1",
    )
    device_b = VideoDevice(
        "device-b",
        "site-b",
        "area-b",
        "二号店设备",
        DeviceProtocol.RTSP,
        "rtsp://b/1",
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "一号店"), Site("site-b", "二号店")),
            areas=(
                Area("area-a", "site-a", "东门"),
                Area("area-b", "site-b", "西门"),
            ),
            devices=(device_a, device_b),
            cameras=(
                CameraDevice("camera-a", "area-a", "一号店机", device_id=device_a.id),
                CameraDevice("camera-b", "area-b", "二号店机", device_id=device_b.id),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("live")
    app.processEvents()

    site_labels = [
        window.live_site_filter.itemText(index) for index in range(window.live_site_filter.count())
    ]
    assert site_labels == ["全部场所", "一号店", "二号店"]

    assert len(window._filtered_live_cameras()) == 2

    window.live_site_filter.setCurrentIndex(window.live_site_filter.findData("site-b"))
    app.processEvents()

    cameras = window._filtered_live_cameras()
    assert [camera.camera.name for camera in cameras] == ["二号店机"]
    area_labels = [
        window.live_area_filter.itemText(index)
        for index in range(1, window.live_area_filter.count())
    ]
    assert area_labels == ["二号店 / 西门"]

    window.live_site_filter.setCurrentIndex(window.live_site_filter.findData("site-a"))
    app.processEvents()
    cameras = window._filtered_live_cameras()
    assert [camera.camera.name for camera in cameras] == ["一号店机"]

    window.quit_application()
    app.processEvents()


def test_banner_mentions_every_site_when_multiple_exist(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-banner-sites.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "一号店"), Site("site-b", "二号店")),
            areas=(Area("area-a", "site-a", "东门"),),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    assert window._banner_site_name() == "一号店 等 2 个场所"

    window.quit_application()
    app.processEvents()


def test_banner_uses_single_site_name(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-banner-one-site.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "一号店"),),
            areas=(Area("area-a", "site-a", "东门"),),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    assert window._banner_site_name() == "一号店"

    window.quit_application()
    app.processEvents()


def test_quick_incident_camera_without_area_still_records_camera(app, tmp_path, monkeypatch):
    """Choosing a camera without a zone must still attribute the incident."""
    database = tmp_path / "merchant-incident-camera-only.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-a", "site-1", "东门"),),
            cameras=(CameraDevice("camera-a", "area-a", "东门机"),),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    dialog = QuickIncidentDialog(window._context, window)
    dialog.area_combo.setCurrentIndex(0)
    dialog.camera_combo.setCurrentIndex(dialog.camera_combo.findData("camera-a"))
    dialog.title_edit.setText("东门有人闯入")
    dialog._accept()

    assert dialog.incident is not None
    assert dialog.incident.camera_id == "camera-a"
    assert dialog.incident.area_id == "area-a"
    assert dialog.incident.site_id == "site-1"
    dialog.close()
    app.processEvents()
    window.quit_application()
    app.processEvents()


def test_quick_incident_area_change_keeps_camera_consistent(app, tmp_path, monkeypatch):
    """Switching the area must not leave a camera from another area selected."""
    database = tmp_path / "merchant-incident-camera-consistency.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(
                Area("area-a", "site-1", "东门"),
                Area("area-b", "site-1", "仓库"),
            ),
            cameras=(
                CameraDevice("camera-a", "area-a", "东门机"),
                CameraDevice("camera-b", "area-b", "仓库机"),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    dialog = QuickIncidentDialog(window._context, window)
    dialog.area_combo.setCurrentIndex(dialog.area_combo.findData("area-a"))
    dialog.camera_combo.setCurrentIndex(dialog.camera_combo.findData("camera-a"))
    assert dialog.camera_combo.currentData() == "camera-a"

    # The operator changes the area; the camera list is rebuilt.
    dialog.area_combo.setCurrentIndex(dialog.area_combo.findData("area-b"))
    app.processEvents()

    current_camera = str(dialog.camera_combo.currentData() or "")
    if current_camera:
        selected = next(
            item for item in window._context.cameras if item.camera.id == current_camera
        )
        assert selected.area.id == "area-b"

    dialog.title_edit.setText("仓库有人闯入")
    dialog._accept()

    assert dialog.incident is not None
    assert dialog.incident.area_id == "area-b"
    if dialog.incident.camera_id:
        camera = next(
            item for item in window._context.cameras if item.camera.id == dialog.incident.camera_id
        )
        assert camera.area.id == dialog.incident.area_id
    dialog.close()
    app.processEvents()
    window.quit_application()
    app.processEvents()


def test_quick_incident_dialog_labels_include_site(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-dialog-labels.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "device-a",
        "site-a",
        "area-a",
        "一号店东门设备",
        DeviceProtocol.RTSP,
        "rtsp://a/1",
    )
    device_b = VideoDevice(
        "device-b",
        "site-b",
        "area-b",
        "二号店东门设备",
        DeviceProtocol.RTSP,
        "rtsp://b/1",
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "一号店"), Site("site-b", "二号店")),
            areas=(
                Area("area-a", "site-a", "东门"),
                Area("area-b", "site-b", "东门"),
            ),
            devices=(device, device_b),
            cameras=(
                CameraDevice("camera-a", "area-a", "东门摄像机", device_id=device.id),
                CameraDevice("camera-b", "area-b", "东门摄像机", device_id=device_b.id),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    dialog = QuickIncidentDialog(window._context, window)
    area_labels = [
        dialog.area_combo.itemText(index) for index in range(1, dialog.area_combo.count())
    ]
    camera_labels = [
        dialog.camera_combo.itemText(index) for index in range(1, dialog.camera_combo.count())
    ]
    assert area_labels == ["一号店 / 东门", "二号店 / 东门"]
    assert camera_labels == [
        "一号店 / 东门 / 东门摄像机",
        "二号店 / 东门 / 东门摄像机",
    ]
    dialog.close()
    app.processEvents()
    window.quit_application()
    app.processEvents()


def test_background_slot_failure_does_not_stop_other_tasks(app, tmp_path, monkeypatch):
    """One broken background task must not abort the whole duty cycle."""
    database = tmp_path / "merchant-guarded-slot.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    calls: list[str] = []
    statuses: list[str] = []
    monkeypatch.setattr(
        window.statusBar(),
        "showMessage",
        lambda message, timeout=0: statuses.append(message),
    )

    def boom() -> None:
        calls.append("boom")
        raise RuntimeError("设备订阅同步失败")

    monkeypatch.setattr(window, "_drain_native_incidents", lambda: calls.append("alarms"))
    monkeypatch.setattr(window, "_sync_native_subscriptions", boom)
    monkeypatch.setattr(window, "_retry_failed_notifications", lambda: calls.append("retry"))
    monkeypatch.setattr(window, "_refresh_periodic_visible", lambda: calls.append("refresh"))

    window._periodic_refresh()

    assert calls == ["alarms", "boom", "retry", "refresh"]
    assert window._background_failures == 1
    assert any("设备订阅同步失败" in text for text in statuses)
    window.quit_application()
    app.processEvents()


def test_guarded_slot_reports_failure_without_raising(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-guarded-single.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    statuses: list[str] = []
    monkeypatch.setattr(
        window.statusBar(),
        "showMessage",
        lambda message, timeout=0: statuses.append(message),
    )

    def boom() -> None:
        raise ValueError("数据库被占用")

    window._guarded_slot("数据清理", boom)

    assert window._background_failures == 1
    assert any("数据清理" in text for text in statuses)
    assert any("数据库被占用" in text for text in statuses)
    window.quit_application()
    app.processEvents()


def test_settings_page_exposes_autostart_switch(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-autostart-settings.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("settings")
    app.processEvents()

    assert window.autostart_check.isEnabled()
    assert "开机" in window.autostart_check.text()
    window.quit_application()
    app.processEvents()


def test_autostart_toggle_reports_failure_without_leaving_state(app, tmp_path, monkeypatch):
    from factoryguard.app import autostart

    database = tmp_path / "merchant-autostart-toggle.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    def fail_enable(**_kwargs):
        raise RuntimeError("无法创建启动快捷方式")

    monkeypatch.setattr(autostart, "enable", fail_enable)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )

    window._toggle_autostart(True)

    assert warnings and warnings[0][0] == "开机自启动设置失败"
    assert "无法创建启动快捷方式" in warnings[0][1]
    assert not window.autostart_check.isChecked()
    window.quit_application()
    app.processEvents()


def test_stream_failure_hint_gives_protocol_specific_advice():
    from factoryguard.ui.merchant_console import _stream_failure_hint

    gb_camera = CameraView(
        camera=CameraDevice("camera-gb", "area-1", "东门"),
        site=Site("site-1", "星河园区"),
        area=Area("area-1", "site-1", "东门"),
        device=VideoDevice(
            "device-gb",
            "site-1",
            "area-1",
            "国标设备",
            DeviceProtocol.GB28181,
            "gb28181://34020000001320000001",
        ),
        streams=(),
    )
    onvif_camera = CameraView(
        camera=CameraDevice("camera-onvif", "area-1", "东门"),
        site=Site("site-1", "星河园区"),
        area=Area("area-1", "site-1", "东门"),
        device=VideoDevice(
            "device-onvif",
            "site-1",
            "area-1",
            "ONVIF 设备",
            DeviceProtocol.ONVIF,
            "http://10.0.0.8/onvif",
        ),
        streams=(),
    )

    gb_hint = _stream_failure_hint(gb_camera, "连接超时")
    assert "连接超时" in gb_hint
    assert "国标服务" in gb_hint

    onvif_hint = _stream_failure_hint(onvif_camera, "")
    assert "设备没有返回可用地址" in onvif_hint
    assert "ONVIF" in onvif_hint


def test_config_recovery_hint_lists_backups_and_paths(tmp_path):
    from factoryguard.config import ConfigFileError
    from factoryguard.ui.merchant_console import _config_recovery_hint

    database = tmp_path / "factoryguard.db"
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    archive = backup_root / "factoryguard-20261106.zip"
    archive.write_bytes(b"PK")

    config = MerchantConfig(db=str(database), config_path=str(tmp_path / "factoryguard.json"))
    hint = _config_recovery_hint(config, ConfigFileError("配置文件 JSON 格式错误"))

    assert str(tmp_path / "factoryguard.json") in hint
    assert archive.name in hint
    assert "JSON" in hint
    assert "业务数据不会因此丢失" not in hint


def test_config_recovery_hint_without_backups_explains_manual_fix(tmp_path):
    from factoryguard.config import ConfigFileError
    from factoryguard.ui.merchant_console import _config_recovery_hint

    config = MerchantConfig(
        db=str(tmp_path / "factoryguard.db"), config_path=str(tmp_path / "f.json")
    )
    hint = _config_recovery_hint(config, ConfigFileError("无法读取配置文件"))

    assert "没有找到备份包" in hint
    assert "业务数据不会因此丢失" in hint
    assert str(tmp_path / "f.json") in hint


def test_incident_search_reaches_history_beyond_recent_snapshot(app, tmp_path, monkeypatch):
    """Old incidents must stay findable even when the dashboard only keeps 100."""
    database = tmp_path / "merchant-incident-deep-search.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
        )
    )
    old_incident = Incident.create(
        title="很久以前的特殊事件标记",
        description="历史记录",
        severity=IncidentSeverity.LOW,
        area_id="area-gate",
        reporter="当前用户",
    )
    store.create_incident(old_incident, actor="tester")
    store.update_incident_status(old_incident.id, IncidentStatus.RESOLVED, actor="tester")
    for index in range(150):
        incident = Incident.create(
            title=f"近期事件 {index}",
            description="用于把历史记录挤出快照",
            severity=IncidentSeverity.LOW,
        )
        store.create_incident(incident, actor="tester")
        store.update_incident_status(incident.id, IncidentStatus.RESOLVED, actor="tester")
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    # The recent snapshot no longer holds the old incident.
    assert old_incident.id not in {item.id for item in window._context.incidents}

    window.incident_search.setText("特殊事件标记")
    app.processEvents()

    assert old_incident.id in window._incident_records
    assert len(window._incident_records) == 1
    window.quit_application()
    app.processEvents()


def test_incident_list_shows_recorded_location_after_area_rename(app, tmp_path, monkeypatch):
    """The list and the export must name the same place for one incident."""
    database = tmp_path / "merchant-incident-location-consistency.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
        )
    )
    incident = Incident.create(
        title="东门有人闯入",
        area_id="area-gate",
        area_name="东门旧名",
        camera_name="东门旧摄像机",
    )
    store.create_incident(incident, actor="tester")
    # The operator renames the area afterwards.
    store.save_area_bundle(
        Site("site-1", "星河园区"), Area("area-gate", "site-1", "正门"), actor="tester"
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    row = window._incident_row(window._context.incidents[0])
    assert "东门旧名" in row.detail.text()
    assert "正门" not in row.detail.text()

    window.quit_application()
    app.processEvents()


def test_incident_search_matches_snapshot_names(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-snapshot-search.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"),),
        )
    )
    incident = Incident.create(
        title="普通标题",
        description="普通描述",
        area_id="area-gate",
        area_name="东门旧名",
        camera_name="旧摄像头",
    )
    store.create_incident(incident, actor="tester")
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    window.incident_search.setText("东门旧名")
    app.processEvents()
    assert incident.id in window._incident_records

    window.incident_search.setText("旧摄像头")
    app.processEvents()
    assert incident.id in window._incident_records
    window.quit_application()
    app.processEvents()


def test_incident_export_writes_current_filtered_list(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-export.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    target = tmp_path / "导出.csv"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), ""),
    )

    window._export_incidents()

    assert target.is_file()
    text = target.read_text(encoding="utf-8-sig")
    assert "险情编号" in text
    assert "仓库北门人员闯入" in text
    window.quit_application()
    app.processEvents()


def test_incident_export_without_results_shows_message(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-export-empty.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    window.incident_search.setText("不存在的关键字")
    app.processEvents()

    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: messages.append((title, text)),
    )

    window._export_incidents()

    assert messages and messages[0][0] == "没有可导出的险情"
    window.quit_application()
    app.processEvents()


def test_site_can_be_created_renamed_and_deleted(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-site-management.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    class FakeSiteDialog:
        def __init__(self, _topology, _parent, *, site=None):
            self.site = (
                Site("site-new", "二号店") if site is None else replace(site, name="一号店东门")
            )

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr("factoryguard.ui.merchant_console.SiteDialog", FakeSiteDialog)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )

    window._add_site()
    assert window.store.load_topology().sites[-1].name == "二号店"

    existing = window._context.topology.sites[0]
    window._rename_site(existing)
    assert any(site.name == "一号店东门" for site in window.store.load_topology().sites)

    empty_site = Site("site-empty", "空门店")
    window.store.save_site(empty_site, actor="tester")
    window._refresh_all()
    window._delete_site(empty_site)
    assert all(site.id != "site-empty" for site in window.store.load_topology().sites)

    window.quit_application()
    app.processEvents()


def test_site_with_areas_cannot_be_deleted(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-site-delete-blocked.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )

    site = window._context.topology.sites[0]
    window._delete_site(site)

    assert warnings and "该场所下仍有区域" in warnings[0]
    assert any(item.id == site.id for item in window.store.load_topology().sites)
    window.quit_application()
    app.processEvents()


def test_area_filters_show_site_and_area(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-area-filter-labels.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "一号店"), Site("site-b", "二号店")),
            areas=(
                Area("area-a", "site-a", "东门"),
                Area("area-b", "site-b", "东门"),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )

    window._switch_page("devices")
    app.processEvents()

    for combo in (
        window.live_area_filter,
        window.incident_area_filter,
        window.devices_area_filter,
    ):
        labels = [combo.itemText(index) for index in range(1, combo.count())]
        assert labels == ["一号店 / 东门", "二号店 / 东门"]
        assert len(set(labels)) == len(labels)
    window.quit_application()
    app.processEvents()


def test_incident_without_area_can_be_filed_from_the_detail_page(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-incident-location.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东门"), Area("area-warehouse", "site-1", "仓库")),
        )
    )
    incident = Incident.create(title="门口有人闯入", description="现场反馈")
    assert store.create_incident(incident, actor="tester")
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("incidents")
    app.processEvents()

    assert window.incident_area_edit.isEnabled()
    assert window.incident_area_button.isEnabled()

    index = window.incident_area_edit.findData("area-warehouse")
    assert index > 0
    window.incident_area_edit.setCurrentIndex(index)
    window._assign_incident_area()
    app.processEvents()

    stored = window.store.get_incident(incident.id)
    assert stored is not None
    assert stored.area_id == "area-warehouse"
    assert stored.area_name_snapshot == "仓库"

    # Once the incident has a place, the picker is read-only.
    assert not window.incident_area_edit.isEnabled()
    window.quit_application()
    app.processEvents()


def test_devices_count_label_shows_total_when_filtered(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-device-count.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "门店"),),
            areas=(
                Area("area-a", "site-1", "东门"),
                Area("area-b", "site-1", "仓库"),
            ),
            cameras=(
                CameraDevice("camera-a", "area-a", "大门"),
                CameraDevice("camera-b", "area-b", "货架"),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._switch_page("devices")
    app.processEvents()

    assert window.devices_count_label.text() == "共 2 台"

    window.devices_search.setText("仓库")
    window._refresh_devices()
    app.processEvents()

    assert window.devices_count_label.text() == "1 / 2 台"
    window.quit_application()
    app.processEvents()


def test_camera_edit_credentials_keep_omitted_fields(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-edit-credentials.db"
    config_path = tmp_path / "factoryguard.json"
    store = SQLiteStore(database)
    device = VideoDevice(
        "device-cred",
        "site-1",
        "area-1",
        "仓库摄像头",
        DeviceProtocol.RTSP,
        "rtsp://camera/1",
        credentials_ref="cred-device",
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=(device,),
            cameras=(CameraDevice("camera-1", "area-1", "仓库摄像头", device_id=device.id),),
        )
    )
    store.close()
    vault = PlaintextCredentialVault()
    config_store = AppConfigStore(config_path)
    config_store.save_device_credential(
        "cred-device",
        vault.encode(DeviceCredentials(username="old-user", password="old-password")),
    )
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path))
    )
    view = window._context.cameras[0]

    username_only = window._merge_edited_credentials(
        view.device, DeviceCredentials(username="new-user")
    )
    assert username_only.username == "new-user"
    assert username_only.password == "old-password"

    password_only = window._merge_edited_credentials(
        view.device, DeviceCredentials(password="new-password")
    )
    assert password_only.username == "old-user"
    assert password_only.password == "new-password"
    assert (
        window._playable_stream_url(view, "rtsp://camera/1?token=a@b")
        == "rtsp://old-user:old-password@camera/1?token=a@b"
    )
    window.quit_application()
    app.processEvents()


def test_camera_edit_rejects_duplicate_connection(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-edit-duplicate.db"
    store = SQLiteStore(database)
    devices = (
        VideoDevice(
            "device-1",
            "site-1",
            "area-1",
            "东门摄像头",
            DeviceProtocol.RTSP,
            "rtsp://camera/1",
        ),
        VideoDevice(
            "device-2",
            "site-1",
            "area-1",
            "仓库摄像头",
            DeviceProtocol.RTSP,
            "rtsp://camera/2",
        ),
    )
    cameras = (
        CameraDevice(
            "camera-1", "area-1", "东门摄像头", address="rtsp://camera/1", device_id="device-1"
        ),
        CameraDevice(
            "camera-2", "area-1", "仓库摄像头", address="rtsp://camera/2", device_id="device-2"
        ),
    )
    streams = (
        VideoStream("stream-1", "camera-1", "子码流", "rtsp://camera/1", device_id="device-1"),
        VideoStream("stream-2", "camera-2", "子码流", "rtsp://camera/2", device_id="device-2"),
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=devices,
            cameras=cameras,
            streams=streams,
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    source = next(camera for camera in window._context.cameras if camera.camera.id == "camera-1")
    target = next(camera for camera in window._context.cameras if camera.camera.id == "camera-2")

    class FakeEditDialog:
        def __init__(self, _topology, _view, **_kwargs):
            self.device = replace(source.device, endpoint=target.device.endpoint)
            self.camera = replace(source.camera, address=target.camera.address)
            self.stream = replace(source.best_stream, url=target.best_stream.url)
            self.credentials = None

        def exec(self):
            return QDialog.Accepted

    messages: list[str] = []
    monkeypatch.setattr("factoryguard.ui.merchant_console.CameraEditDialog", FakeEditDialog)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(text),
    )

    window._edit_camera(source)

    assert messages and "已接入为" in messages[-1]
    topology = window.store.load_topology()
    edited = topology.get_camera("camera-1")
    assert edited.address == "rtsp://camera/1"
    window.quit_application()
    app.processEvents()


def test_onvif_native_event_maps_internal_device_to_camera(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-onvif-event-mapping.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "video-onvif",
        "site-1",
        "area-1",
        "东门摄像头",
        DeviceProtocol.ONVIF,
        "http://camera/onvif",
        capabilities=DeviceCapabilities(native_events=True),
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(device,),
            cameras=(
                CameraDevice(
                    "camera-1",
                    "area-1",
                    "东门摄像头",
                    device_id=device.id,
                    channel_id="ch1",
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    window._handle_gb_native_event(
        NativeEvent(
            device_id="video-onvif",
            channel_id="profile-token",
            event_code="motion",
            occurred_at=datetime.now(UTC),
            source=EventSource.ONVIF,
            title="检测到移动",
            level="high",
            source_event_id="onvif-internal-device-event",
        )
    )
    window._drain_native_incidents()

    incident = next(item for item in window.store.list_incidents() if item.title == "检测到移动")
    assert incident.site_id == "site-1"
    assert incident.area_id == "area-1"
    assert incident.camera_id == "camera-1"
    assert incident.reporter == "ONVIF · 东门摄像头"
    assert "motion" not in incident.description
    assert "请结合现场视频核实" in incident.description
    assert incident.metadata["event_code"] == "motion"
    window.quit_application()
    app.processEvents()


def test_onvif_cached_stream_refreshes_after_endpoint_host_changes(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-onvif-stream-refresh.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "video-onvif",
        "site-1",
        "area-1",
        "东门摄像头",
        DeviceProtocol.ONVIF,
        "http://new-camera/onvif",
        capabilities=DeviceCapabilities(live_video=True),
    )
    camera = CameraDevice(
        "camera-1",
        "area-1",
        "东门摄像头",
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "设备实时码流",
        "rtsp://old-camera/live",
        role=StreamRole.SUB,
        device_id=device.id,
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(device,),
            cameras=(camera,),
            streams=(stream,),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    view = window._context.cameras[0]
    assert not window._cached_stream_matches_device(view, stream.url)
    same_host_view = replace(
        view,
        device=replace(view.device, endpoint="http://old-camera/onvif"),
        health_status="unknown",
    )
    assert not window._should_use_cached_stream(same_host_view, stream)
    assert window._should_use_cached_stream(replace(same_host_view, health_status="online"), stream)

    opened: list[str] = []

    class FakePreview:
        def __init__(self, *, title, url, parent=None):
            opened.append(url)

        def exec(self):
            return 0

    monkeypatch.setattr("factoryguard.ui.merchant_console.LivePreviewDialog", FakePreview)
    window._on_stream_resolved(
        view,
        ResolvedStream(uri="rtsp://new-camera/live"),
    )

    refreshed_stream = window.store.load_topology().get_stream("stream-1")
    assert refreshed_stream.url == "rtsp://new-camera/live"
    assert window.store.list_stream_health()["stream-1"]["status"] == "online"
    assert opened == ["rtsp://new-camera/live"]
    window.quit_application()
    app.processEvents()


def test_cached_stream_is_refreshed_when_device_port_changes(app, tmp_path, monkeypatch):
    """Moving the device to another port must not reuse the old stream."""
    database = tmp_path / "merchant-onvif-port-change.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "video-onvif",
        "site-1",
        "area-1",
        "东门摄像头",
        DeviceProtocol.ONVIF,
        "http://camera-1:9000/onvif",
        capabilities=DeviceCapabilities(live_video=True),
    )
    camera = CameraDevice(
        "camera-1",
        "area-1",
        "东门摄像头",
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "设备实时码流",
        "rtsp://camera-1:554/live",
        role=StreamRole.SUB,
        device_id=device.id,
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(device,),
            cameras=(camera,),
            streams=(stream,),
        )
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    view = window._context.cameras[0]

    # The operator moved the device to another port on the same host.
    moved = replace(
        view,
        device=replace(view.device, endpoint="http://camera-1:9001/onvif"),
        health_status="online",
    )
    assert not window._cached_stream_matches_device(moved, stream.url)
    assert not window._should_use_cached_stream(moved, stream)

    window.quit_application()
    app.processEvents()


def test_camera_endpoint_edit_invalidates_cached_stream_health(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-endpoint-health-invalidation.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "video-onvif",
        "site-1",
        "area-1",
        "东门摄像头",
        DeviceProtocol.ONVIF,
        "http://old-camera/onvif",
        capabilities=DeviceCapabilities(live_video=True),
    )
    camera = CameraDevice(
        "camera-1",
        "area-1",
        "东门摄像头",
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "设备实时码流",
        "rtsp://old-camera/live",
        role=StreamRole.SUB,
        device_id=device.id,
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(device,),
            cameras=(camera,),
            streams=(stream,),
        )
    )
    store.set_stream_health(
        "stream-1", "online", "1280×720", updated_at=datetime.now(UTC), actor="test"
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    view = window._context.cameras[0]

    class FakeEditDialog:
        def __init__(self, _topology, camera_view, **_kwargs):
            self.device = replace(
                camera_view.device,
                endpoint="http://new-camera/onvif",
                health_status="unknown",
                health_detail="设备地址已更新，等待重新检测",
                last_probe_at=None,
                last_ok_at=None,
            )
            self.camera = camera_view.camera
            self.stream = None
            self.credentials = None

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr("factoryguard.ui.merchant_console.CameraEditDialog", FakeEditDialog)
    window._edit_camera(view)

    snapshot = window.store.list_stream_health()["stream-1"]
    assert snapshot["status"] == "unknown"
    assert "连接参数已更新" in snapshot["detail"]
    assert window._context.cameras[0].status_label == "待检测"
    window.quit_application()
    app.processEvents()


def test_camera_credential_edit_invalidates_cached_stream_health(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-credential-health-invalidation.db"
    store = SQLiteStore(database)
    device = VideoDevice(
        "video-rtsp",
        "site-1",
        "area-1",
        "东门摄像头",
        DeviceProtocol.RTSP,
        "rtsp://camera/live",
    )
    camera = CameraDevice(
        "camera-1",
        "area-1",
        "东门摄像头",
        device_id=device.id,
        channel_id="ch1",
    )
    stream = VideoStream(
        "stream-1",
        camera.id,
        "子码流",
        "rtsp://camera/live",
        role=StreamRole.SUB,
        device_id=device.id,
    )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "东门"),),
            devices=(device,),
            cameras=(camera,),
            streams=(stream,),
        )
    )
    store.set_stream_health(
        "stream-1", "online", "1280×720", updated_at=datetime.now(UTC), actor="test"
    )
    store.close()
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    monkeypatch.setattr(MerchantMainWindow, "_sync_native_subscriptions", lambda self: None)
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    view = window._context.cameras[0]

    class FakeEditDialog:
        def __init__(self, _topology, camera_view, **_kwargs):
            self.device = camera_view.device
            self.camera = camera_view.camera
            self.stream = None
            self.credentials = DeviceCredentials(username="new-user", password="new-password")

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr("factoryguard.ui.merchant_console.CameraEditDialog", FakeEditDialog)
    window._edit_camera(view)

    snapshot = window.store.list_stream_health()["stream-1"]
    assert snapshot["status"] == "unknown"
    assert "连接参数已更新" in snapshot["detail"]
    assert window._context.cameras[0].status_label == "待检测"
    window.quit_application()
    app.processEvents()


def test_onvif_scan_lets_user_choose_discovered_device(app, tmp_path, monkeypatch):
    database = tmp_path / "merchant-onvif-picker.db"
    _seed(database)
    monkeypatch.setattr(MerchantMainWindow, "_load_llm_config", lambda self: LLMConfig())
    window = MerchantMainWindow(
        config=MerchantConfig(
            db=str(database),
            config_path=str(tmp_path / "factoryguard.json"),
        )
    )
    devices = (
        SimpleNamespace(
            endpoint="http://camera-1/onvif",
            scopes=("onvif://www.onvif.org/name/Front_Door",),
        ),
        SimpleNamespace(
            endpoint="http://camera-2/onvif",
            scopes=("onvif://www.onvif.org/name/Warehouse",),
        ),
    )
    captured: dict[str, object] = {}

    def choose_device(_parent, _title, _label, labels, _current, _editable):
        captured["labels"] = tuple(labels)
        return labels[1], True

    monkeypatch.setattr(QInputDialog, "getItem", choose_device)
    monkeypatch.setattr(window, "_add_camera", lambda: captured.update(called=True))

    window._on_onvif_discovered(devices)

    assert "Front Door" in captured["labels"][0]
    assert window._last_discovered_onvif[0] == "http://camera-2/onvif"
    assert captured["called"] is True
    window.quit_application()
    app.processEvents()


def test_startup_failure_hint_explains_database_corruption(tmp_path):
    from factoryguard.ui.merchant_console import _startup_failure_hint

    database = tmp_path / "factoryguard.db"
    config = MerchantConfig(db=str(database), config_path=str(tmp_path / "f.json"))
    hint = _startup_failure_hint(
        config, sqlite3.DatabaseError("file is not a database"), "file is not a database"
    )

    assert "已损坏" in hint
    assert str(database.resolve()) in hint
    assert "恢复备份" in hint
    assert "改名保留" in hint


def test_startup_failure_hint_explains_locked_database(tmp_path):
    from factoryguard.ui.merchant_console import _startup_failure_hint

    config = MerchantConfig(db=str(tmp_path / "f.db"), config_path=str(tmp_path / "f.json"))
    hint = _startup_failure_hint(
        config, sqlite3.OperationalError("database is locked"), "database is locked"
    )

    assert "被其他程序占用" in hint
    assert "重新启动" in hint


def test_startup_failure_hint_falls_back_to_generic_guidance(tmp_path):
    from factoryguard.ui.merchant_console import _startup_failure_hint

    config = MerchantConfig(db=str(tmp_path / "f.db"), config_path=str(tmp_path / "f.json"))
    hint = _startup_failure_hint(config, ValueError("未知问题"), "未知问题")

    assert "无法读取本地配置或业务数据" in hint
    assert "未知问题" in hint
