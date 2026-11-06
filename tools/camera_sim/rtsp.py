"""Minimal RTSP server backed by FFmpeg-generated MPEG-TS/RTP streams."""

from __future__ import annotations

import os
import socket
import socketserver
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from factoryguard.media.ffmpeg import FFmpegInstallation


def _publisher_creation_flags() -> int:
    """Return the creation flags used for the FFmpeg publisher."""
    if os.name != "nt":
        return 0
    return int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def _kill_process_tree(process: object) -> None:
    """Terminate the publisher and every process it started."""
    if os.name != "nt":
        try:
            process.terminate()
        except (OSError, ValueError):
            pass
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        try:
            process.terminate()
        except (OSError, ValueError):
            pass


@dataclass(slots=True)
class RtspClient:
    connection: socket.socket
    peer: tuple[str, int]
    transport: str = "tcp"
    client_rtp_port: int = 0
    interleaved_channel: int = 0
    active: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def send_rtp(self, packet: bytes) -> None:
        if not self.active:
            return
        try:
            with self.lock:
                if self.transport == "tcp":
                    # RFC 2326 requires an interleaved frame to carry an
                    # even number of bytes.  An odd length packet would
                    # shift every following frame and smear the picture.
                    body = packet + (b"\x00" if len(packet) % 2 else b"")
                    header = bytes((36, self.interleaved_channel)) + len(body).to_bytes(2, "big")
                    self.connection.sendall(header + body)
                else:
                    self.connection.sendto(packet, (self.peer[0], self.client_rtp_port))
        except OSError:
            self.active = False


class RtpRelay(threading.Thread):
    """Receive FFmpeg RTP output and fan it out to RTSP clients."""

    def __init__(self, ffmpeg: FFmpegInstallation, source: str) -> None:
        super().__init__(name="camera-sim-rtp-relay", daemon=True)
        self.ffmpeg = ffmpeg
        self.source = source
        self._stop = threading.Event()
        self._clients: list[RtspClient] = []
        self._clients_lock = threading.Lock()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.settimeout(0.5)
        # High definition footage produces a lot of RTP packets.  A small
        # receive buffer makes the kernel drop packets, which shows up as
        # a vertically smeared picture instead of a clean frame.
        try:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        except OSError:
            pass
        self.port = int(self._socket.getsockname()[1])
        self._publisher: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        # A source is either a lavfi graph or a real media file such as a
        # recorded surveillance clip.  Files need no lavfi demuxer and are
        # looped so the stream keeps running for long sessions.
        is_media_file = Path(self.source).is_file()
        command = [
            str(self.ffmpeg.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
        ]
        if is_media_file:
            command += ["-stream_loop", "-1"]
        else:
            command += ["-f", "lavfi"]
        command += [
            "-i",
            self.source,
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
            f"rtp://127.0.0.1:{self.port}",
        ]
        self._publisher = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_publisher_creation_flags(),
        )
        super().start()

    def add_client(self, client: RtspClient) -> None:
        with self._clients_lock:
            if client not in self._clients:
                self._clients.append(client)

    def remove_client(self, client: RtspClient) -> None:
        client.active = False
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)

    def stop(self) -> None:
        self._stop.set()
        if self._publisher is not None and self._publisher.poll() is None:
            # FFmpeg can start helper processes.  Stopping the whole tree keeps
            # a long verification session from accumulating stray encoders.
            _kill_process_tree(self._publisher)
            try:
                self._publisher.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._publisher.kill()
                try:
                    self._publisher.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        self._socket.close()
        self.join(timeout=2.0)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                packet, _address = self._socket.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            with self._clients_lock:
                clients = tuple(self._clients)
            for client in clients:
                client.send_rtp(packet)


class _RtspRequestReader:
    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.buffer = b""

    def next_request(self) -> tuple[str, dict[str, str], bytes] | None:
        while True:
            while self.buffer.startswith(b"$"):
                if len(self.buffer) < 4:
                    self._receive()
                    continue
                size = int.from_bytes(self.buffer[2:4], "big")
                if len(self.buffer) < 4 + size:
                    self._receive()
                    continue
                self.buffer = self.buffer[4 + size :]
            header_end = self.buffer.find(b"\r\n\r\n")
            if header_end < 0:
                self._receive()
                continue
            header_blob = self.buffer[:header_end]
            lines = header_blob.decode("utf-8", errors="replace").split("\r\n")
            request_line = lines[0] if lines else ""
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
            content_length = _safe_int(headers.get("content-length", "0"))
            if len(self.buffer) < header_end + 4 + content_length:
                self._receive()
                continue
            body = self.buffer[header_end + 4 : header_end + 4 + content_length]
            self.buffer = self.buffer[header_end + 4 + content_length :]
            return request_line, headers, body

    def _receive(self) -> None:
        chunk = self.connection.recv(8192)
        if not chunk:
            raise ConnectionError("RTSP client disconnected")
        self.buffer += chunk


