"""ONVIF device discovery, capability probing, media and PullPoint events.

The implementation speaks the ONVIF SOAP contract directly so one lightweight
adapter works across vendor extensions. WS-Discovery is used only to find
candidate XAddr values; all device operations then use authenticated SOAP.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from factoryguard.domain.models import (
    DeviceCapabilities,
    DeviceProtocol,
    EventSource,
    NativeEvent,
    VideoDevice,
)
from factoryguard.net import read_http_error

from .base import (
    DeviceAdapter,
    DeviceChannel,
    DeviceCredentials,
    DeviceInfo,
    EventSubscription,
    LiveStream,
    ProbeResult,
    Recording,
    require_open,
)

WS_DISCOVERY_ADDRESS = ("239.255.255.250", 3702)
SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
SOAP_ENC = "http://www.w3.org/2003/05/soap-encoding"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
TT = "http://www.onvif.org/ver10/schema"
TERSE = "http://www.onvif.org/ver10/events"
MEDIA = "http://www.onvif.org/ver10/media"
MEDIA2 = "http://www.onvif.org/ver20/media/wsdl"
DEVICE = "http://www.onvif.org/ver10/device/wsdl"
EVENTS = "http://www.onvif.org/ver10/events/wsdl"
RECORDING = "http://www.onvif.org/ver10/recording/wsdl"
REPLAY = "http://www.onvif.org/ver10/replay/wsdl"

ET.register_namespace("s", SOAP_ENV)
ET.register_namespace("tt", TT)
ET.register_namespace("ter", TERSE)


def _utc(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(
    node: ET.Element | None,
    local_name: str,
    default: str = "",
    *,
    direct_only: bool = False,
) -> str:
    # direct_only limits the search to immediate children, which matters for
    # SOAP replies that repeat the same tag inside sibling elements.
    if node is None:
        return default
    children = list(node) if direct_only else list(node.iter())
    for child in children:
        if _local(child.tag) == local_name and child.text:
            return child.text.strip()
    return default


def _find_child(node: ET.Element | None, local_name: str) -> ET.Element | None:
    if node is None:
        return None
    for child in node.iter():
        if _local(child.tag) == local_name:
            return child
    return None


def _items(node: ET.Element | None, local_name: str) -> tuple[ET.Element, ...]:
    if node is None:
        return ()
    return tuple(child for child in node.iter() if _local(child.tag) == local_name)


def _split_wsdl_address(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split() if part.strip())


@dataclass(frozen=True, slots=True)
class OnvifDiscoveredDevice:
    endpoint: str
    scopes: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    metadata_version: int = 0
    raw: Mapping[str, str] | None = None


class OnvifDiscovery:
    """WS-Discovery Probe implementation for local network inspection."""

    def discover(
        self, *, timeout: float = 3.0, probes: int = 2
    ) -> tuple[OnvifDiscoveredDevice, ...]:
        if timeout <= 0 or probes < 1:
            raise ValueError("discovery timeout and probe count must be positive")
        probe = _ws_discovery_probe()
        found: dict[str, OnvifDiscoveredDevice] = {}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(min(0.5, timeout))
            for _ in range(probes):
                sock.sendto(probe.encode("utf-8"), WS_DISCOVERY_ADDRESS)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        data, _address = sock.recvfrom(65535)
                    except TimeoutError:
                        break
                    for item in self._parse_matches(data):
                        found.setdefault(item.endpoint, item)
        return tuple(found[key] for key in sorted(found))

    @staticmethod
    def _parse_matches(data: bytes) -> tuple[OnvifDiscoveredDevice, ...]:
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return ()
        results: list[OnvifDiscoveredDevice] = []
        for match in _items(root, "ProbeMatch"):
            xaddrs = _split_wsdl_address(_child_text(match, "XAddrs"))
            if not xaddrs:
                continue
            scopes = _split_wsdl_address(_child_text(match, "Scopes"))
            types = _split_wsdl_address(_child_text(match, "Types"))
            try:
                metadata = int(_child_text(match, "MetadataVersion", "0"))
            except ValueError:
                metadata = 0
            results.append(
                OnvifDiscoveredDevice(
                    endpoint=xaddrs[0],
                    scopes=scopes,
                    types=types,
                    metadata_version=metadata,
                    raw={"xaddrs": " ".join(xaddrs), "scopes": " ".join(scopes)},
                )
            )
        return tuple(results)


def _ws_discovery_probe() -> str:
    message_id = f"uuid:{uuid4()}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="{SOAP_ENV}" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <s:Header>
  <d:MessageID>{message_id}</d:MessageID>
  <d:To s:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</d:To>
  <d:Action s:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</d:Action>
 </s:Header>
 <s:Body>
  <d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>
 </s:Body>
</s:Envelope>"""


