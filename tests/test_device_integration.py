from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from factoryguard.app.native_events import NativeEventIngestor, decide_analysis
from factoryguard.devices.base import DeviceCredentials
from factoryguard.devices.gb28181.server import Gb28181Platform
from factoryguard.devices.gb28181.sip import DigestChallenge, SipMessage, digest_response, verify_digest
from factoryguard.devices.onvif import OnvifAdapter, OnvifDiscovery
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
    Event,
    EventSource,
    NativeEvent,
    Rule,
    RuleMode,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.inference.base import InferenceHealth
from factoryguard.media.rtsp import RawFrame
from factoryguard.ui.worker import MonitorConfig, MonitorWorker, StreamRuntimeConfig
from factoryguard.inference.profiles import ModelBinding, ModelProfile
from factoryguard.security.credentials import CredentialVault, XorTestProtector, default_vault
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.topology_io import topology_from_csv, topology_to_csv


def _topology() -> EntityRegistry:
    return EntityRegistry(
        sites=(Site("site-1", "厂区"),),
        areas=(Area("area-1", "site-1", "仓库"),),
        devices=(
            VideoDevice(
                "device-1",
                "site-1",
                "area-1",
                "东门摄像机",
                DeviceProtocol.ONVIF,
                "http://10.0.0.8/onvif/device_service",
                capabilities=DeviceCapabilities(
                    live_video=True,
                    native_events=True,
                    recording_search=True,
                    profiles=("Profile T", "Profile G", "Profile M"),
                    event_types=("motiondetect",),
                ),
            ),
        ),
        cameras=(CameraDevice("camera-1", "area-1", "东门", device_id="device-1", channel_id="ch1"),),
        streams=(
            VideoStream(
                "stream-1",
                "camera-1",
                "子码流",
                "rtsp://10.0.0.8/live",
                StreamRole.SUB,
                device_id="device-1",
                analysis_policy=AnalysisPolicy.LOCAL_FALLBACK,
                native_event_codes=("motiondetect",),
            ),
        ),
    )



def test_device_health_snapshot_round_trip():
    store = SQLiteStore(":memory:")
    try:
        topology = _topology()
        device = topology.devices[0]
        healthy = VideoDevice(
            id=device.id,
            site_id=device.site_id,
            area_id=device.area_id,
            name=device.name,
            protocol=device.protocol,
            endpoint=device.endpoint,
            capabilities=device.capabilities,
            analysis_policy=device.analysis_policy,
            last_probe_at="2026-01-01T00:00:00+00:00",
            last_ok_at="2026-01-01T00:00:01+00:00",
            health_status="ready",
            health_detail="ONVIF connected",
        )
        store.save_topology(
            EntityRegistry(
                sites=topology.sites,
                areas=topology.areas,
                devices=(healthy,),
                cameras=topology.cameras,
                streams=topology.streams,
            )
        )
        restored = store.load_topology().devices[0]
        assert restored.health_status == "ready"
        assert restored.last_probe_at == "2026-01-01T00:00:00+00:00"
        assert restored.last_ok_at == "2026-01-01T00:00:01+00:00"
        assert restored.health_detail == "ONVIF connected"
    finally:
        store.close()


def test_native_event_maps_to_existing_event_workflow_and_deduplicates_sources():
    store = SQLiteStore(":memory:")
    try:
        rule = Rule(
            "rule-native",
            stream_id="stream-1",
            name="设备移动侦测",
            trigger_mode=RuleMode.NATIVE_EVENT,
            native_event_codes=("motiondetect",),
            dedup_window_seconds=10,
            actions=("event", "notify"),
        )
        store.save_rule(rule)
        ingestor = NativeEventIngestor(store, (rule,), channels=("webhook",))
        native = NativeEvent(
            device_id="device-1",
            channel_id="ch1",
            event_code="motiondetect",
            occurred_at=datetime.now(UTC),
            source=EventSource.ONVIF,
            source_event_id="onvif-event-1",
            stream_id="stream-1",
            payload={"semantic": "motion"},
        )
        first = ingestor.ingest(native)
        second = ingestor.ingest(native)
        assert len(first) == 1
        assert second == ()
        assert first[0].source is EventSource.ONVIF
        assert first[0].native is True
        rows = store.search_events(stream_id="stream-1")
        assert len(rows) == 1
        assert rows[0]["source"] == "onvif"
    finally:
        store.close()



