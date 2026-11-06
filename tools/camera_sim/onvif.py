"""Small ONVIF Profile S simulator for multi-camera integration tests."""

from __future__ import annotations

import re
import socket
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
DEVICE = "http://www.onvif.org/ver10/device/wsdl"
MEDIA = "http://www.onvif.org/ver10/media/wsdl"
EVENTS = "http://www.onvif.org/ver10/events/wsdl"
DISCOVERY = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
ADDRESSING = "http://schemas.xmlsoap.org/ws/2004/08/addressing"


@dataclass(frozen=True, slots=True)
class OnvifCameraSpec:
    camera_id: str
    name: str
    stream_uri: str
    manufacturer: str = "FactoryGuard Simulator"
    model: str = "Virtual ONVIF Camera"
    firmware: str = "1.0.0"
    width: int = 640
    height: int = 360


class _OnvifHandler(BaseHTTPRequestHandler):
    server: OnvifHttpServer

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        parts = self.path.strip("/").split("/")
        camera_id = parts[1] if len(parts) >= 2 and parts[0] == "onvif" else ""
        camera = self.server.cameras.get(camera_id)
        if camera is None:
            self.send_error(404, "unknown simulated camera")
            return
        action = self.headers.get("action", "").strip('"')
        if not action:
            match = re.search(
                r'action="([^"]+)"', self.headers.get("Content-Type", ""), re.IGNORECASE
            )
            if match:
                action = match.group(1)
        if not action:
            try:
                root = ET.fromstring(body)
                body_node = next((node for node in root.iter() if _local(node.tag) == "Body"), None)
                action = (
                    _local(body_node[0].tag) if body_node is not None and len(body_node) else ""
                )
            except ET.ParseError:
                action = ""
        response = self._soap_response(action, camera)
        payload = response.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _pull_messages_body(self, camera: OnvifCameraSpec) -> str:
        """Return queued alarms, or an empty pull response when there are none."""
        events = self.server.take_events(camera.camera_id)
        if not events:
            return (
                "<tews:PullMessagesResponse>"
                "<tews:CurrentTime>2026-01-01T00:00:00Z</tews:CurrentTime>"
                "</tews:PullMessagesResponse>"
            )
        messages: list[str] = []
        for event in events:
            messages.append(
                '<tews:Message UtcTime="'
                + escape(event["utc_time"])
                + '" PropertyOperation="Changed">'
                "<tews:Source>"
                '<tt:SimpleItem Name="VideoSourceConfigurationToken" Value="'
                + escape(camera.camera_id)
                + '/Profile_1"/>'
                "</tews:Source>"
                "<tews:Data>"
                '<tt:SimpleItem Name="Motion" Value="' + escape(event["state"]) + '"/>'
                "</tews:Data>"
                "</tews:Message>"
            )
        return (
            "<tews:PullMessagesResponse>"
            "<tews:CurrentTime>2026-01-01T00:00:00Z</tews:CurrentTime>"
            + "".join(messages)
            + "</tews:PullMessagesResponse>"
        )

    def log_message(self, _format: str, *_args: object) -> None:
        return None

    def _soap_response(self, action: str, camera: OnvifCameraSpec) -> str:
        endpoint = self.server.device_endpoint(camera.camera_id)
        media_endpoint = self.server.device_endpoint(camera.camera_id, service="media")
        events_endpoint = self.server.device_endpoint(camera.camera_id, service="events")
        if action == "GetDeviceInformation":
            body = (
                "<tds:GetDeviceInformationResponse>"
                f"<tds:Manufacturer>{escape(camera.manufacturer)}</tds:Manufacturer>"
                f"<tds:Model>{escape(camera.model)}</tds:Model>"
                f"<tds:FirmwareVersion>{escape(camera.firmware)}</tds:FirmwareVersion>"
                f"<tds:SerialNumber>SIM-{escape(camera.camera_id)}</tds:SerialNumber>"
                f"<tds:HardwareId>{escape(camera.camera_id)}</tds:HardwareId>"
                "</tds:GetDeviceInformationResponse>"
            )
        elif action == "GetServices":
            body = (
                "<tds:GetServicesResponse>"
                "<tds:Service>"
                f"<tds:Namespace>{MEDIA}</tds:Namespace>"
                f"<tds:XAddr>{escape(media_endpoint)}</tds:XAddr>"
                "<tds:Version><tt:Major>24</tt:Major><tt:Minor>12</tt:Minor></tds:Version>"
                "</tds:Service>"
                "<tds:Service>"
                f"<tds:Namespace>{EVENTS}</tds:Namespace>"
                f"<tds:XAddr>{escape(events_endpoint)}</tds:XAddr>"
                "<tds:Version><tt:Major>24</tt:Major><tt:Minor>12</tt:Minor></tds:Version>"
                "</tds:Service>"
                "</tds:GetServicesResponse>"
            )
        elif action == "GetCapabilities":
            body = (
                "<tds:GetCapabilitiesResponse><tds:Capabilities>"
                f"<tds:Device><tt:XAddr>{escape(endpoint)}</tt:XAddr></tds:Device>"
                f"<tds:Media><tt:XAddr>{escape(media_endpoint)}</tt:XAddr>"
                "<tt:StreamingCapabilities><tt:RTPMulticast>false</tt:RTPMulticast>"
                "<tt:RTP_TCP>true</tt:RTP_TCP><tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>"
                "</tt:StreamingCapabilities></tds:Media>"
                "</tds:Capabilities></tds:GetCapabilitiesResponse>"
            )
        elif action == "GetProfiles":
            body = (
                "<trt:GetProfilesResponse>"
                '<trt:Profiles token="profile-main" fixed="true">'
                "<tt:Name>MainStream</tt:Name>"
                '<tt:VideoSourceConfiguration token="source-main"><tt:Name>VideoSource</tt:Name>'
                "<tt:SourceToken>source-main</tt:SourceToken>"
                '<tt:Bounds x="0" y="0" width="'
                + str(camera.width)
                + '" height="'
                + str(camera.height)
                + '"/>'
                "</tt:VideoSourceConfiguration>"
                '<tt:VideoEncoderConfiguration token="encoder-main"><tt:Name>H264</tt:Name>'
                "<tt:Encoding>H264</tt:Encoding>"
                "<tt:Resolution><tt:Width>"
                + str(camera.width)
                + "</tt:Width><tt:Height>"
                + str(camera.height)
                + "</tt:Height></tt:Resolution>"
                "</tt:VideoEncoderConfiguration>"
                "</trt:Profiles></trt:GetProfilesResponse>"
            )
        elif action == "GetStreamUri":
            body = (
                "<trt:GetStreamUriResponse><trt:MediaUri>"
                f"<tt:Uri>{escape(camera.stream_uri)}</tt:Uri>"
                "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
                "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout>"
                "</trt:MediaUri></trt:GetStreamUriResponse>"
            )
        elif action == "GetEventProperties":
            body = (
                "<ter:GetEventPropertiesResponse>"
                "<ter:TopicSet>"
                "<tt:RuleEngine>"
                '<tt:Rule Name="MotionAlarm" Topic="tns1:VideoSource/MotionAlarm" Enabled="true"/>'
                '<tt:Rule Name="TamperAlarm" Topic="tns1:VideoSource/TamperAlarm" Enabled="true"/>'
                "</tt:RuleEngine>"
                "</ter:TopicSet>"
                "</ter:GetEventPropertiesResponse>"
            )
        elif action == "CreatePullPointSubscription":
            body = (
                "<tews:CreatePullPointSubscriptionResponse>"
                f"<tews:SubscriptionReference>{escape(events_endpoint)}</tews:SubscriptionReference>"
                "</tews:CreatePullPointSubscriptionResponse>"
            )
        elif action == "PullMessages":
            body = self._pull_messages_body(camera)
        elif action == "GetSnapshotUri":
            body = (
                "<trt:GetSnapshotUriResponse><trt:MediaUri>"
                f"<tt:Uri>{escape(self.server.snapshot_url(camera.camera_id))}</tt:Uri>"
                "</trt:MediaUri></trt:GetSnapshotUriResponse>"
            )
        else:
            body = (
                "<ter:GetEventPropertiesResponse>"
                "<ter:TopicSet><tt:RuleEngine><tt:RuleConfiguration/></tt:RuleEngine></ter:TopicSet>"
                "</ter:GetEventPropertiesResponse>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" xmlns:tds="{DEVICE}" '
            f'xmlns:trt="{MEDIA}" xmlns:tt="http://www.onvif.org/ver10/schema" '
            f'xmlns:ter="http://www.onvif.org/ver10/events/wsdl" '
            f'xmlns:tews="http://www.onvif.org/ver10/events/wsdl">'
            f"<s:Body>{body}</s:Body></s:Envelope>"
        )


class OnvifHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], cameras: dict[str, OnvifCameraSpec]) -> None:
        self.cameras = cameras
        self._events: dict[str, list[dict[str, str]]] = {}
        self._event_lock = threading.Lock()
        super().__init__(address, _OnvifHandler)

    def take_events(self, camera_id: str) -> list[dict[str, str]]:
        """Pop the queued alarms for one camera."""
        with self._event_lock:
            queued = self._events.get(camera_id, [])
            self._events[camera_id] = []
        return queued

    def push_event(self, camera_id: str, state: str = "true") -> None:
        """Queue one alarm for delivery on the next PullMessages call."""
        with self._event_lock:
            self._events.setdefault(camera_id, []).append(
                {"utc_time": "2026-01-01T00:00:00Z", "state": state}
            )

    def device_endpoint(self, camera_id: str, *, service: str = "device") -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/onvif/{camera_id}/{service}"

    def snapshot_url(self, camera_id: str) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/snapshot/{camera_id}.jpg"


class OnvifCameraServer:
    """Expose one or more simulated ONVIF devices on a single HTTP port."""

    def __init__(
        self, cameras: list[OnvifCameraSpec], *, host: str = "127.0.0.1", port: int = 8000
    ) -> None:
        self.cameras = {camera.camera_id: camera for camera in cameras}
        self.host = host
        self.port = port
        self._server: OnvifHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._discovery: _OnvifDiscoveryResponder | None = None

    def start(self) -> None:
        self._server = OnvifHttpServer((self.host, self.port), self.cameras)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="camera-sim-onvif", daemon=True
        )
        self._thread.start()
        self._discovery = _OnvifDiscoveryResponder(self)
        self._discovery.start()

    def stop(self) -> None:
        if self._discovery is not None:
            self._discovery.stop()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def push_event(self, camera_id: str, state: str = "true") -> None:
        """Queue one alarm for delivery on the next PullMessages call."""
        if self._server is not None:
            self._server.push_event(camera_id, state)

    def endpoint(self, camera_id: str) -> str:
        if self._server is None:
            return f"http://{self.host}:{self.port}/onvif/{camera_id}/device"
        return self._server.device_endpoint(camera_id)

    def snapshot_url(self, camera_id: str) -> str:
        if self._server is None:
            return f"http://{self.host}:{self.port}/snapshot/{camera_id}.jpg"
        return self._server.snapshot_url(camera_id)


