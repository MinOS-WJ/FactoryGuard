from __future__ import annotations

"""Local GB/T 28181 platform-side SIP endpoint.

FactoryGuard receives registrations from compatible cameras, encoders and
NVRs. The implementation intentionally uses asyncio UDP only; no PJSIP or
native service is required. Media still flows over the negotiated RTP port and
is handed to the existing FFmpeg pipeline by a live session descriptor.
"""

import asyncio
import queue
import re
import secrets
import socket
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from factoryguard.domain.models import (
    AnalysisPolicy,
    DeviceCapabilities,
    DeviceProtocol,
    EventSource,
    NativeEvent,
    VideoDevice,
)

from ..base import (
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
from .sip import DigestChallenge, SipMessage, verify_digest


@dataclass(slots=True)
class Gb28181Channel:
    id: str
    name: str
    manufacturer: str = ""
    model: str = ""
    owner: str = ""
    civil_code: str = ""
    address: str = ""
    parental: int = 0
    register_way: int = 1
    status: str = "ON"


@dataclass(slots=True)
class Gb28181Registration:
    device_id: str
    address: tuple[str, int]
    expires_at: datetime
    user_agent: str = ""
    channels: dict[str, Gb28181Channel] = field(default_factory=dict)


@dataclass(slots=True)
class Gb28181LiveSession:
    channel_id: str
    call_id: str
    local_media_port: int
    remote_media_host: str
    remote_media_port: int
    rtp_socket: socket.socket | None

    @property
    def ffmpeg_uri(self) -> str:
        return f"rtp://0.0.0.0:{self.local_media_port}"

    def close(self) -> None:
        if self.rtp_socket is not None:
            try:
                self.rtp_socket.close()
            except OSError:
                pass


class Gb28181Platform(asyncio.DatagramProtocol):
    """SIP registrar, catalog receiver, alarm receiver and INVITE controller."""

    def __init__(
        self,
        *,
        platform_id: str,
        domain: str = "3402000000",
        password: str = "",
        host: str = "0.0.0.0",
        port: int = 5060,
        on_native_event: Callable[[NativeEvent], None] | None = None,
    ) -> None:
        if not platform_id.strip() or not domain.strip():
            raise ValueError("GB/T 28181 platform_id and domain are required")
        self.platform_id = platform_id.strip()
        self.domain = domain.strip()
        self.password = password
        self.host = host
        self.port = port
        self.on_native_event = on_native_event
        self.registrations: dict[str, Gb28181Registration] = {}
        self.last_errors: list[str] = []
        self._transport: asyncio.DatagramTransport | None = None
        self._pending: dict[tuple[str, str], asyncio.Future[SipMessage]] = {}
        self._dialogs: dict[str, Gb28181LiveSession] = {}
        self._requests_sent = 0
        self._messages_received = 0
        self.native_events: queue.Queue[NativeEvent] = queue.Queue(maxsize=1000)
        self._recording_buffers: dict[str, list[Recording]] = {}
        self._recording_futures: dict[str, asyncio.Future[tuple[Recording, ...]]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def started(self) -> bool:
        return self._transport is not None

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "platform_id": self.platform_id,
            "domain": self.domain,
            "started": self.started,
            "registrations": len(self.registrations),
            "channels": sum(len(item.channels) for item in self.registrations.values()),
            "active_streams": len(self._dialogs),
            "requests_sent": self._requests_sent,
            "messages_received": self._messages_received,
            "last_errors": list(self.last_errors[-10:]),
        }

    async def start(self, *, host: str | None = None, port: int | None = None) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=(host or self.host, self.port if port is None else port),
        )
        self._transport = transport
        self.loop = loop
        sockname = transport.get_extra_info("sockname")
        return str(sockname[0]), int(sockname[1])

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._messages_received += 1
        try:
            self.handle_datagram(data, addr)
        except Exception as exc:  # noqa: BLE001 - one malformed datagram must not stop registration
            self._record_error(f"{addr[0]}: {exc}")

    def error_received(self, exc: Exception) -> None:
        self._record_error(str(exc))

    def handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        message = SipMessage.parse(data)
        if not message.is_request:
            self._complete_response(message)
            return
        method = message.method
        if method == "REGISTER":
            self._handle_register(message, addr)
        elif method == "MESSAGE":
            self._handle_message(message, addr)
        elif method == "BYE":
            self._handle_bye(message, addr)
        elif method == "ACK":
            return
        else:
            self._send(message.response(405, "Method Not Allowed", extra_headers=(("Allow", "REGISTER, MESSAGE, INVITE, ACK, BYE"),)), addr)

    def _handle_register(self, message: SipMessage, addr: tuple[str, int]) -> None:
        device_id = _sip_user(message.to_header) or _sip_user(message.from_header)
        if not device_id:
            self._send(message.response(400, "Bad Request"), addr)
            return
        authorization = message.get("authorization")
        if self.password and not verify_digest(authorization, password=self.password, method="REGISTER", uri=message.request_uri):
            challenge = DigestChallenge(realm=self.domain, nonce=secrets.token_hex(16))
            self._send(
                message.response(
                    401,
                    "Unauthorized",
                    extra_headers=(("WWW-Authenticate", challenge.header()),),
                ),
                addr,
            )
            return
        expires = _expires(message.get("expires", "3600"))
        if expires == 0:
            self.registrations.pop(device_id, None)
        else:
            existing = self.registrations.get(device_id)
            self.registrations[device_id] = Gb28181Registration(
                device_id=device_id,
                address=addr,
                expires_at=_future(expires),
                user_agent=message.get("user-agent"),
                channels=existing.channels if existing else {},
            )
        self._send(
            message.response(
                200,
                "OK",
                extra_headers=(
                    ("Date", datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")),
                    ("Expires", str(expires)),
                ),
            ),
            addr,
        )

    def _handle_message(self, message: SipMessage, addr: tuple[str, int]) -> None:
        self._send(message.response(200, "OK"), addr)
        if not message.body:
            return
        try:
            root = ET.fromstring(message.body)
        except ET.ParseError as exc:
            self._record_error(f"{addr[0]}: invalid GB28181 XML: {exc}")
            return
        command = _xml_text(root, "CmdType").lower()
        device_id = _xml_text(root, "DeviceID") or _sip_user(message.from_header)
        registration = self.registrations.get(device_id)
        if command == "keepalive":
            if registration is not None:
                registration.expires_at = _future(360)
            return
        if command == "status":
            registration = self.registrations.get(device_id)
            if registration is not None:
                registration.expires_at = _future(3600)
                status = _xml_text(root, "Status", "OK")
                channel_id = _xml_text(root, "DeviceID", device_id)
                channel = registration.channels.get(channel_id)
                if channel is not None:
                    channel.status = status
            return
        if command == "catalog":
            if registration is None:
                registration = Gb28181Registration(device_id, addr, _future(3600))
                self.registrations[device_id] = registration
            for item in _xml_items(root, "Item"):
                channel_id = _xml_text(item, "DeviceID")
                if not channel_id:
                    continue
                registration.channels[channel_id] = Gb28181Channel(
                    id=channel_id,
                    name=_xml_text(item, "Name", channel_id),
                    manufacturer=_xml_text(item, "Manufacturer"),
                    model=_xml_text(item, "Model"),
                    owner=_xml_text(item, "Owner"),
                    civil_code=_xml_text(item, "CivilCode"),
                    address=_xml_text(item, "Address"),
                    parental=int(_xml_int(item, "Parental")),
                    register_way=int(_xml_int(item, "RegisterWay", 1)),
                    status=_xml_text(item, "Status", "ON"),
                )
            return
        if command in {"alarm", "videolosssignal", "tamperdetection", "motiondetect", "fielddetection"}:
            event = NativeEvent(
                device_id=device_id,
                channel_id=_xml_text(root, "DeviceID", device_id),
                event_code=_normalize_gb_event(command, _xml_text(root, "AlarmMethod")),
                occurred_at=_gb_time(_xml_text(root, "Time")),
                source=EventSource.GB28181,
                title=_xml_text(root, "AlarmDescription", command),
                level="critical" if command == "alarm" else "high",
                source_event_id=_xml_text(root, "SN") or f"{device_id}:{command}:{_xml_text(root, 'Time')}",
                stream_id=_xml_text(root, "DeviceID", device_id),
                payload={"command": command, "alarm_method": _xml_text(root, "AlarmMethod"), "alarm_type": _xml_text(root, "AlarmType")},
            )
            try:
                self.native_events.put_nowait(event)
            except queue.Full:
                pass
            if self.on_native_event is not None:
                self.on_native_event(event)
            return
        if command == "recordinfo":
            self._handle_record_info(root)

    def _handle_bye(self, message: SipMessage, addr: tuple[str, int]) -> None:
        self._send(message.response(200, "OK"), addr)
        session = self._dialogs.pop(message.call_id, None)
        if session is not None:
            session.close()

    def _handle_record_info(self, root: ET.Element) -> None:
        sn = _xml_text(root, "SN")
        if not sn:
            return
        device_id = _xml_text(root, "DeviceID")
        items: list[Recording] = []
        for item in _xml_items(root, "Item"):
            channel_id = _xml_text(item, "DeviceID") or device_id
            start = _gb_time(_xml_text(item, "StartTime"))
            end = _gb_time(_xml_text(item, "EndTime"))
            items.append(
                Recording(
                    id=_xml_text(item, "DeviceID") or f"{channel_id}:{start.isoformat()}",
                    channel_id=channel_id,
                    start_at=start,
                    end_at=max(start, end),
                    source="gb28181",
                    description=_xml_text(item, "Name") or _xml_text(item, "Address"),
                )
            )
        buffer = self._recording_buffers.setdefault(sn, [])
        buffer.extend(items)
        expected = _xml_int(root, "SumNum", len(buffer))
        if len(buffer) < expected:
            return
        future = self._recording_futures.pop(sn, None)
        if future is not None and not future.done():
            future.set_result(tuple(buffer))
        self._recording_buffers.pop(sn, None)

    async def query_recordings(
        self,
        device_id: str,
        channel_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        timeout: float = 8.0,
    ) -> tuple[Recording, ...]:
        registration = self.registrations.get(device_id)
        if registration is None:
            raise RuntimeError("GB/T 28181 设备未注册")
        loop = asyncio.get_running_loop()
        sn = str(loop.time_ns() % 1_000_000_000)
        future: asyncio.Future[tuple[Recording, ...]] = loop.create_future()
        self._recording_futures[sn] = future
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Query>
 <CmdType>RecordInfo</CmdType><SN>{sn}</SN><DeviceID>{_xml_escape(channel_id)}</DeviceID>
 <StartTime>{start_at.astimezone(UTC).isoformat()}</StartTime>
 <EndTime>{end_at.astimezone(UTC).isoformat()}</EndTime>
 <Secutity><Type>0</Type></Secutity>
</Query>""".encode("utf-8")
        request = SipMessage.request(
            "MESSAGE",
            f"sip:{device_id}@{registration.address[0]}:{registration.address[1]}",
            from_header=f"<sip:{self.platform_id}@{self.domain}>;tag={secrets.token_hex(6)}",
            to_header=f"<sip:{device_id}@{self.domain}>",
            call_id=f"record-{sn}@{self.domain}",
            cseq=1,
            via=f"SIP/2.0/UDP {_local_signaling_host(registration.address)}:{self.port};branch=z9hG4bK{secrets.token_hex(10)};rport",
            content_type="Application/MANSCDP+xml",
            body=body,
        )
        self._send(request, registration.address)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._recording_futures.pop(sn, None)
            self._recording_buffers.pop(sn, None)

    def list_channels(self, device_id: str | None = None) -> tuple[Gb28181Channel, ...]:
        if device_id is not None:
            registration = self.registrations.get(device_id)
            return tuple(registration.channels.values()) if registration else ()
        result: list[Gb28181Channel] = []
        for registration in self.registrations.values():
            result.extend(registration.channels.values())
        return tuple(result)

    async def invite(
        self,
        device_id: str,
        channel_id: str,
        *,
        timeout: float = 8.0,
        recording_start: datetime | None = None,
        recording_end: datetime | None = None,
    ) -> Gb28181LiveSession:
        registration = self.registrations.get(device_id)
        if registration is None or registration.expires_at <= datetime.now(UTC):
            raise RuntimeError("GB/T 28181 设备未注册或注册已过期")
        if channel_id not in registration.channels:
            raise KeyError(f"unknown GB/T 28181 channel: {channel_id}")
        loop = asyncio.get_running_loop()
        media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        media_sock.bind(("0.0.0.0", 0))
        media_port = int(media_sock.getsockname()[1])
        media_sock.close()
        media_sock = None
        call_id = f"{secrets.token_hex(12)}@{self.domain}"
        branch = "z9hG4bK" + secrets.token_hex(10)
        via = f"SIP/2.0/UDP {_local_signaling_host(registration.address)}:{self.port};branch={branch};rport"
        from_header = f"<sip:{self.platform_id}@{self.domain}>;tag={secrets.token_hex(6)}"
        to_header = f"<sip:{channel_id}@{self.domain}>"
        sdp = (
            "v=0\r\n"
            f"o={self.platform_id} 0 0 IN IP4 {_local_signaling_host(registration.address)}\r\n"
            f"s={'Playback' if recording_start else 'FactoryGuard'}\r\n"
            f"c=IN IP4 {_local_signaling_host(registration.address)}\r\n"
            f"t={int(recording_start.timestamp()) if recording_start else 0} {int(recording_end.timestamp()) if recording_end else 0}\r\n"
            f"m=video {media_port} TCP/RTP/AVP 96 97 98\r\n"
            "a=recvonly\r\n"
            "a=rtpmap:96 PS/90000\r\n"
            "a=rtpmap:97 MPEG4/90000\r\n"
            "a=rtpmap:98 H264/90000\r\n"
        ).encode("utf-8")
        request = SipMessage.request(
            "INVITE",
            f"sip:{channel_id}@{registration.address[0]}:{registration.address[1]}",
            from_header=from_header,
            to_header=to_header,
            call_id=call_id,
            cseq=1,
            via=via,
            content_type="Application/SDP",
            body=sdp,
            extra_headers=(("Contact", f"<sip:{self.platform_id}@{_local_signaling_host(registration.address)}:{self.port}>"), ("Subject", f"{channel_id}:{0},{self.platform_id}:{0}")),
        )
        response = await self._request(request, registration.address, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"GB/T 28181 INVITE failed: {response.status_code} {response.reason}")
        remote_host, remote_port = _sdp_media(response.text)
        if remote_port == 0:
            remote_host = registration.address[0]
        session = Gb28181LiveSession(
            channel_id=channel_id,
            call_id=call_id,
            local_media_port=media_port,
            remote_media_host=remote_host,
            remote_media_port=remote_port or media_port,
            rtp_socket=media_sock,
        )
        self._dialogs[call_id] = session
        self._send(
            SipMessage.request(
                "ACK",
                request.request_uri,
                from_header=from_header,
                to_header=_with_to_tag(to_header, response.tag),
                call_id=call_id,
                cseq=1,
                via=via,
            ),
            registration.address,
        )
        return session

    async def bye(self, session: Gb28181LiveSession) -> None:
        self._dialogs.pop(session.call_id, None)
        registration = next((item for item in self.registrations.values() if item.channels.get(session.channel_id)), None)
        if registration is not None:
            self._send(
                SipMessage.request(
                    "BYE",
                    f"sip:{session.channel_id}@{registration.address[0]}:{registration.address[1]}",
                    from_header=f"<sip:{self.platform_id}@{self.domain}>;tag={secrets.token_hex(6)}",
                    to_header=f"<sip:{session.channel_id}@{self.domain}>",
                    call_id=session.call_id,
                    cseq=2,
                    via=f"SIP/2.0/UDP {_local_signaling_host(registration.address)}:{self.port};branch=z9hG4bK{secrets.token_hex(10)};rport",
                ),
                registration.address,
            )
        session.close()

    async def _request(self, request: SipMessage, address: tuple[str, int], *, timeout: float) -> SipMessage:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[SipMessage] = loop.create_future()
        key = (request.call_id, request.branch)
        self._pending[key] = future
        self._send(request, address)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(key, None)

    def _complete_response(self, message: SipMessage) -> None:
        key = (_call_id_from_cseq_or_headers(message), message.branch)
        future = self._pending.get(key)
        if future is not None and not future.done():
            future.set_result(message)

    def _send(self, message: SipMessage, address: tuple[str, int]) -> None:
        if self._transport is None:
            raise RuntimeError("GB/T 28181 SIP endpoint is not started")
        self._transport.sendto(message.encode(), address)
        if message.is_request:
            self._requests_sent += 1

    def _record_error(self, detail: str) -> None:
        safe = re.sub(r"(password|authorization)=([^,\s]+)", r"\1=***", detail, flags=re.IGNORECASE)
        self.last_errors.append(safe[-1000:])

    async def close(self) -> None:
        for session in tuple(self._dialogs.values()):
            session.close()
        self._dialogs.clear()
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()


class Gb28181Adapter(DeviceAdapter):
    protocol = DeviceProtocol.GB28181

    def __init__(
        self,
        device: VideoDevice,
        credentials: DeviceCredentials | None = None,
        *,
        platform: Gb28181Platform,
    ) -> None:
        super().__init__(device, credentials)
        self.platform = platform
        self._channels = ()
        self._sessions: dict[str, Gb28181LiveSession] = {}
        self._recordings: dict[str, Recording] = {}

    def probe(self, *, timeout: float = 5.0) -> ProbeResult:
        require_open(self)
        registered = self.device.device_id in self.platform.registrations
        if not registered:
            return ProbeResult(ok=False, detail="等待 GB/T 28181 设备注册", capabilities=self.get_capabilities())
        return ProbeResult(ok=True, detail="设备已注册", capabilities=self.get_capabilities(), diagnostics=self.platform.diagnostics)

    def get_device_info(self) -> DeviceInfo:
        registration = self.platform.registrations.get(self.device.device_id)
        channels = tuple(registration.channels.values()) if registration else ()
        return DeviceInfo(
            manufacturer=channels[0].manufacturer if channels else "",
            model=channels[0].model if channels else "",
            serial_number=self.device.device_id,
        )

    def get_capabilities(self) -> DeviceCapabilities:
        channels = self.platform.list_channels(self.device.device_id)
        return DeviceCapabilities(
            live_video=bool(channels),
            native_events=True,
            recording_search=True,
            profiles=("GB/T 28181-2022",),
            event_types=("alarm", "videolosssignal", "tamperdetection", "motiondetect", "fielddetection"),
            media_profiles=tuple(channel.id for channel in channels),
        )

    def list_channels(self) -> tuple[DeviceChannel, ...]:
        channels = self.platform.list_channels(self.device.device_id)
        self._channels = tuple(DeviceChannel(id=item.id, name=item.name, enabled=item.status.upper() == "ON") for item in channels)
        return self._channels

    def get_live_stream(self, channel_id: str, *, role: str = "sub") -> LiveStream:
        require_open(self)
        session = self._run_coroutine(self.platform.invite(self.device.device_id, channel_id))
        self._sessions[channel_id] = session
        return LiveStream(uri=session.ffmpeg_uri, profile_id=channel_id, transport="rtp")

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
        recordings = self._run_coroutine(
            self.platform.query_recordings(
                self.device.device_id,
                channel_id,
                start_at=start_at,
                end_at=end_at,
            )
        )
        self._recordings.update({item.id: item for item in recordings})
        return recordings

    def get_recording_uri(self, recording_id: str) -> LiveStream:
        require_open(self)
        recording = self._recordings.get(recording_id)
        if recording is None:
            raise KeyError(f"unknown GB/T 28181 recording: {recording_id}")
        session = self._run_coroutine(
            self.platform.invite(
                self.device.device_id,
                recording.channel_id,
                recording_start=recording.start_at,
                recording_end=recording.end_at,
            )
        )
        self._sessions[recording_id] = session
        return LiveStream(uri=session.ffmpeg_uri, profile_id=recording_id, transport="rtp")

    def subscribe_events(self, channel_id: str | None = None) -> EventSubscription:
        require_open(self)

        def events() -> Any:
            while not self.closed:
                try:
                    event = self.platform.native_events.get(timeout=1.0)
                except queue.Empty:
                    continue
                if channel_id is None or event.channel_id == channel_id:
                    yield event

        return EventSubscription(source="gb28181", iterator=events())

    def _run_coroutine(self, coroutine: Any) -> Any:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        loop = self.platform.loop
        if current is not None and loop is current:
            return current.run_until_complete(coroutine)
        if loop is None:
            coroutine.close()
            raise RuntimeError("GB/T 28181 设备服务尚未启动")
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=15)

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
        self.closed = True


def _sip_user(value: str) -> str:
    match = re.search(r"sip:([^@;>\s]+)", value, re.IGNORECASE)
    return match.group(1) if match else ""


def _expires(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 3600


def _future(seconds: int) -> datetime:
    from datetime import timedelta

    return datetime.now(UTC) + timedelta(seconds=seconds)


def _local_signaling_host(address: tuple[str, int]) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(address)
        return str(probe.getsockname()[0])
    except OSError:
        return "0.0.0.0"
    finally:
        probe.close()


def _call_id_from_cseq_or_headers(message: SipMessage) -> str:
    return message.call_id


def _with_to_tag(value: str, tag: str) -> str:
    return value if "tag=" in value.lower() else f"{value};tag={tag}"


def _xml_text(node: ET.Element, name: str, default: str = "") -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] == name and child.text:
            return child.text.strip()
    return default


def _xml_int(node: ET.Element, name: str, default: int = 0) -> int:
    try:
        return int(_xml_text(node, name, str(default)))
    except ValueError:
        return default


def _xml_items(node: ET.Element, name: str) -> tuple[ET.Element, ...]:
    return tuple(child for child in node.iter() if child.tag.rsplit("}", 1)[-1] == name)


def _gb_time(value: str) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _normalize_gb_event(command: str, method: str) -> str:
    normalized = method.strip().lower()
    if normalized in {"1", "signal"}:
        return "alarm"
    if normalized in {"2", "motion"}:
        return "motiondetect"
    return command.strip().lower() or "alarm"


def _sdp_media(body: str) -> tuple[str, int]:
    host = ""
    port = 0
    for line in body.splitlines():
        if line.startswith("c="):
            parts = line.split()
            if len(parts) >= 3:
                host = parts[2]
        elif line.startswith("m=video"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                port = int(parts[1])
    return host, port


__all__ = [
    "Gb28181Adapter",
    "Gb28181Channel",
    "Gb28181LiveSession",
    "Gb28181Platform",
    "Gb28181Registration",
]
