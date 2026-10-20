import json
import os
from datetime import UTC, datetime

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import numpy as np

from factoryguard.domain.models import Area, CameraDevice, Detection, Event, EventStatus, Frame, Rule, RuleType
from factoryguard.domain.models import Site, StreamRole, VideoStream
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.ui.main_window import MainWindow
from factoryguard.ui.preview import save_frame_image
from factoryguard.ui.rule_dialog import parse_geometry
from factoryguard.ui.worker import MonitorConfig


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def test_qt_control_panel_builds_without_starting_worker(app, tmp_path):
    window = MainWindow(config=MonitorConfig(db=str(tmp_path / "events.db")))
    assert window.windowTitle().startswith("FactoryGuard")
    assert window.url_edit.text() == "rtsp://localhost/live"
    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert window._thread is None
    window.webhook_url_edit.setText("https://example.test/hook")
    window.webhook_secret_edit.setText("secret")
    window.notification_interval_spin.setValue(12)
    config = window._config_from_form()
    assert config.webhook_url == "https://example.test/hook"
    assert config.webhook_secret == "secret"
    assert config.notification_interval == 12
    window.close()
    app.processEvents()


def test_split_settings_pages_persist_email_without_sensitive_credentials(app, tmp_path):
    database = tmp_path / "settings.db"
    window = MainWindow(
        config=MonitorConfig(
            db=str(database),
            webhook_secret="startup-webhook-secret",
            email_password="startup-smtp-password",
        )
    )
    assert window.page_stack.count() == 13
    assert window._page_routes["settings-inference"] == 7
    assert window._page_routes["settings-email"] == 9
    assert window._page_routes["settings-sms"] == 10
    assert window._page_routes["settings-storage"] == 11
    assert window._page_routes["settings-health"] == 12

    window.webhook_enabled_check.setChecked(False)
    window.email_enabled_check.setChecked(True)
    window.email_host_edit.setText("smtp.example.test")
    window.email_port_spin.setValue(465)
    window.email_username_edit.setText("factoryguard@example.test")
    window.email_sender_edit.setText("factoryguard@example.test")
    window.email_recipients_edit.setText("operator@example.test; security@example.test")
    window.email_security_combo.setCurrentIndex(window.email_security_combo.findData("ssl"))
    window.notification_interval_spin.setValue(15)
    window.notification_batch_spin.setValue(25)
    window._save_notification_settings()

    saved_path = tmp_path / "factoryguard-ui-settings.json"
    persisted = json.loads(saved_path.read_text(encoding="utf-8"))
    notification = persisted["notification"]
    assert notification["channels"] == ["email"]
    assert notification["interval"] == 15
    assert notification["batch"] == 25
    assert notification["email"]["host"] == "smtp.example.test"
    assert notification["email"]["port"] == 465
    assert notification["email"]["recipients"] == [
        "operator@example.test",
        "security@example.test",
    ]
    persisted_text = saved_path.read_text(encoding="utf-8")
    assert "startup-webhook-secret" not in persisted_text
    assert "startup-smtp-password" not in persisted_text
    assert "password" not in persisted_text.lower()
    assert "secret" not in persisted_text.lower()

    config = window._config_from_form()
    assert config.notification_channels == ("email",)
    assert config.email_host == "smtp.example.test"
    assert config.email_port == 465
    assert config.email_password == "startup-smtp-password"
    assert config.email_recipients == ("operator@example.test", "security@example.test")
    assert config.email_use_ssl is True
    assert config.email_starttls is False
    window.close()
    app.processEvents()