def test_native_event_is_merged_with_recent_local_fallback_event():
    from dataclasses import replace as dataclass_replace

    store = SQLiteStore(":memory:")
    try:
        native_rule = Rule(
            "rule-native",
            stream_id="stream-1",
            trigger_mode=RuleMode.NATIVE_EVENT,
            native_event_codes=("motiondetect",),
            dedup_window_seconds=30,
        )
        store.save_rule(native_rule)
        now = datetime.now(UTC)
        native = NativeEvent(
            device_id="device-1",
            channel_id="ch1",
            event_code="motiondetect",
            occurred_at=now,
            source=EventSource.ONVIF,
            source_event_id="native-1",
            stream_id="stream-1",
            payload={"semantic": "motion"},
        )
        local_event = Event(
            id="evt-local",
            dedup_key="local-1",
            rule_id="rule-analysis",
            channel_id="stream-1",
            level="high",
            occurred_at=now,
            target={"condition": "motion"},
            source=EventSource.LOCAL_INFERENCE,
            correlation_key=native.correlation_key,
            dedup_window_seconds=30,
        )
        assert store.create_event_with_outbox(local_event, enqueue=False)
        ingestor = NativeEventIngestor(store, (native_rule,), channels=("webhook",))
        assert ingestor.ingest(native) == ()
        assert len(store.search_events(stream_id="stream-1")) == 1
    finally:
        store.close()



def test_native_event_rule_executes_http_action(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "factoryguard.app.native_events.execute_http_action",
        lambda event, *, url, timeout: calls.append((event.id, url, timeout)) or (True, "ok"),
    )
    store = SQLiteStore(":memory:")
    try:
        rule = Rule(
            "rule-http",
            stream_id="stream-1",
            trigger_mode=RuleMode.NATIVE_EVENT,
            native_event_codes=("alarm",),
            actions=("event", "http"),
            http_action_url="https://example.invalid/hook",
            http_action_timeout=2.0,
        )
        ingestor = NativeEventIngestor(store, (rule,), channels=())
        event = NativeEvent(
            device_id="device-1",
            channel_id="ch1",
            event_code="alarm",
            occurred_at=datetime.now(UTC),
            source=EventSource.GB28181,
            source_event_id="gb-alarm-1",
            stream_id="stream-1",
        )
        stored = ingestor.ingest(event)
        assert len(stored) == 1
        assert calls and calls[0][1] == "https://example.invalid/hook"
    finally:
        store.close()


def test_analysis_policy_prefers_native_events_and_falls_back_when_unhealthy():
    capabilities = DeviceCapabilities(native_events=True, event_types=("motiondetect",))
    native_rule = Rule(
        "native",
        stream_id="s",
        trigger_mode=RuleMode.NATIVE_EVENT,
        native_event_codes=("motiondetect",),
    )
    analysis_rule = Rule(
        "analysis",
        stream_id="s",
        type="polygon",
        geometry=((0, 0), (10, 0), (10, 10)),
        condition="intrusion",
    )
    healthy = decide_analysis(
        policy=AnalysisPolicy.LOCAL_FALLBACK,
        capabilities=capabilities,
        native_subscription_healthy=True,
        native_rules=(native_rule,),
    )
    fallback = decide_analysis(
        policy=AnalysisPolicy.LOCAL_FALLBACK,
        capabilities=capabilities,
        native_subscription_healthy=False,
        native_rules=(native_rule,),
        analysis_rules=(analysis_rule,),
    )
    native_subscription_fallback = decide_analysis(
        policy=AnalysisPolicy.LOCAL_FALLBACK,
        capabilities=capabilities,
        native_subscription_healthy=False,
        native_rules=(native_rule,),
    )
    assert healthy.use_native is True
    assert healthy.use_local is False
    assert fallback.use_local is True
    assert native_subscription_fallback.use_local is True
    assert fallback.policy is AnalysisPolicy.LOCAL_FALLBACK


def test_topology_csv_round_trips_physical_device_capabilities():
    restored = topology_from_csv(topology_to_csv(_topology()))
    assert restored.to_dict() == _topology().to_dict()
    assert restored.stream_path("stream-1").device.capabilities.recording_search is True


