from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from factoryguard.app.native_events import NativeEventIngestor, decide_analysis
from factoryguard.devices.base import DeviceCredentials
from factoryguard.devices.gb28181.server import Gb28181Platform
from factoryguard.devices.gb28181.sip import SipMessage, digest_response, verify_digest
from factoryguard.devices.onvif import OnvifAdapter, OnvifDiscovery
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
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
    assert healthy.use_native is True
    assert healthy.use_local is False
    assert fallback.use_local is True
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
    channel = adapter.list_channels()[0]
    assert channel.id == "p1"
    stream = adapter.get_live_stream(channel.id)
    assert stream.uri == "rtsp://cam/live"
    subscription = adapter.subscribe_events("ch1")
    event = next(subscription.iterator)
    subscription.close()
    assert event.event_code == "propertyoperation"
    assert event.channel_id == "ch1"


def test_ws_discovery_parses_probe_match_without_network(monkeypatch):
    payload = b"""<Envelope><Body><ProbeMatches><ProbeMatch>
<XAddrs>http://10.0.0.8/onvif/device_service</XAddrs>
<Scopes>onvif://www.onvif.org/type/video_encoder</Scopes>
<Types>dn:NetworkVideoTransmitter</Types><MetadataVersion>3</MetadataVersion>
</ProbeMatch></ProbeMatches></Body></Envelope>"""
    assert OnvifDiscovery._parse_matches(payload)[0].endpoint.endswith("/device_service")