def test_provider_presets_and_sms_settings_are_available(app, tmp_path):
    database = tmp_path / "provider-settings.db"
    window = MainWindow(config=MonitorConfig(db=str(database), sms_token="startup-sms-token"))

    webhook_providers = [window.webhook_provider_combo.itemData(i) for i in range(window.webhook_provider_combo.count())]
    assert webhook_providers == ["wecom", "wechat", "dingtalk", "feishu", "custom"]
    email_providers = [window.email_provider_combo.itemData(i) for i in range(window.email_provider_combo.count())]
    assert email_providers == ["163", "139", "qq", "outlook", "google", "custom"]

    window.webhook_provider_combo.setCurrentIndex(window.webhook_provider_combo.findData("feishu"))
    assert "飞书" in window.webhook_provider_hint.text()
    assert "feishu.cn" in window.webhook_url_edit.placeholderText()

    window.email_provider_combo.setCurrentIndex(window.email_provider_combo.findData("qq"))
    assert window.email_host_edit.text() == "smtp.qq.com"
    assert window.email_port_spin.value() == 465
    assert window.email_security_combo.currentData() == "ssl"

    window.webhook_enabled_check.setChecked(False)
    window.email_enabled_check.setChecked(False)
    window.sms_enabled_check.setChecked(True)
    window.sms_provider_combo.setCurrentIndex(window.sms_provider_combo.findData("custom_http"))
    window.sms_endpoint_edit.setText("https://sms.example.test/send")
    window.sms_sender_edit.setText("FactoryGuard")
    window.sms_template_edit.setText("event-alert")
    window.sms_recipients_edit.setText("13800138000; +86 139-0013-9000")
    window.sms_token_edit.setText("sms-runtime-token")
    window._save_notification_settings()

    persisted = json.loads((tmp_path / "factoryguard-ui-settings.json").read_text(encoding="utf-8"))
    sms = persisted["notification"]["sms"]
    assert sms["provider"] == "custom_http"
    assert sms["endpoint"] == "https://sms.example.test/send"
    assert sms["sender"] == "FactoryGuard"
    assert sms["template_id"] == "event-alert"
    assert sms["recipients"] == ["13800138000", "+8613900139000"]
    assert "sms-runtime-token" not in json.dumps(persisted, ensure_ascii=False)

    config = window._config_from_form()
    assert config.notification_channels == ("sms",)
    assert config.sms_provider == "custom_http"
    assert config.sms_endpoint == "https://sms.example.test/send"
    assert config.sms_token == "sms-runtime-token"
    assert config.sms_recipients == ("13800138000", "+8613900139000")
    window.close()
    app.processEvents()


def test_multiple_destinations_of_same_kind_are_independent(app, tmp_path):
    database = tmp_path / "multi-destinations.db"
    window = MainWindow(config=MonitorConfig(db=str(database)))

    window.webhook_name_edit.setText("主 Webhook")
    window.webhook_enabled_check.setChecked(True)
    window.webhook_url_edit.setText("https://webhook-one.example.test/event")
    window.webhook_secret_edit.setText("webhook-one-secret")
    window._add_destination("webhook")
    window.webhook_name_edit.setText("备份 Webhook")
    window.webhook_enabled_check.setChecked(True)
    window.webhook_url_edit.setText("https://webhook-two.example.test/event")
    window.webhook_secret_edit.setText("webhook-two-secret")

    window.email_name_edit.setText("主邮箱")
    window.email_enabled_check.setChecked(True)
    window.email_host_edit.setText("smtp-one.example.test")
    window.email_sender_edit.setText("one@example.test")
    window.email_recipients_edit.setText("ops@example.test")
    window.email_password_edit.setText("email-one-password")
    window._add_destination("email")
    window.email_name_edit.setText("备份邮箱")
    window.email_enabled_check.setChecked(True)
    window.email_host_edit.setText("smtp-two.example.test")
    window.email_sender_edit.setText("two@example.test")
    window.email_recipients_edit.setText("security@example.test")
    window.email_password_edit.setText("email-two-password")

    window.sms_name_edit.setText("主短信")
    window.sms_enabled_check.setChecked(True)
    window.sms_endpoint_edit.setText("https://sms-one.example.test/send")
    window.sms_recipients_edit.setText("13800138000")
    window.sms_token_edit.setText("sms-one-token")
    window._add_destination("sms")
    window.sms_name_edit.setText("备份短信")
    window.sms_enabled_check.setChecked(True)
    window.sms_endpoint_edit.setText("https://sms-two.example.test/send")
    window.sms_recipients_edit.setText("13900139000")
    window.sms_token_edit.setText("sms-two-token")

    window._save_notification_settings()
    persisted = json.loads((tmp_path / "factoryguard-ui-settings.json").read_text(encoding="utf-8"))
    destinations = persisted["notification"]["destinations"]
    assert len(destinations) == 6
    assert sum(item["kind"] == "webhook" and item["enabled"] for item in destinations) == 2
    assert sum(item["kind"] == "email" and item["enabled"] for item in destinations) == 2
    assert sum(item["kind"] == "sms" and item["enabled"] for item in destinations) == 2
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    for secret in (
        "webhook-one-secret",
        "webhook-two-secret",
        "email-one-password",
        "email-two-password",
        "sms-one-token",
        "sms-two-token",
    ):
        assert secret not in persisted_text

    config = window._config_from_form()
    assert len(config.notification_channels) == 6
    assert len(set(config.notification_channels)) == 6
    assert sum(channel.startswith("webhook") for channel in config.notification_channels) == 2
    assert sum(channel.startswith("email") for channel in config.notification_channels) == 2
    assert sum(channel.startswith("sms") for channel in config.notification_channels) == 2

    from factoryguard.ui.notification_worker import NotificationWorker

    worker = NotificationWorker(
        db=str(database),
        destinations=config.delivery_destinations,
        interval=30,
    )
    assert set(worker.adapters) == set(config.notification_channels)
    window.close()
    app.processEvents()

    reloaded = MainWindow(config=MonitorConfig(db=str(database)))
    assert len(reloaded._destinations) == 6
    assert reloaded.webhook_destination_table.rowCount() == 2
    assert reloaded.email_destination_table.rowCount() == 2
    assert reloaded.sms_destination_table.rowCount() == 2
    reloaded.close()
    app.processEvents()