def test_sqlite_migrates_legacy_camera_and_persists_model_configuration(tmp_path):
    database = tmp_path / "legacy.db"
    store = SQLiteStore(database)
    try:
        store.save_topology(
            EntityRegistry(
                sites=(Site("site-1", "厂区"),),
                areas=(Area("area-1", "site-1", "车间"),),
                cameras=(CameraDevice("camera-old", "area-1", "旧摄像机"),),
                streams=(VideoStream("stream-old", "camera-old", "流", "rtsp://camera/live"),),
            )
        )
    finally:
        store.close()
    migrated = SQLiteStore(database)
    try:
        topology = migrated.load_topology()
        assert len(topology.devices) == 1
        assert topology.cameras[0].device_id == topology.devices[0].id
        assert topology.streams[0].device_id == topology.devices[0].id
        profile = ModelProfile("model-1", "人员模型", model_uri="models/person.onnx")
        binding = ModelBinding("binding-1", "model-1", "stream", "stream-old")
        migrated.save_model_configuration((profile,), (binding,))
        profiles, bindings = migrated.load_model_configuration()
        assert profiles[0].model_uri == "models/person.onnx"
        assert bindings[0].scope_id == "stream-old"
    finally:
        migrated.close()



def test_configuration_export_redacts_device_and_stream_credentials():
    source = SQLiteStore(":memory:")
    try:
        topology = _topology()
        device = VideoDevice(
            id="device-onvif",
            site_id="site-1",
            area_id="area-1",
            name="ONVIF Camera",
            protocol=DeviceProtocol.ONVIF,
            endpoint="http://admin:pw-device-123@10.0.0.8/onvif/device_service",
            capabilities=DeviceCapabilities(live_video=True, native_events=True),
        )
        camera = CameraDevice(
            "camera-secret",
            "area-1",
            "ONVIF Camera",
            device_id=device.id,
            channel_id="ch1",
        )
        stream = VideoStream(
            "stream-secret",
            camera.id,
            "Main",
            "rtsp://admin:pw-stream-123@10.0.0.8/live",
            device_id=device.id,
        )
        source.save_topology(
            EntityRegistry(
                sites=topology.sites,
                areas=topology.areas,
                devices=(device,),
                cameras=(camera,),
                streams=(stream,),
            )
        )
        exported = source.export_configuration()
        serialized = json.dumps(exported, ensure_ascii=False)
        assert exported["schema_version"] == 2
        assert exported["credentials_redacted"] is True
        assert "pw-device-123" not in serialized
        assert "pw-stream-123" not in serialized
        restored = SQLiteStore(":memory:")
        try:
            restored.import_configuration(exported, replace=True)
            restored_device = restored.load_topology().get_device(device.id)
            restored_stream = restored.load_topology().get_stream(stream.id)
            assert "***:***@" in restored_device.endpoint
            assert "***:***@" in restored_stream.url
        finally:
            restored.close()
    finally:
        source.close()



def test_configuration_import_accepts_legacy_schema_one():
    store = SQLiteStore(":memory:")
    try:
        store.import_configuration(
            {
                "schema_version": 1,
                "topology": {
                    "sites": [{"id": "site-old", "name": "旧厂区", "description": "", "enabled": True}],
                    "areas": [],
                    "cameras": [],
                    "streams": [],
                },
                "rules": [],
                "stream_arming": {},
                "arming_schedules": [],
            },
            replace=True,
        )
        assert store.load_topology().sites[0].id == "site-old"
    finally:
        store.close()


def test_credential_vault_round_trip_and_config_export_has_no_secret():
    vault = CredentialVault(XorTestProtector(b"test-key"))
    credentials = DeviceCredentials(username="admin", password="secret", token="token")
    encoded = vault.encode(credentials)
    assert "secret" not in encoded
    assert vault.decode(encoded) == credentials



def test_windows_dpapi_credential_vault_round_trip():
    import sys

    if sys.platform != "win32":
        return
    vault = default_vault()
    credentials = DeviceCredentials(username="camera", password="local-secret")
    protected = vault.encode(credentials)
    assert "local-secret" not in protected
    assert vault.decode(protected) == credentials