class _OnvifDiscoveryResponder(threading.Thread):
    def __init__(self, owner: OnvifCameraServer) -> None:
        super().__init__(name="camera-sim-onvif-discovery", daemon=True)
        self.owner = owner
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.bind(("", 3702))
            self._socket.settimeout(0.5)
            self._available = True
        except OSError:
            self._available = False

    def run(self) -> None:
        if not self._available:
            return
        while not self._stop.is_set():
            try:
                data, address = self._socket.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            if b"Probe" not in data:
                continue
            for camera in self.owner.cameras.values():
                message = self._probe_match(camera)
                self._socket.sendto(message.encode("utf-8"), address)

    def stop(self) -> None:
        self._stop.set()
        self._socket.close()
        self.join(timeout=1.0)

    def _probe_match(self, camera: OnvifCameraSpec) -> str:
        endpoint = self.owner.endpoint(camera.camera_id)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" xmlns:d="{DISCOVERY}" xmlns:a="{ADDRESSING}">'
            "<s:Body><d:ProbeMatches><d:ProbeMatch>"
            f"<a:EndpointReference><a:Address>urn:uuid:sim-{escape(camera.camera_id)}</a:Address></a:EndpointReference>"
            "<d:Types>dn:NetworkVideoTransmitter</d:Types>"
            f"<d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/{escape(camera.name)}</d:Scopes>"
            f"<d:XAddrs>{escape(endpoint)}</d:XAddrs>"
            "<d:MetadataVersion>1</d:MetadataVersion>"
            "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


__all__ = ["OnvifCameraServer", "OnvifCameraSpec"]