def test_pages_adapt_to_small_windows_and_scroll_vertically(app, tmp_path):
    window = MainWindow(config=MonitorConfig(db=str(tmp_path / "responsive.db")))
    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        assert window.width() <= available.width()
        assert window.height() <= available.height()
    window.resize(800, 600)
    window.show()
    app.processEvents()

    assert window.page_scroll.widget() is window.page_stack
    assert window.page_scroll.verticalScrollBar().isVisible()
    assert not window.page_scroll.horizontalScrollBar().isVisible()
    window.page_scroll.verticalScrollBar().setValue(100)
    window._switch_page(5)
    app.processEvents()
    assert window.page_scroll.verticalScrollBar().value() == 0

    window.resize(1440, 900)
    app.processEvents()
    assert window.page_stack.width() <= window.page_scroll.viewport().width()
    window.close()
    app.processEvents()


def test_event_search_and_snapshot_replay(app, tmp_path):
    database = tmp_path / "events.db"
    store = SQLiteStore(database)
    rule = Rule(
        "r1",
        "CH01",
        "fence",
        RuleType.POLYGON,
        ((0, 0), (100, 0), (100, 100), (0, 100)),
        site_id="site-1",
        area_id="area-1",
        camera_id="camera-1",
    )
    occurred = datetime.now(UTC)
    event = Event.create(
        rule=rule,
        frame=Frame("CH01", 1, occurred, 100, 100, site_id="site-1", area_id="area-1", camera_id="camera-1"),
        detection=Detection("person", 0.9, (20, 10, 40, 80), "track-1"),
    )
    assert store.create_event_with_outbox(event)
    snapshot = tmp_path / "snap.png"
    save_frame_image(np.zeros((100, 100, 3), dtype=np.uint8), snapshot)
    assert store.update_event_media(event.id, snapshot_uri=str(snapshot))
    store.close()

    window = MainWindow(config=MonitorConfig(db=str(database)))
    assert window.events_table.rowCount() == 1
    assert window.overview_event_table.rowCount() == 1
    assert window.overview_event_table.item(0, 1).text() == "1"
    window.site_filter.setCurrentIndex(window.site_filter.findData("site-1"))
    window.area_filter.setCurrentIndex(window.area_filter.findData("area-1"))
    window.camera_filter.setCurrentIndex(window.camera_filter.findData("camera-1"))
    assert window.events_table.rowCount() == 1
    window.site_filter.setCurrentIndex(0)
    window.area_filter.setCurrentIndex(0)
    window.camera_filter.setCurrentIndex(0)
    window.search_edit.setText("person")
    assert window.events_table.rowCount() == 1
    window.search_edit.setText("fork")
    assert window.events_table.rowCount() == 0
    window.search_edit.clear()
    window.time_filter.setCurrentIndex(1)
    assert window.events_table.rowCount() == 1
    window.events_table.selectRow(0)
    app.processEvents()
    assert window.replay_button.isEnabled()
    assert window.confirm_event_button.isEnabled()
    assert event.id in window.event_inspector.text()
    assert "规则：r1" in window.event_inspector.text()
    window._set_event_status(EventStatus.CONFIRMED)
    assert window.events_table.item(0, 4).text() == "已确认"
    window.status_filter.setCurrentText("已确认")
    assert window.events_table.rowCount() == 1
    window.events_table.selectRow(0)
    app.processEvents()
    window._show_selected_replay()
    assert window.preview.has_image
    window._export_events_csv()
    window._export_audit_json()
    assert list((tmp_path / "exports").glob("events-*.csv"))
    assert list((tmp_path / "exports").glob("audit-*.json"))
    window.close()
    app.processEvents()