def test_sip_message_and_digest_authentication():
    message = SipMessage.parse(
        b"REGISTER sip:3402000000@3402000000 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.8:5060;branch=z9hG4bKabc\r\n"
        b"From: <sip:340200000000000001@3402000000>;tag=cam\r\n"
        b"To: <sip:340200000000000001@3402000000>\r\n"
        b"Call-ID: call-1\r\nCSeq: 1 REGISTER\r\nContent-Length: 0\r\n\r\n"
    )
    assert message.method == "REGISTER"
    assert message.branch == "z9hG4bKabc"
    authorization = digest_response(
        username="cam",
        password="secret",
        realm="3402000000",
        nonce="nonce",
        method="REGISTER",
        uri="sip:3402000000@3402000000",
    )
    assert verify_digest(authorization, password="secret", method="REGISTER", uri="sip:3402000000@3402000000")
    assert not verify_digest(authorization, password="wrong", method="REGISTER", uri="sip:3402000000@3402000000")


def test_gb28181_platform_registers_catalogs_and_receives_alarm():
    events = []
    platform = Gb28181Platform(
        platform_id="340200000000000000",
        domain="3402000000",
        password="",
        on_native_event=events.append,
    )
    platform._transport = SimpleNamespace(sendto=lambda *_args: None)
    register = (
        b"REGISTER sip:3402000000@3402000000 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.8:5060;branch=z9hG4bKreg\r\n"
        b"From: <sip:340200000000000001@3402000000>;tag=cam\r\n"
        b"To: <sip:340200000000000001@3402000000>\r\n"
        b"Call-ID: reg\r\nCSeq: 1 REGISTER\r\nExpires: 3600\r\nContent-Length: 0\r\n\r\n"
    )
    platform.handle_datagram(register, ("10.0.0.8", 5060))
    catalog_body = """<?xml version="1.0"?><Response><CmdType>Catalog</CmdType><SN>1</SN><DeviceID>340200000000000001</DeviceID><SumNum>1</SumNum><DeviceList><Item><DeviceID>340200000000000002</DeviceID><Name>东门</Name><Status>ON</Status></Item></DeviceList></Response>""".encode()
    catalog = (
        b"MESSAGE sip:3402000000@3402000000 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.8:5060;branch=z9hG4bKmsg\r\n"
        b"From: <sip:340200000000000001@3402000000>;tag=cam\r\n"
        b"To: <sip:340200000000000000@3402000000>\r\n"
        b"Call-ID: msg\r\nCSeq: 2 MESSAGE\r\nContent-Type: Application/MANSCDP+xml\r\n"
        + f"Content-Length: {len(catalog_body)}\r\n\r\n".encode()
        + catalog_body
    )
    platform.handle_datagram(catalog, ("10.0.0.8", 5060))
    assert platform.list_channels("340200000000000001")[0].name == "东门"
    alarm_body = b"""<?xml version="1.0"?><Notify><CmdType>Alarm</CmdType><SN>2</SN><DeviceID>340200000000000002</DeviceID><AlarmMethod>2</AlarmMethod><Time>2026-01-01T00:00:00Z</Time></Notify>"""
    alarm = (
        b"MESSAGE sip:3402000000@3402000000 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.8:5060;branch=z9hG4bKalarm\r\n"
        b"From: <sip:340200000000000001@3402000000>;tag=cam\r\n"
        b"To: <sip:340200000000000000@3402000000>\r\n"
        b"Call-ID: alarm\r\nCSeq: 3 MESSAGE\r\nContent-Type: Application/MANSCDP+xml\r\n"
        + f"Content-Length: {len(alarm_body)}\r\n\r\n".encode()
        + alarm_body
    )
    platform.handle_datagram(alarm, ("10.0.0.8", 5060))
    assert events and events[0].source is EventSource.GB28181