class OnvifSoapClient:
    """Authenticated ONVIF SOAP request client with injectable transport."""

    def __init__(
        self,
        endpoint: str,
        credentials: DeviceCredentials | None = None,
        *,
        timeout: float = 5.0,
        transport: Any | None = None,
    ) -> None:
        if not endpoint.lower().startswith(("http://", "https://")):
            raise ValueError("ONVIF endpoint must use HTTP or HTTPS")
        self.endpoint = endpoint.rstrip("/")
        self.credentials = credentials or DeviceCredentials()
        self.timeout = timeout
        self.transport = transport
        self.last_status: int | None = None
        self.last_action = ""

    def call(self, service_endpoint: str, action: str, body: str) -> ET.Element:
        envelope = self._envelope(action, body)
        headers = {"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'}
        self.last_action = action
        content = self._post(service_endpoint, envelope.encode("utf-8"), headers)
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise RuntimeError(f"ONVIF returned invalid SOAP XML for {action}") from exc
        fault = _find_child(root, "Fault")
        if fault is not None:
            reason = (
                _child_text(fault, "Text")
                or _child_text(fault, "Reason")
                or _child_text(fault, "Value")
            )
            raise RuntimeError(f"ONVIF SOAP fault for {action}: {reason or 'unknown fault'}")
        return root

    def _post(self, address: str, payload: bytes, headers: Mapping[str, str]) -> bytes:
        if self.transport is not None:
            response = self.transport.post_xml(address, payload, headers)
            content = getattr(response, "content", response)
            self.last_status = int(getattr(response, "status_code", 200))
            return content if isinstance(content, bytes) else str(content).encode("utf-8")
        request = urllib.request.Request(
            address, data=payload, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.last_status = int(response.status)
                return response.read()
        except urllib.error.HTTPError as exc:
            self.last_status = int(exc.code)
            detail = read_http_error(exc)
            raise RuntimeError(
                f"ONVIF HTTP {exc.code} for {self.last_action}: {detail[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"ONVIF connection failed for {self.last_action}: {exc.reason}"
            ) from exc

    def _envelope(self, action: str, body: str) -> str:
        security = ""
        if self.credentials.username or self.credentials.password:
            nonce_bytes = os.urandom(24)
            nonce = base64.b64encode(nonce_bytes).decode("ascii")
            created_dt = datetime.now(UTC)
            created = created_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            expires = (
                (created_dt + timedelta(minutes=5))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            digest = base64.b64encode(
                hashlib.sha1(
                    nonce_bytes
                    + created.encode("ascii")
                    + self.credentials.password.encode("utf-8")
                ).digest()
            ).decode("ascii")
            security = f"""
  <o:Security s:mustUnderstand="true" xmlns:o="{WSSE}" xmlns:u="{WSU}">
   <u:Timestamp u:Id="_0"><u:Created>{created}</u:Created><u:Expires>{expires}</u:Expires></u:Timestamp>
   <o:UsernameToken>
    <o:Username>{_xml_escape(self.credentials.username)}</o:Username>
    <o:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</o:Password>
    <o:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce}</o:Nonce>
    <u:Created>{created}</u:Created>
   </o:UsernameToken>
  </o:Security>"""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="{SOAP_ENV}" xmlns:enc="{SOAP_ENC}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:tt="{TT}">
 <s:Header>{security}</s:Header>
 <s:Body>{body}</s:Body>
</s:Envelope>"""


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


class OnvifAdapter(DeviceAdapter):
    protocol = DeviceProtocol.ONVIF

    # A camera that drops one poll must not end the alarm stream.  The
    # subscription keeps retrying with a bounded backoff so a short outage
    # heals on its own while a permanently offline device still gives up.
    PULL_MAX_FAILURES: int = 10
    PULL_BACKOFF_SECONDS: float = 2.0
    PULL_BACKOFF_MAX_SECONDS: float = 15.0

    def __init__(
        self,
        device: VideoDevice,
        credentials: DeviceCredentials | None = None,
        *,
        soap: OnvifSoapClient | None = None,
    ) -> None:
        super().__init__(device, credentials)
        self.soap = soap or OnvifSoapClient(device.endpoint, self.credentials)
        self._info: DeviceInfo | None = None
        self._capabilities = DeviceCapabilities()
        self._services: dict[str, str] = {}
        self._profiles: tuple[DeviceChannel, ...] = ()
        self._media_is_media2 = False
        self._pull_cancelled = False

    def probe(self, *, timeout: float = 5.0) -> ProbeResult:
        require_open(self)
        started = time.monotonic()
        try:
            self.soap.timeout = timeout
            self.get_device_info()
            capabilities = self.get_capabilities()
            latency = (time.monotonic() - started) * 1000
            return ProbeResult(
                ok=True,
                detail="ONVIF 连接与认证成功",
                latency_ms=latency,
                info=self._info,
                capabilities=capabilities,
                diagnostics={
                    "last_action": self.soap.last_action,
                    "http_status": self.soap.last_status,
                },
            )
        except (RuntimeError, ValueError) as exc:
            return ProbeResult(
                ok=False,
                detail=str(exc),
                latency_ms=(time.monotonic() - started) * 1000,
                diagnostics={
                    "last_action": self.soap.last_action,
                    "http_status": self.soap.last_status,
                },
            )

    def get_device_info(self) -> DeviceInfo:
        require_open(self)
        root = self.soap.call(
            _service_url(self.device.endpoint, "device_service"),
            "GetDeviceInformation",
            "<GetDeviceInformation/>",
        )
        self._info = DeviceInfo(
            manufacturer=_child_text(root, "Manufacturer"),
            model=_child_text(root, "Model"),
            firmware=_child_text(root, "FirmwareVersion"),
            serial_number=_child_text(root, "SerialNumber"),
            hardware_id=_child_text(root, "HardwareId"),
        )
        return self._info

    def get_capabilities(self) -> DeviceCapabilities:
        require_open(self)
        body = '<GetServices xmlns="http://www.onvif.org/ver10/device/wsdl"><IncludeCapability>true</IncludeCapability></GetServices>'
        try:
            root = self.soap.call(
                _service_url(self.device.endpoint, "device_service"), "GetServices", body
            )
            for service in _items(root, "Service"):
                namespace = _child_text(service, "Namespace", direct_only=True)
                xaddr = _child_text(service, "XAddr", direct_only=True)
                if namespace and xaddr:
                    self._services[namespace] = xaddr
        except RuntimeError:
            root = self.soap.call(
                _service_url(self.device.endpoint, "device_service"),
                "GetCapabilities",
                '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"><Category>All</Category></GetCapabilities>',
            )
            for name in ("Device", "Media", "Events", "PTZ", "DeviceIO", "Extension"):
                node = _find_child(root, name)
                xaddr = _child_text(node, "XAddr")
                if xaddr:
                    self._services[name.lower()] = xaddr
        services = self._services
        event_types: tuple[str, ...] = ()
        events_service = self._service_for("events")
        if events_service:
            try:
                event_root = self.soap.call(
                    events_service,
                    "GetEventProperties",
                    '<GetEventProperties xmlns="http://www.onvif.org/ver10/events/wsdl"/>',
                )
                topic_set = _find_child(event_root, "TopicSet")
                if topic_set is None:
                    for node in event_root.iter():
                        if _local(node.tag) == "TopicSet":
                            topic_set = node
                            break
                if topic_set is not None:
                    names: set[str] = set()
                    for node in topic_set.iter():
                        local = _local(node.tag).lower()
                        if local in {"rule", "topic"}:
                            label = node.attrib.get("Name") or node.attrib.get("Topic") or ""
                            if label:
                                names.add(label.split(":")[-1].split("/")[-1].lower())
                    if not names:
                        # Some cameras list the topics directly under TopicSet.
                        for node in list(topic_set):
                            local = _local(node.tag).lower()
                            if local and local not in {"ruleengine"}:
                                names.add(local)
                    if names:
                        event_types = tuple(sorted(names))
            except RuntimeError:
                event_types = ()
        profiles: list[str] = []
        for namespace in services:
            lower = namespace.lower()
            if "/ver10/" in lower and "/wsdl" in lower:
                profiles.append("Profile S")
            if "/ver20/media" in lower:
                profiles.append("Profile T")
            if "/ver10/recording" in lower or "/ver10/replay" in lower:
                profiles.append("Profile G")
            if "/ver20/media" in lower and "analytics" in lower:
                profiles.append("Profile M")
        if not profiles and services:
            profiles.append("ONVIF Core")
        if event_types:
            profiles.append("Profile M")
        self._media_is_media2 = any("/ver20/media" in key.lower() for key in services)
        self._capabilities = DeviceCapabilities(
            live_video=any("media" in key.lower() for key in services),
            native_events=bool(event_types) or any("event" in key.lower() for key in services),
            recording_search=any(
                "recording" in key.lower() or "replay" in key.lower() for key in services
            ),
            ptz=any("ptz" in key.lower() for key in services),
            digital_io=any("deviceio" in key.lower() for key in services),
            metadata=bool(event_types)
            or any("analytics" in key.lower() or "metadata" in key.lower() for key in services),
            profiles=tuple(dict.fromkeys(profiles)),
            event_types=event_types,
        )
        return self._capabilities

    def list_channels(self) -> tuple[DeviceChannel, ...]:
        require_open(self)
        service = self._service_for("media")
        if not service:
            raise RuntimeError("设备未提供 ONVIF Media 服务")
        namespace = (
            "http://www.onvif.org/ver20/media/wsdl"
            if self._media_is_media2
            else "http://www.onvif.org/ver10/media/wsdl"
        )
        body = f'<GetProfiles xmlns="{namespace}"/>'
        root = self.soap.call(service, "GetProfiles", body)
        channels: list[DeviceChannel] = []
        for index, profile in enumerate(_items(root, "Profiles"), start=1):
            token = profile.attrib.get("token", f"profile-{index}")
            name = _child_text(profile, "Name", f"通道 {index}")
            configuration = _find_child(profile, "VideoEncoderConfiguration")
            resolution = _find_child(configuration, "Resolution")
            width = _int_text(_child_text(resolution, "Width"))
            height = _int_text(_child_text(resolution, "Height"))
            channels.append(
                DeviceChannel(
                    id=token,
                    name=name,
                    profile_token=token,
                    source_token=_child_text(profile, "SourceToken"),
                    encoding=_child_text(configuration, "Encoding"),
                    width=width,
                    height=height,
                )
            )
        self._profiles = tuple(channels)
        if not channels:
            raise RuntimeError("ONVIF Media 服务未返回媒体 Profile")
        return self._profiles

    def get_live_stream(self, channel_id: str, *, role: str = "sub") -> LiveStream:
        require_open(self)
        profiles = self._profiles or self.list_channels()
        profile = next((item for item in profiles if item.id == channel_id), None)
        if profile is None:
            raise KeyError(f"unknown ONVIF media profile: {channel_id}")
        service = self._service_for("media")
        stream = "RTP-Unicast"
        protocol = "RTSP"
        namespace = (
            "http://www.onvif.org/ver20/media/wsdl"
            if self._media_is_media2
            else "http://www.onvif.org/ver10/media/wsdl"
        )
        body = f"""<GetStreamUri xmlns="{namespace}">
 <StreamSetup><Stream>{stream}</Stream><Transport><Protocol>{protocol}</Protocol></Transport></StreamSetup>
 <ProfileToken>{_xml_escape(profile.profile_token)}</ProfileToken>
</GetStreamUri>"""
        root = self.soap.call(service, "GetStreamUri", body)
        uri = _child_text(root, "Uri")
        if not uri:
            raise RuntimeError("ONVIF 未返回视频流地址")
        return LiveStream(
            uri=uri,
            profile_id=profile.id,
            transport="rtsp",
            width=profile.width,
            height=profile.height,
        )

    def search_recordings(
        self,
        channel_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[Recording, ...]:
        require_open(self)
        if end_at <= start_at:
            raise ValueError("recording end_at must be after start_at")
        service = self._service_for("recording")
        if not service:
            raise RuntimeError("设备未启用 ONVIF Profile G 录像检索")
        scope = f"<Scope><Value>{_xml_escape(channel_id)}</Value></Scope>"
        body = f"""<FindRecordings xmlns="http://www.onvif.org/ver10/recording/wsdl">
 <SearchScope>{scope}</SearchScope>
 <IncludeJobs>false</IncludeJobs>
 <MaxResults>100</MaxResults>
 <KeepAliveTime>PT10S</KeepAliveTime>
</FindRecordings>"""
        root = self.soap.call(service, "FindRecordings", body)
        token = _child_text(root, "SearchToken")
        result_root = self.soap.call(
            service,
            "GetRecordingSearchResults",
            f'<GetRecordingSearchResults xmlns="http://www.onvif.org/ver10/recording/wsdl"><SearchToken>{_xml_escape(token)}</SearchToken><MinResults>0</MinResults><MaxResults>100</MaxResults><WaitTime>PT1S</WaitTime></GetRecordingSearchResults>',
        )
        recordings: list[Recording] = []
        for index, item in enumerate(_items(result_root, "RecordingItem"), start=1):
            recording_id = _child_text(item, "RecordingToken") or f"recording-{index}"
            summary = _find_child(item, "Description") or item
            start = _utc(_child_text(summary, "From"))
            finish = _utc(_child_text(summary, "Until"))
            recordings.append(
                Recording(
                    id=recording_id,
                    channel_id=channel_id,
                    start_at=start,
                    end_at=max(start, finish),
                    source="onvif",
                    description=_child_text(item, "Description"),
                )
            )
        return tuple(recordings)

    def get_recording_uri(self, recording_id: str) -> LiveStream:
        require_open(self)
        service = self._service_for("replay")
        if not service:
            raise RuntimeError("设备未启用 ONVIF Profile G 录像回放")
        body = f"""<GetReplayUri xmlns="http://www.onvif.org/ver10/replay/wsdl">
 <RecordingToken>{_xml_escape(recording_id)}</RecordingToken>
 <StreamSetup><Stream>RTP-Unicast</Stream><Transport><Protocol>RTSP</Protocol></Transport></StreamSetup>
</GetReplayUri>"""
        root = self.soap.call(service, "GetReplayUri", body)
        uri = _child_text(root, "Uri")
        if not uri:
            raise RuntimeError("ONVIF 未返回录像回放地址")
        return LiveStream(uri=uri, profile_id=recording_id, transport="rtsp")

    def subscribe_events(self, channel_id: str | None = None) -> EventSubscription:
        require_open(self)
        service = self._service_for("events")
        if not service:
            # Discovering the services here keeps the call self-contained
            # instead of relying on a previous probe.
            try:
                self.get_capabilities()
            except (RuntimeError, ValueError) as exc:
                raise RuntimeError("设备未启用 ONVIF 事件服务") from exc
            service = self._service_for("events")
        if not service:
            raise RuntimeError("设备未启用 ONVIF 事件服务")
        body = """<CreatePullPointSubscription xmlns="http://www.onvif.org/ver10/events/wsdl">
 <InitialTerminationTime>PT1H</InitialTerminationTime>
</CreatePullPointSubscription>"""
        root = self.soap.call(service, "CreatePullPointSubscription", body)
        address = _child_text(_find_child(root, "SubscriptionReference"), "Address") or service
        return EventSubscription(
            source="onvif",
            iterator=self._pull_messages(address, channel_id),
        )

    def _pull_messages(self, address: str, channel_id: str | None) -> Iterator[NativeEvent]:
        failures = 0
        while not self.closed and not self._pull_cancelled:
            body = """<PullMessages xmlns="http://www.onvif.org/ver10/events/wsdl">
 <Timeout>PT30S</Timeout><MessageLimit>100</MessageLimit>
</PullMessages>"""
            try:
                root = self.soap.call(address, "PullMessages", body)
            except RuntimeError:
                # A single dropped connection must not end the alarm stream.
                # Retry with a bounded backoff and only give up when the
                # camera has been unreachable for a sustained period.
                failures += 1
                if failures > self.PULL_MAX_FAILURES:
                    break
                time.sleep(min(self.PULL_BACKOFF_SECONDS * failures, self.PULL_BACKOFF_MAX_SECONDS))
                continue
            failures = 0
            for message in _items(root, "Message"):
                event = self._parse_event(message, channel_id)
                if event is not None:
                    yield event

    def _parse_event(self, message: ET.Element, channel_id: str | None) -> NativeEvent | None:
        topic = _child_text(message, "Topic")
        source = _find_child(message, "Source")
        simple_items = _items(source, "SimpleItem")
        source_channel = channel_id
        for item in simple_items:
            name = item.attrib.get("Name", "").lower()
            if name in {"source", "channel", "channelid", "videosourceconfigurationtoken"}:
                source_channel = item.attrib.get("Value") or source_channel
        data = _find_child(message, "Data")
        values = {
            item.attrib.get("Name", "").lower(): item.attrib.get("Value", "")
            for item in _items(data, "SimpleItem")
        }
        state = str(values.get("state", values.get("value", ""))).lower()
        code = (
            topic.rsplit("/", 1)[-1].lower() or "propertyoperation"
            if "PropertyOperation" in message.attrib
            else "onvif_event"
        )
        if code in {"propertyoperation", "source"} and topic:
            code = topic.lower().replace("/", ".")
        occurred = _utc(message.attrib.get("UtcTime") or _child_text(message, "UtcTime"))
        fingerprint = "|".join(
            (
                topic,
                occurred.isoformat(),
                source_channel or "",
                code,
                repr(sorted(values.items())),
            )
        )
        source_event_id = f"onvif-{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()}"
        return NativeEvent(
            device_id=self.device.id,
            channel_id=source_channel or "unknown",
            event_code=code,
            occurred_at=occurred,
            source=EventSource.ONVIF,
            title=_friendly_onvif_title(topic, code),
            level=(
                "high"
                if state in {"true", "active", "triggered", "on", "open"}
                else "info"
                if state in {"false", "inactive", "idle", "off", "closed"}
                else "medium"
            ),
            source_event_id=source_event_id,
            stream_id=source_channel,
            payload={
                "topic": topic,
                "items": values,
                "raw_property_operation": message.attrib.get("PropertyOperation", ""),
            },
        )

    def _service_for(self, kind: str) -> str:
        for namespace, address in self._services.items():
            if kind in namespace.lower():
                return address
        return ""

    def close(self) -> None:
        self.closed = True
        self._pull_cancelled = True


def _friendly_onvif_title(topic: str, code: str) -> str:
    lowered = f"{topic} {code}".lower()
    mappings = (
        (("tamper", "遮挡"), "摄像头遮挡告警"),
        (("videoloss", "videosignal"), "视频信号丢失"),
        (("fielddetector", "fielddetection", "intrusion"), "检测到区域入侵"),
        (("linedetector", "linecrossing"), "检测到越线"),
        (("loitering", "dwell"), "检测到人员滞留"),
        (("cellmotiondetector", "motiondetect", "motion"), "检测到移动"),
        (("alarm",), "设备主动报警"),
    )
    for markers, label in mappings:
        if any(marker in lowered for marker in markers):
            return label
    return "设备原生告警"


def _service_url(endpoint: str, default_path: str) -> str:
    lowered = endpoint.lower()
    if any(
        marker in lowered
        for marker in ("/onvif/", "/device_service", "/device/wsdl", "/media_service")
    ):
        return endpoint
    return endpoint.rstrip("/") + "/" + default_path


def _int_text(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "OnvifAdapter",
    "OnvifDiscoveredDevice",
    "OnvifDiscovery",
    "OnvifSoapClient",
]