def test_events_can_be_processed_in_bulk(app, tmp_path):
    database = tmp_path / "bulk-events.db"
    store = SQLiteStore(database)
    rule = Rule("bulk-rule", "stream-1", "fence", RuleType.POLYGON, ((0, 0), (100, 0), (100, 100)))
    for frame_no in (1, 2):
        event = Event.create(
            rule=rule,
            frame=Frame("stream-1", frame_no, datetime.now(UTC), 100, 100),
            detection=Detection("person", 0.9, (10 + frame_no, 10, 30, 60), f"track-{frame_no}"),
        )
        assert store.create_event_with_outbox(event)
    store.close()

    window = MainWindow(config=MonitorConfig(db=str(database)))
    window.events_table.selectAll()
    app.processEvents()
    assert len(window._selected_event_records()) == 2
    assert window.confirm_event_button.isEnabled()
    window._set_selected_event_status(EventStatus.CONFIRMED, confirm=False)

    restored = SQLiteStore(database)
    assert {str(row["status"]) for row in restored.list_events()} == {"confirmed"}
    restored.close()
    window.close()
    app.processEvents()


def test_theme_selector_updates_application_style(app, tmp_path):
    window = MainWindow(config=MonitorConfig(db=str(tmp_path / "themes.db")))
    window.theme_combo.setCurrentText("深色")
    assert "#141a24" in app.styleSheet()
    window.theme_combo.setCurrentText("海蓝")
    assert "#eef8fa" in app.styleSheet()
    window.theme_combo.setCurrentText("浅色")
    window.close()
    app.processEvents()