def test_onvif_adapter_probes_media_streams_and_pullpoint_events():
    class FakeSoap:
        last_status = 200
        last_action = ""

        def call(self, _service, action, _body):
            self.last_action = action
            if action == "GetDeviceInformation":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    "<Response><Manufacturer>Acme</Manufacturer><Model>Cam-1</Model>"
                    "<FirmwareVersion>2.0</FirmwareVersion><SerialNumber>S-1</SerialNumber></Response>"
                )
            if action == "GetServices":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    "<Response>"
                    "<Service><Namespace>http://www.onvif.org/ver10/device/wsdl</Namespace><XAddr>http://cam/device</XAddr></Service>"
                    "<Service><Namespace>http://www.onvif.org/ver20/media/wsdl</Namespace><XAddr>http://cam/media2</XAddr></Service>"
                    "<Service><Namespace>http://www.onvif.org/ver10/events/wsdl</Namespace><XAddr>http://cam/events</XAddr></Service>"
                    "</Response>"
                )
            if action == "GetEventProperties":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    "<Response><TopicSet><MotionAlarm><Rule>true</Rule></MotionAlarm>"
                    "<TamperAlarm><Rule>true</Rule></TamperAlarm></TopicSet></Response>"
                )
            if action == "GetProfiles":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    '<Response><Profiles token="p1"><Name>Main</Name>'
                    "<VideoEncoderConfiguration><Resolution><Width>1920</Width><Height>1080</Height></Resolution>"
                    "<Encoding>H264</Encoding></VideoEncoderConfiguration></Profiles></Response>"
                )
            if action == "GetStreamUri":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    "<Response><Uri>rtsp://cam/live</Uri></Response>"
                )
            if action == "CreatePullPointSubscription":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    "<Response><SubscriptionReference><Address>http://cam/pullpoint</Address></SubscriptionReference></Response>"
                )
            if action == "PullMessages":
                return __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                    '<Response><Message UtcTime="2026-01-01T00:00:00Z" PropertyOperation="Changed">'
                    '<Source><SimpleItem Name="VideoSourceConfigurationToken" Value="ch1"/></Source>'
                    '<Data><SimpleItem Name="State" Value="true"/></Data></Message></Response>'
                )
            raise AssertionError(action)

    topology = _topology()
    adapter = OnvifAdapter(topology.devices[0], DeviceCredentials("admin", "secret"), soap=FakeSoap())
    result = adapter.probe()
    assert result.ok
    assert result.info.manufacturer == "Acme"
    assert result.capabilities.live_video is True
    assert result.capabilities.native_events is True
    assert "motionalarm" in result.capabilities.event_types
    channel = adapter.list_channels()[0]
    assert channel.id == "p1"
    stream = adapter.get_live_stream(channel.id)
    assert stream.uri == "rtsp://cam/live"
    subscription = adapter.subscribe_events("ch1")
    event = next(subscription.iterator)
    subscription.close()
    assert event.event_code == "propertyoperation"
    assert event.channel_id == "ch1"