class _RtspHandler(socketserver.BaseRequestHandler):
    server: RtspTcpServer

    def handle(self) -> None:
        path = ""
        client: RtspClient | None = None
        relay: RtpRelay | None = None
        session = uuid4().hex
        reader = _RtspRequestReader(self.request)
        try:
            while True:
                parsed = reader.next_request()
                if parsed is None:
                    break
                request_line, headers, _body = parsed
                parts = request_line.split(" ")
                if len(parts) < 3:
                    continue
                method, url, _version = parts[:3]
                cseq = headers.get("cseq", "0")
                if method == "OPTIONS":
                    self._respond(cseq, public="OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN")
                    continue
                if method == "DESCRIBE":
                    path = _url_path(url)
                    relay = self.server.relays.get(path)
                    if relay is None:
                        self._respond(cseq, status="404 Not Found")
                        continue
                    self._respond(
                        cseq,
                        content_type="application/sdp",
                        body=_sdp(self.request.getsockname()[0], path),
                    )
                    continue
                if method == "SETUP":
                    path = _url_path(url)
                    relay = self.server.relays.get(path)
                    if relay is None:
                        self._respond(cseq, status="404 Not Found")
                        continue
                    transport = headers.get("transport", "")
                    client = self._setup_client(transport, session)
                    extra = (
                        f"RTP/AVP/TCP;unicast;interleaved={client.interleaved_channel}-{client.interleaved_channel + 1}"
                        if client.transport == "tcp"
                        else f"RTP/AVP;unicast;client_port={client.client_rtp_port}-{client.client_rtp_port + 1};server_port={relay.port}-{relay.port + 1}"
                    )
                    self._respond(cseq, transport=extra, session=session)
                    continue
                if method == "PLAY":
                    if relay is not None and client is not None:
                        client.active = True
                        relay.add_client(client)
                    self._respond(cseq, session=session, rtp_info="url=/trackID=0")
                    continue
                if method == "TEARDOWN":
                    if client is not None:
                        client.active = False
                    self._respond(cseq, session=session)
                    return
                self._respond(cseq, status="405 Method Not Allowed")
        except (ConnectionError, OSError):
            pass
        finally:
            if relay is not None and client is not None:
                relay.remove_client(client)

    def _setup_client(self, transport: str, session: str) -> RtspClient:
        peer = self.client_address
        if "RTP/AVP/TCP" in transport.upper() or "interleaved=" in transport.lower():
            channel = 0
            for token in transport.split(";"):
                if token.strip().lower().startswith("interleaved="):
                    channel = _safe_int(token.split("=", 1)[1].split("-", 1)[0])
            return RtspClient(self.request, peer, transport="tcp", interleaved_channel=channel)
        client_port = 0
        for token in transport.split(";"):
            if token.strip().lower().startswith("client_port="):
                client_port = _safe_int(token.split("=", 1)[1].split("-", 1)[0])
        return RtspClient(self.request, peer, transport="udp", client_rtp_port=client_port)

    def _respond(
        self,
        cseq: str,
        *,
        status: str = "200 OK",
        content_type: str = "",
        body: str = "",
        public: str = "",
        transport: str = "",
        session: str = "",
        rtp_info: str = "",
    ) -> None:
        payload = body.encode("utf-8")
        lines = [f"RTSP/1.0 {status}", f"CSeq: {cseq}"]
        if public:
            lines.append(f"Public: {public}")
        if content_type:
            lines.append(f"Content-Type: {content_type}")
        if transport:
            lines.append(f"Transport: {transport}")
        if session:
            lines.append(f"Session: {session};timeout=60")
        if rtp_info:
            lines.append(f"RTP-Info: {rtp_info}")
        lines.extend((f"Content-Length: {len(payload)}", "", ""))
        self.request.sendall("\r\n".join(lines).encode("utf-8") + payload)


class RtspTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], relays: dict[str, RtpRelay]) -> None:
        self.relays = relays
        super().__init__(address, _RtspHandler)


@dataclass(slots=True)
class RtspCameraSpec:
    camera_id: str
    name: str
    source: str


class RtspCameraServer:
    """One local RTSP server exposing multiple synthetic camera paths."""

    def __init__(
        self, ffmpeg: FFmpegInstallation, *, host: str = "127.0.0.1", port: int = 8554
    ) -> None:
        self.ffmpeg = ffmpeg
        self.host = host
        self.port = port
        self.relays: dict[str, RtpRelay] = {}
        self._server: RtspTcpServer | None = None
        self._thread: threading.Thread | None = None

    def add_camera(self, camera: RtspCameraSpec) -> str:
        path = f"/{camera.camera_id}"
        relay = RtpRelay(self.ffmpeg, camera.source)
        self.relays[path] = relay
        return self.stream_url(camera.camera_id)

    def start(self) -> None:
        if not self.relays:
            raise RuntimeError("RTSP simulator has no cameras")
        for relay in self.relays.values():
            relay.start()
        self._server = RtspTcpServer((self.host, self.port), self.relays)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="camera-sim-rtsp", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        for relay in self.relays.values():
            relay.stop()

    def stream_url(self, camera_id: str) -> str:
        return f"rtsp://{self.host}:{self.port}/{camera_id}"


def _url_path(value: str) -> str:
    if "://" not in value:
        path = value if value.startswith("/") else f"/{value}"
        return "/" + path.strip("/").split("/", 1)[0]
    remainder = value.split("://", 1)[1]
    slash = remainder.find("/")
    path = remainder[slash:] if slash >= 0 else "/"
    return "/" + path.strip("/").split("/", 1)[0]


def _sdp(host: str, path: str) -> str:
    return "\r\n".join(
        (
            "v=0",
            "o=- 0 0 IN IP4 127.0.0.1",
            "s=FactoryGuard Camera Simulator",
            f"c=IN IP4 {host}",
            "t=0 0",
            "a=tool:FactoryGuard",
            "m=video 0 RTP/AVP 33",
            "a=rtpmap:33 MP2T/90000",
            f"a=control:{path}",
            "",
        )
    )


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


__all__ = ["RtpRelay", "RtspCameraServer", "RtspCameraSpec", "RtspClient"]