def test_system_pages_and_nested_topology_drive_stream_selection(app, tmp_path):
    database = tmp_path / "topology.db"
    topology = EntityRegistry(
        sites=(Site("site-1", "一号厂区"),),
        areas=(
            Area("area-1", "site-1", "生产区"),
            Area("area-2", "site-1", "产线 A", parent_id="area-1"),
        ),
        cameras=(CameraDevice("camera-1", "area-2", "东门摄像头"),),
        streams=(
            VideoStream("stream-main", "camera-1", "主码流", "rtsp://camera/main", StreamRole.MAIN),
            VideoStream("stream-sub", "camera-1", "子码流", "rtsp://camera/sub", StreamRole.SUB),
        ),
    )
    store = SQLiteStore(database)
    store.save_topology(topology)
    store.set_stream_health("stream-sub", "offline", "connection refused", actor="worker")
    store.close()

    window = MainWindow(config=MonitorConfig(db=str(database)))
    assert window.page_stack.count() == 13
    assert window.stream_table.rowCount() == 2
    assert window._stream_states["stream-sub"] == ("offline", "connection refused")
    assert set(window._preview_grid_widgets) == {"stream-main", "stream-sub"}
    assert window.overview_stream_table.rowCount() == 2
    assert window.overview_event_table.rowCount() == 0
    assert window.overview_stream_table.item(1, 2).text() == "离线"
    window._on_stream_frame("stream-sub", 4, 2)
    assert window.overview_stream_table.item(1, 3).text() == "1 / 2"
    window.topology_search_edit.setText("sub")
    assert [not window.stream_table.isRowHidden(row) for row in range(window.stream_table.rowCount())] == [False, True]
    assert not window.entity_tree.topLevelItem(0).isHidden()
    camera_item = window.entity_tree.topLevelItem(0).child(0).child(0).child(0)
    assert camera_item.child(0).isHidden()
    assert not camera_item.child(1).isHidden()
    window.topology_search_edit.clear()
    assert all(not window.stream_table.isRowHidden(row) for row in range(window.stream_table.rowCount()))
    assert [value.text() for value in window._overview_labels.values()] == ["1", "2", "1", "2", "0"]

    window._on_stream_preview("stream-main", np.zeros((80, 120, 3), dtype=np.uint8), ())
    window._on_stream_preview("stream-sub", np.zeros((80, 120, 3), dtype=np.uint8), ())
    assert window._preview_grid_widgets["stream-main"].has_image
    assert window._preview_grid_widgets["stream-sub"].has_image

    window._switch_page(2)
    assert window.page_stack.currentIndex() == 2
    window.stream_table.selectRow(1)
    app.processEvents()
    assert window._selected_stream_id == "stream-sub"
    assert window.monitor_scope_combo.currentData() == "selected"
    assert {item.stream_id for item in window._config_from_form().resolved_streams()} == {"stream-sub"}
    window.monitor_scope_combo.setCurrentIndex(window.monitor_scope_combo.findData("all"))
    assert {item.stream_id for item in window._config_from_form().resolved_streams()} == {"stream-main", "stream-sub"}
    assert window.probe_stream_button.isEnabled()
    assert window.url_edit.text() == "rtsp://camera/sub"
    assert "子码流" in window.channel_status_label.text()
    assert window.disarm_button.isEnabled()
    assert window.schedule_button.isEnabled()
    window._set_all_streams_armed(False, confirm=False)
    assert [window.stream_table.item(row, 7).text() for row in range(window.stream_table.rowCount())] == ["撤防", "撤防"]
    window._set_all_streams_armed(True, confirm=False)
    assert [window.stream_table.item(row, 7).text() for row in range(window.stream_table.rowCount())] == ["布防", "布防"]
    window._set_stream_armed(False)
    assert window.stream_table.item(1, 7).text() == "撤防"
    assert window.arm_button.isEnabled()
    window._toggle_selected_entity()
    assert window.stream_table.item(1, 6).text() == "停用"
    window._toggle_selected_entity()
    assert window.stream_table.item(1, 6).text() != "停用"
    window._on_stream_status("stream-sub", "degraded", "连接超时")
    assert window.stream_table.item(1, 6).text() == "降级"
    assert window._selected_stream_id == "stream-sub"
    rule = Rule(
        "rule-ui",
        stream_id="stream-sub",
        site_id="site-1",
        area_id="area-2",
        camera_id="camera-1",
        name="子码流全画面",
        type=RuleType.POLYGON,
        geometry=((0, 0), (1280, 0), (1280, 720), (0, 720)),
    )
    rule_store = SQLiteStore(database)
    rule_store.save_rule(rule)
    rule_store.close()
    window._refresh_rule_table()
    assert window.rule_table.rowCount() == 1
    window.rule_table.selectRow(0)
    window._toggle_selected_rule()
    assert window.rule_table.item(0, 4).text() == "停用"

    for index in range(13):
        window._switch_page(index)
        assert window.page_stack.currentIndex() == index
    for route, index in window._page_routes.items():
        window._switch_page(route)
        assert window.page_stack.currentIndex() == index
    window.close()
    app.processEvents()


