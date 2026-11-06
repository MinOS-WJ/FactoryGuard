"""GB/T 28181 camera-side simulator for registration and live playback tests."""

from __future__ import annotations

import secrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from factoryguard.devices.gb28181.sip import DigestChallenge, SipMessage, digest_response
from factoryguard.media.ffmpeg import FFmpegInstallation


@dataclass(frozen=True, slots=True)
class Gb28181DeviceSpec:
    device_id: str
    channel_id: str
    name: str
    platform_host: str = "127.0.0.1"
    platform_port: int = 5060
    platform_id: str = "340200000000000000"
    domain: str = "3402000000"
    password: str = ""
    manufacturer: str = "FactoryGuard Simulator"
    model: str = "Virtual GB/T 28181 Camera"
    source: str = "testsrc2=size=640x360:rate=12"


class Gb28181DeviceClient(threading.Thread):
    """One GB/T 28181 device registering and streaming to a platform."""

    def __init__(self, spec: Gb28181DeviceSpec, ffmpeg: FFmpegInstallation) -> None:
        super().__init__(name=f"camera-sim-gb-{spec.device_id}", daemon=True)
        self.spec = spec
        self.ffmpeg = ffmpeg
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("", 0))
        self._socket.connect((spec.platform_host, spec.platform_port))
        self._socket.settimeout(0.5)
        self.local_address = self._socket.getsockname()
        self.call_id = f"{uuid4().hex}@{spec.domain}"
        self.cseq = 0
        self._from_tag = secrets.token_hex(6)
        self._stream_process: subprocess.Popen[bytes] | None = None
        self.registered = False
        self.last_error = ""

    @property
    def from_header(self) -> str:
        return f"<sip:{self.spec.device_id}@{self.spec.domain}>;tag={self._from_tag}"

    @property
    def to_header(self) -> str:
        return f"<sip:{self.spec.device_id}@{self.spec.domain}>"

    @property
    def contact(self) -> str:
        return f"<sip:{self.spec.device_id}@{self.local_address[0]}:{self.local_address[1]}>"

    def run(self) -> None:
        try:
            self._register()
            if not self.registered:
                return
            self._send_catalog()
            last_keepalive = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - last_keepalive >= 30.0:
                    self._send_control("Keepalive", "<Status>OK</Status>")
                    last_keepalive = time.monotonic()
                try:
                    data = self._socket.recv(65535)
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    message = SipMessage.parse(data)
                except ValueError:
                    continue
                if message.is_request and message.method == "INVITE":
                    self._handle_invite(message)
                elif message.is_request and message.method == "BYE":
                    self._stop_stream()
                    self._send_response(message, 200, "OK")
        finally:
            self._stop_stream()
            self._socket.close()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self.registered:
            try:
                self._send_register(expires=0)
            except OSError:
                pass
        self.join(timeout=3.0)

    def send_alarm(self, *, description: str = "Simulated intrusion alarm") -> None:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = (
            "<Alarm><CmdType>Alarm</CmdType>"
            f"<SN>{int(time.time())}</SN><DeviceID>{self.spec.channel_id}</DeviceID>"
            f"<AlarmPriority>1</AlarmPriority><AlarmMethod>5</AlarmMethod><AlarmTime>{timestamp}</AlarmTime>"
            f"<AlarmDescription>{_xml_escape(description)}</AlarmDescription></Alarm>"
        )
        self._send_message(body, content_type="Application/MANSCDP+xml")

    def _register(self) -> None:
        first = self._send_register(expires=3600)
        if first is None:
            self.last_error = "registration timeout"
            return
        if first.status_code == 401:
            challenge_value = first.get("www-authenticate")
            challenge = DigestChallenge.parse(challenge_value)
            authorization = digest_response(
                username=self.spec.device_id,
                password=self.spec.password,
                realm=challenge.realm,
                nonce=challenge.nonce,
                method="REGISTER",
                uri=f"sip:{self.spec.domain}",
                qop=challenge.qop,
                algorithm=challenge.algorithm,
            )
            first = self._send_register(expires=3600, authorization=authorization)
        if first is not None and first.status_code == 200:
            self.registered = True
            return
        if first is not None:
            self.last_error = f"registration failed: {first.status_code} {first.reason}"

    def _send_register(self, *, expires: int, authorization: str = "") -> SipMessage | None:
        self.cseq += 1
        headers: list[tuple[str, str]] = [
            ("Contact", self.contact),
            ("Expires", str(expires)),
            ("User-Agent", "FactoryGuard-Camera-Simulator"),
        ]
        if authorization:
            headers.append(("Authorization", authorization))
        request = SipMessage.request(
            "REGISTER",
            f"sip:{self.spec.domain}",
            from_header=self.from_header,
            to_header=self.to_header,
            call_id=self.call_id,
            cseq=self.cseq,
            via=self._via(),
            extra_headers=tuple(headers),
        )
        self._socket.send(request.encode())
        return self._receive_response(timeout=4.0)

    def _send_catalog(self) -> None:
        body = (
            '<?xml version="1.0" encoding="GB2312"?>'
            "<Control><CmdType>Catalog</CmdType>"
            f"<SN>{int(time.time())}</SN><DeviceID>{self.spec.device_id}</DeviceID>"
            '<SumNum>1</SumNum><DeviceList Num="1"><Item>'
            f"<DeviceID>{self.spec.channel_id}</DeviceID><Name>{_xml_escape(self.spec.name)}</Name>"
            f"<Manufacturer>{_xml_escape(self.spec.manufacturer)}</Manufacturer>"
            f"<Model>{_xml_escape(self.spec.model)}</Model><Owner>FactoryGuard</Owner>"
            "<CivilCode>340200</CivilCode><Address>Simulated Address</Address>"
            "<Parental>0</Parental><ParentID>" + self.spec.device_id + "</ParentID>"
            "<SafetyWay>0</SafetyWay><RegisterWay>1</RegisterWay><Secrecy>0</Secrecy>"
            "<Status>ON</Status></Item></DeviceList></Control>"
        )
        self._send_message(body, content_type="Application/MANSCDP+xml")

    def _send_control(self, command: str, values: str) -> None:
        body = (
            "<Control>"
            f"<CmdType>{command}</CmdType><SN>{int(time.time())}</SN>"
            f"<DeviceID>{self.spec.device_id}</DeviceID>{values}</Control>"
        )
        self._send_message(body, content_type="Application/MANSCDP+xml")

    def _send_message(self, body: str, *, content_type: str) -> None:
        self.cseq += 1
        request = SipMessage.request(
            "MESSAGE",
            f"sip:{self.spec.platform_id}@{self.spec.platform_host}:{self.spec.platform_port}",
            from_header=self.from_header,
            to_header=f"<sip:{self.spec.platform_id}@{self.spec.domain}>",
            call_id=f"{uuid4().hex}@{self.spec.domain}",
            cseq=self.cseq,
            via=self._via(),
            content_type=content_type,
            body=body.encode(
                "gb2312" if content_type == "Application/MANSCDP+xml" else "utf-8", errors="replace"
            ),
            extra_headers=(
                ("Contact", self.contact),
                ("User-Agent", "FactoryGuard-Camera-Simulator"),
            ),
        )
        self._socket.send(request.encode())

    def _handle_invite(self, message: SipMessage) -> None:
        media_host, media_port = _sdp_media(message.text)
        if not media_port:
            self._send_response(message, 488, "Not Acceptable Here")
            return
        local_host = self.local_address[0]
        answer = (
            "v=0\r\n"
            f"o={self.spec.device_id} 0 0 IN IP4 {local_host}\r\n"
            "s=FactoryGuard Simulator\r\n"
            f"c=IN IP4 {local_host}\r\n"
            "t=0 0\r\n"
            f"m=video {int(media_port)} RTP/AVP 96\r\n"
            "a=sendonly\r\n"
            "a=rtpmap:96 PS/90000\r\n"
        ).encode()
        self._send_response(
            message,
            200,
            "OK",
            extra_headers=(
                ("Contact", self.contact),
                ("Content-Type", "Application/SDP"),
            ),
            body=answer,
        )
        self._start_stream(media_host or "127.0.0.1", media_port)

    def _send_response(
        self,
        request: SipMessage,
        status: int,
        reason: str,
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
        body: bytes = b"",
    ) -> None:
        response = request.response(status, reason, extra_headers=extra_headers, body=body)
        self._socket.send(response.encode())

    def _receive_response(self, *, timeout: float) -> SipMessage | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = self._socket.recv(65535)
            except TimeoutError:
                continue
            except OSError:
                return None
            try:
                message = SipMessage.parse(data)
            except ValueError:
                continue
            if not message.is_request:
                return message
        return None

    def _start_stream(self, host: str, port: int) -> None:
        self._stop_stream()
        command = [
            str(self.ffmpeg.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            self.spec.source,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-g",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rtp_mpegts",
            "-pkt_size",
            "1200",
            f"rtp://{host}:{port}",
        ]
        self._stream_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )

    def _stop_stream(self) -> None:
        if self._stream_process is None:
            return
        if self._stream_process.poll() is None:
            self._stream_process.terminate()
            try:
                self._stream_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._stream_process.kill()
                self._stream_process.wait(timeout=2.0)
        self._stream_process = None

    def _via(self) -> str:
        return (
            f"SIP/2.0/UDP {self.local_address[0]}:{self.local_address[1]};"
            f"branch=z9hG4bK{secrets.token_hex(8)};rport"
        )


def _sdp_media(value: str) -> tuple[str, int]:
    host = ""
    port = 0
    for line in value.replace("\r", "").split("\n"):
        if line.startswith("c=IN IP4 "):
            host = line.removeprefix("c=IN IP4 ").strip()
        elif line.startswith("m=video "):
            parts = line.split()
            if len(parts) >= 2:
                port = int(parts[1]) if parts[1].isdigit() else 0
    return host, port


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


__all__ = ["Gb28181DeviceClient", "Gb28181DeviceSpec"]