def _gb_register(auth: str = "") -> bytes:
    headers = [
        "REGISTER sip:3402000000@3402000000 SIP/2.0",
        "Via: SIP/2.0/TCP 10.0.0.8:5060;branch=z9hG4bKtcp",
        "From: <sip:340200000000000001@3402000000>;tag=cam",
        "To: <sip:340200000000000001@3402000000>",
        "Call-ID: tcp-reg",
        "CSeq: 1 REGISTER",
        "Expires: 3600",
        "Content-Length: 0",
    ]
    if auth:
        headers.insert(5, f"Authorization: {auth}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode()


def test_gb28181_tcp_registration_uses_digest_and_routes_response_over_tcp():
    platform = Gb28181Platform(
        platform_id="340200000000000000",
        domain="3402000000",
        password="secret",
    )
    transport = SimpleNamespace(write=lambda payload: transport.writes.append(payload), writes=[])
    platform.handle_message(
        SipMessage.parse(_gb_register()),
        ("10.0.0.8", 5060),
        transport=transport,
        transport_kind="tcp",
    )
    challenge = SipMessage.parse(transport.writes[0])
    assert challenge.status_code == 401
    parsed = DigestChallenge.parse(challenge.get("www-authenticate"))
    auth = digest_response(
        username="340200000000000001",
        password="secret",
        realm=parsed.realm,
        nonce=parsed.nonce,
        method="REGISTER",
        uri="sip:3402000000@3402000000",
    )
    platform.handle_message(
        SipMessage.parse(_gb_register(auth)),
        ("10.0.0.8", 5060),
        transport=transport,
        transport_kind="tcp",
    )
    response = SipMessage.parse(transport.writes[1])
    assert response.status_code == 200
    registration = platform.registrations["340200000000000001"]
    assert registration.transport_kind == "tcp"
    assert registration.transport is transport


def test_gb28181_tcp_invite_ack_bye_and_recording_query():
    async def run() -> None:
        platform = Gb28181Platform(
            platform_id="340200000000000000",
            domain="3402000000",
        )
        transport = SimpleNamespace(write=lambda payload: transport.writes.append(payload), writes=[])
        platform.registrations["device-1"] = __import__(
            "factoryguard.devices.gb28181.server", fromlist=["Gb28181Registration"]
        ).Gb28181Registration(
            device_id="device-1",
            address=("10.0.0.8", 5060),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            channels={
                "ch1": __import__(
                    "factoryguard.devices.gb28181.server", fromlist=["Gb28181Channel"]
                ).Gb28181Channel(id="ch1", name="Camera"),
            },
            transport_kind="tcp",
            transport=transport,
        )
        invite_task = asyncio.create_task(platform.invite("device-1", "ch1", timeout=1))
        await asyncio.sleep(0)
        invite = SipMessage.parse(transport.writes[0])
        assert invite.method == "INVITE"
        assert "SIP/2.0/TCP" in invite.via
        response = invite.response(
            200,
            "OK",
            extra_headers=(("Content-Type", "application/sdp"),),
            body=b"v=0\r\n",
        )
        platform._complete_response(response)
        session = await invite_task
        ack = SipMessage.parse(transport.writes[1])
        assert ack.method == "ACK"
        await platform.bye(session)
        bye = SipMessage.parse(transport.writes[2])
        assert bye.method == "BYE"

        record_task = asyncio.create_task(
            platform.query_recordings(
                "device-1",
                "ch1",
                start_at=datetime.now(UTC) - timedelta(hours=1),
                end_at=datetime.now(UTC),
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        query = SipMessage.parse(transport.writes[3])
        assert query.method == "MESSAGE"
        assert b"RecordInfo" in query.body
        query_root = ET.fromstring(query.body)
        sn = query_root.findtext("SN")
        platform._handle_record_info(
            ET.fromstring(
                f"<Response><CmdType>RecordInfo</CmdType><SN>{sn}</SN><DeviceID>ch1</DeviceID>"
                "<SumNum>1</SumNum><DeviceList><Item><DeviceID>ch1</DeviceID>"
                "<Name>录像</Name><StartTime>2026-01-01T00:00:00Z</StartTime>"
                "<EndTime>2026-01-01T01:00:00Z</EndTime></Item></DeviceList></Response>"
            )
        )
        recordings = await record_task
        assert recordings[0].channel_id == "ch1"

    asyncio.run(run())



def test_gb28181_platform_starts_udp_and_tcp_listeners_and_closes():
    async def run():
        platform = Gb28181Platform(
            platform_id="340200000000000000",
            domain="3402000000",
        )
        host, port = await platform.start(host="127.0.0.1", port=0)
        assert host == "127.0.0.1"
        assert port > 0
        assert platform.started
        assert platform.diagnostics["transports"] == ("udp", "tcp")
        await platform.close()
        assert not platform.started

    asyncio.run(run())


def test_gb28181_malformed_transport_does_not_stop_registration():
    platform = Gb28181Platform(
        platform_id="340200000000000000",
        domain="3402000000",
    )
    transport = SimpleNamespace(sendto=lambda *_args: None)
    platform._transport = transport
    platform.handle_datagram(b"not sip", ("10.0.0.8", 5060))
    platform.handle_datagram(_gb_register(), ("10.0.0.8", 5060))
    assert "340200000000000001" in platform.registrations



def test_ws_discovery_probe_receives_local_device_match(monkeypatch):
    import socket as socket_module

    payload = (
        b"<Envelope><Body><ProbeMatches><ProbeMatch>"
        b"<XAddrs>http://10.0.0.8/onvif/device_service</XAddrs>"
        b"<Scopes>onvif://www.onvif.org/type/video_encoder</Scopes>"
        b"<Types>dn:NetworkVideoTransmitter</Types><MetadataVersion>1</MetadataVersion>"
        b"</ProbeMatch></ProbeMatches></Body></Envelope>"
    )

    class FakeSocket:
        def __init__(self, *_args, **_kwargs):
            self.sent = []
            self.received = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def setsockopt(self, *_args):
            return None

        def settimeout(self, _timeout):
            return None

        def sendto(self, data, address):
            self.sent.append((data, address))

        def recvfrom(self, _size):
            if self.received:
                raise socket_module.timeout()
            self.received = True
            return payload, ("10.0.0.8", 3702)

    fake = FakeSocket()
    monkeypatch.setattr("factoryguard.devices.onvif.socket.socket", lambda *_args, **_kwargs: fake)
    found = OnvifDiscovery().discover(timeout=0.1, probes=1)
    assert found[0].endpoint == "http://10.0.0.8/onvif/device_service"
    assert fake.sent[0][1] == ("239.255.255.250", 3702)


def test_onvif_soap_client_posts_authenticated_xml(monkeypatch):
    from factoryguard.devices.onvif import OnvifSoapClient

    response_xml = (
        b"<Envelope><Body><GetDeviceInformationResponse>"
        b"<Manufacturer>Acme</Manufacturer><Model>Cam</Model>"
        b"</GetDeviceInformationResponse></Body></Envelope>"
    )

    class FakeResponse:
        status = 200

        def read(self):
            return response_xml

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        captured["headers"] = request.headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("factoryguard.devices.onvif.urllib.request.urlopen", fake_urlopen)
    client = OnvifSoapClient(
        "http://10.0.0.8/onvif/device_service",
        DeviceCredentials(username="admin", password="secret"),
        timeout=3,
    )
    root = client.call("http://10.0.0.8/onvif/device_service", "GetDeviceInformation", "<GetDeviceInformation/>")
    assert root.find(".//Manufacturer").text == "Acme"
    assert b"UsernameToken" in captured["data"]
    assert captured["timeout"] == 3



def test_monitor_worker_stops_local_analysis_when_native_subscription_recovers(monkeypatch):
    states = ["healthy", "healthy", "degraded", "degraded", "healthy"]
    calls = []

    class FakeStore:
        def load_rules(self, **_kwargs):
            return []

        def save_rule(self, _rule):
            return None

        def get_stream_armed(self, _stream_id, default=True):
            return default

        def get_arming_schedule(self, _stream_id):
            return None

        def list_device_subscriptions(self):
            return {"device-1": {"state": states.pop(0) if states else "healthy"}}

        def set_stream_health(self, *_args, **_kwargs):
            return None

        def close(self):
            return None

    class FakeEngine:
        def infer_image(self, _image):
            calls.append(frame_index[0])
            return ()

    class FakeCapture:
        def __init__(self, *_args, **_kwargs):
            pass

        @contextmanager
        def stream(self, *, timeout):
            def frames():
                for frame_index[0] in range(20):
                    yield RawFrame(__import__("numpy").zeros((2, 2, 3), dtype="uint8"), frame_index[0])
                worker._stop.set()

            yield frames()

    frame_index = [0]
    worker = MonitorWorker(
        MonitorConfig(
            db=":memory:",
            notification_channels=(),
            streams=(
                StreamRuntimeConfig(
                    stream_id="stream-1",
                    url="rtsp://camera/live",
                    device_id="device-1",
                    capabilities=DeviceCapabilities(native_events=True),
                    native_subscription_healthy=True,
                    analysis_policy=AnalysisPolicy.LOCAL_FALLBACK,
                ),
            ),
        )
    )
    monkeypatch.setattr("factoryguard.ui.worker.SQLiteStore", lambda *_args, **_kwargs: FakeStore())
    monkeypatch.setattr("factoryguard.ui.worker.RTSPCapture", FakeCapture)
    monkeypatch.setattr(
        "factoryguard.ui.worker.require_ffmpeg",
        lambda: SimpleNamespace(version="fake"),
    )
    worker._shared_engine = lambda _runtime: (FakeEngine(), InferenceHealth("ready", "fake"))
    worker._run_stream_once(SimpleNamespace(), worker.config.streams[0])
    assert calls
    assert min(calls) >= 5
    assert max(calls) <= 15


def test_ws_discovery_parses_probe_match_without_network(monkeypatch):
    payload = b"""<Envelope><Body><ProbeMatches><ProbeMatch>
<XAddrs>http://10.0.0.8/onvif/device_service</XAddrs>
<Scopes>onvif://www.onvif.org/type/video_encoder</Scopes>
<Types>dn:NetworkVideoTransmitter</Types><MetadataVersion>3</MetadataVersion>
</ProbeMatch></ProbeMatches></Body></Envelope>"""
    assert OnvifDiscovery._parse_matches(payload)[0].endpoint.endswith("/device_service")