def test_topology_page_persists_entities_added_by_management_actions(app, tmp_path):
    database = tmp_path / "managed-topology.db"
    window = MainWindow(config=MonitorConfig(db=str(database)))
    window._persist_topology_entity(Site("site-new", "新厂区"))
    window._persist_topology_entity(Area("area-new", "site-new", "仓储区"))
    window._persist_topology_entity(CameraDevice("camera-new", "area-new", "北门摄像头"))
    window._persist_topology_entity(
        VideoStream("stream-new", "camera-new", "分析流", "rtsp://new/analysis", StreamRole.ANALYSIS)
    )

    assert window._topology is not None
    assert window._topology.stream_path("stream-new").display_name == "新厂区 / 仓储区 / 北门摄像头 / 分析流"
    assert window.stream_table.rowCount() == 1
    reloaded = SQLiteStore(database)
    assert reloaded.load_topology().stream_path("stream-new").stream.role is StreamRole.ANALYSIS
    reloaded.close()
    window.close()
    app.processEvents()


def test_selected_stream_configuration_is_persisted_and_audited(app, tmp_path):
    database = tmp_path / "stream-edit.db"
    topology = EntityRegistry(
        sites=(Site("site-1", "Factory"),),
        areas=(Area("area-1", "site-1", "Workshop"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
        streams=(VideoStream("stream-1", "camera-1", "Main", "rtsp://old/live"),),
    )
    store = SQLiteStore(database)
    store.save_topology(topology)
    store.close()

    window = MainWindow(config=MonitorConfig(db=str(database)))
    window._select_stream("stream-1")
    assert window.save_stream_button.isEnabled()
    window.url_edit.setText("rtsp://new/live")
    window.model_edit.setText("models/custom.onnx")
    window.width_spin.setValue(1920)
    window.height_spin.setValue(1080)
    window.confidence_spin.setValue(0.6)
    window._save_selected_stream_config()

    restored = SQLiteStore(database)
    stream = restored.load_topology().get_stream("stream-1")
    assert stream.url == "rtsp://new/live"
    assert stream.model == "models/custom.onnx"
    assert (stream.width, stream.height, stream.confidence) == (1920, 1080, 0.6)
    audit = restored._connection_or_raise().execute(
        "SELECT action, actor FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1", ("stream-1",)
    ).fetchone()
    assert tuple(audit) == ("topology.stream.updated", "ui")
    restored.close()
    window._switch_page(4)
    assert window.audit_table.rowCount() >= 1
    window.audit_search_edit.setText("topology.stream.updated")
    assert window.audit_table.rowCount() >= 1
    window.close()
    app.processEvents()


def test_stream_configuration_handles_memory_session_and_invalid_input(app):
    topology = EntityRegistry(
        sites=(Site("site-1", "Factory"),),
        areas=(Area("area-1", "site-1", "Workshop"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
        streams=(VideoStream("stream-1", "camera-1", "Main", "rtsp://old/live"),),
    )
    window = MainWindow(config=MonitorConfig(db=":memory:", topology=topology))
    window._save_selected_stream_config()
    window._select_stream("stream-1")
    window.url_edit.clear()
    window._save_selected_stream_config()
    assert window._topology.get_stream("stream-1").url == "rtsp://old/live"
    window.url_edit.setText("rtsp://memory/live")
    window._save_selected_stream_config()
    assert window._topology.get_stream("stream-1").url == "rtsp://memory/live"
    window.close()
    app.processEvents()


def test_topology_entities_can_be_edited_and_audited(app, tmp_path, monkeypatch):
    database = tmp_path / "entity-edit.db"
    topology = EntityRegistry(
        sites=(Site("site-1", "Factory"),),
        areas=(Area("area-1", "site-1", "Workshop"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate", address="10.0.0.1"),),
    )
    store = SQLiteStore(database)
    store.save_topology(topology)
    store.close()
    window = MainWindow(config=MonitorConfig(db=str(database)))

    window.entity_tree.setCurrentItem(window.entity_tree.topLevelItem(0))
    monkeypatch.setattr(window, "_prompt_name", lambda *_args: "Factory North")
    window._edit_selected_entity()

    site_item = window.entity_tree.topLevelItem(0)
    camera_item = site_item.child(0).child(0)
    window.entity_tree.setCurrentItem(camera_item)
    responses = iter(("Gate North", "10.0.0.9"))
    monkeypatch.setattr(window, "_prompt_name", lambda *_args: next(responses))
    window._edit_selected_entity()

    restored = SQLiteStore(database)
    loaded = restored.load_topology()
    assert loaded.get_site("site-1").name == "Factory North"
    assert loaded.get_camera("camera-1").name == "Gate North"
    assert loaded.get_camera("camera-1").address == "10.0.0.9"
    actions = restored._connection_or_raise().execute(
        "SELECT action, actor FROM audit_log WHERE resource IN ('site-1', 'camera-1') ORDER BY id"
    ).fetchall()
    assert ("topology.site.updated", "ui") in [tuple(row) for row in actions]
    assert ("topology.camera.updated", "ui") in [tuple(row) for row in actions]
    restored.close()
    window.close()
    app.processEvents()


def test_bulk_stream_registration_creates_multiple_roles(app, tmp_path, monkeypatch):
    database = tmp_path / "bulk-streams.db"
    topology = EntityRegistry(
        sites=(Site("site-1", "Factory"),),
        areas=(Area("area-1", "site-1", "Workshop"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
    )
    store = SQLiteStore(database)
    store.save_topology(topology)
    store.close()
    window = MainWindow(config=MonitorConfig(db=str(database)))
    camera_item = window.entity_tree.topLevelItem(0).child(0).child(0)
    window.entity_tree.setCurrentItem(camera_item)
    from factoryguard.ui.qt import QInputDialog

    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: (
            "主码流 | rtsp://camera/main | main\n分析流 | rtsp://camera/analysis | analysis",
            True,
        ),
    )
    window._add_bulk_streams()
    restored = SQLiteStore(database)
    streams = restored.load_topology().streams
    assert {stream.role for stream in streams} == {StreamRole.MAIN, StreamRole.ANALYSIS}
    assert len(streams) == 2
    restored.close()
    window.close()
    app.processEvents()


def test_notification_settings_persist_without_storing_secret(app, tmp_path):
    database = tmp_path / "settings.db"
    first = MainWindow(config=MonitorConfig(db=str(database)))
    first.webhook_url_edit.setText("https://example.test/factoryguard")
    first.webhook_secret_edit.setText("temporary-secret")
    first.notification_interval_spin.setValue(17)
    first.notification_batch_spin.setValue(23)
    first._save_notification_settings()
    first.close()
    app.processEvents()

    second = MainWindow(config=MonitorConfig(db=str(database)))
    assert second.webhook_url_edit.text() == "https://example.test/factoryguard"
    assert second.notification_interval_spin.value() == 17
    assert second.notification_batch_spin.value() == 23
    assert second.webhook_secret_edit.text() == ""
    second.close()
    app.processEvents()


def test_operator_identity_persists_and_is_used_for_audit(app, tmp_path):
    database = tmp_path / "operator.db"
    first = MainWindow(config=MonitorConfig(db=str(database)))
    first.operator_name_edit.setText("夜班值守 A")
    first._save_operator_settings()
    first.close()
    app.processEvents()

    second = MainWindow(config=MonitorConfig(db=str(database)))
    assert second.operator_name_edit.text() == "夜班值守 A"
    second._persist_topology_entity(Site("site-operator", "Operator Site"))
    restored = SQLiteStore(database)
    audit = restored._connection_or_raise().execute(
        "SELECT actor FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1", ("site-operator",)
    ).fetchone()
    assert audit[0] == "夜班值守 A"
    restored.close()
    second.close()
    app.processEvents()


def test_auditor_role_cannot_modify_topology(app, tmp_path):
    database = tmp_path / "auditor-role.db"
    window = MainWindow(config=MonitorConfig(db=str(database)))
    window.operator_role_combo.setCurrentIndex(window.operator_role_combo.findData("auditor"))
    window._save_operator_settings()
    with pytest.raises(PermissionError):
        window._persist_topology_entity(Site("blocked-site", "Blocked"))
    restored = SQLiteStore(database)
    assert restored.load_topology().sites == ()
    restored.close()
    window.close()
    app.processEvents()


def test_notification_queue_operations_are_visible_in_notification_center(app, tmp_path, monkeypatch):
    database = tmp_path / "notification-queue.db"
    store = SQLiteStore(database)
    rule = Rule("queue-rule", "CH01", "fence", RuleType.POLYGON, ((0, 0), (10, 0), (10, 10)))
    event = Event.create(
        rule=rule,
        frame=Frame("CH01", 1, datetime.now(UTC), 20, 20),
        detection=Detection("person", 0.9, (1, 1, 4, 8), "queue-track"),
    )
    assert store.create_event_with_outbox(event)
    outbox_id = str(store.list_pending_outbox()[0]["id"])
    assert store.claim_pending_outbox()
    assert store.mark_outbox_failed(outbox_id, "temporary failure")
    store.close()

    window = MainWindow(config=MonitorConfig(db=str(database)))
    window._switch_page(5)
    assert window.notification_outbox_table.rowCount() == 1
    window.notification_outbox_table.selectRow(0)
    assert window.requeue_notification_button.isEnabled()
    from factoryguard.ui.qt import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window._requeue_selected_notification()
    restored = SQLiteStore(database)
    assert restored.outbox_counts()["retry"] == 1
    restored.close()
    window.close()
    app.processEvents()


def test_changing_database_path_reloads_topology(app, tmp_path):
    def create_database(path, site_id, stream_id):
        topology = EntityRegistry(
            sites=(Site(site_id, site_id),),
            areas=(Area(f"area-{site_id}", site_id, "Area"),),
            cameras=(CameraDevice(f"camera-{site_id}", f"area-{site_id}", "Camera"),),
            streams=(VideoStream(stream_id, f"camera-{site_id}", stream_id, f"rtsp://{stream_id}"),),
        )
        store = SQLiteStore(path)
        store.save_topology(topology)
        store.close()

    first_database = tmp_path / "first.db"
    second_database = tmp_path / "second.db"
    create_database(first_database, "site-first", "stream-first")
    create_database(second_database, "site-second", "stream-second")
    window = MainWindow(config=MonitorConfig(db=str(first_database)))
    assert window._topology.get_stream("stream-first").url == "rtsp://stream-first"
    window.db_edit.setText(str(second_database))
    window._refresh_entity_tree()
    assert window._topology.get_stream("stream-second").url == "rtsp://stream-second"
    assert window.stream_table.rowCount() == 1
    assert window.stream_table.item(0, 3).text() == "stream-second"
    window.close()
    app.processEvents()


def test_health_snapshot_and_diagnostics_export_are_redacted(app, tmp_path):
    database = tmp_path / "health.db"
    window = MainWindow(config=MonitorConfig(db=str(database)))
    window._on_stream_status("stream-1", "degraded", "rtsp://admin:secret@example.invalid/live timeout")
    window._switch_page(12)
    assert window.preflight_table.rowCount() >= 5
    assert "数据库" in window.health_label.text()
    snapshot = window._health_snapshot()
    assert snapshot["stream_states"]["stream-1"]["detail"] == "rtsp://***:***@example.invalid/live timeout"
    assert {item["name"] for item in snapshot["preflight"]} >= {
        "SQLite 数据库",
        "FFmpeg",
        "ONNX 模型",
        "媒体目录",
        "通知配置",
    }
    window._export_diagnostics()
    files = list((tmp_path / "diagnostics").glob("factoryguard-*.json"))
    assert len(files) == 1
    exported = json.loads(files[0].read_text(encoding="utf-8"))
    assert "secret" not in json.dumps(exported)
    window.close()
    app.processEvents()


def test_rule_geometry_editor_parser_accepts_points_and_rejects_malformed_input():
    assert parse_geometry("0,0; 100,0\n100,100;0,100") == (
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 100.0),
        (0.0, 100.0),
    )
    with pytest.raises(ValueError, match="几何点格式错误"):
        parse_geometry("0,0; bad")
