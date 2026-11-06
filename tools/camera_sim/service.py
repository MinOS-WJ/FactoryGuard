"""Lifecycle manager for a local multi-camera simulation lab."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from factoryguard.media.ffmpeg import FFmpegInstallation, require_ffmpeg

from .gb28181 import Gb28181DeviceClient, Gb28181DeviceSpec
from .onvif import OnvifCameraServer, OnvifCameraSpec
from .rtsp import RtspCameraServer, RtspCameraSpec

SUPPORTED_PROTOCOLS = ("onvif", "gb28181")


@dataclass(slots=True)
class SimulatedCamera:
    index: int
    camera_id: str
    name: str
    protocol: str
    device_id: str = ""
    channel_id: str = ""
    onvif_endpoint: str = ""
    rtsp_url: str = ""
    gb_registered: bool = False
    last_error: str = ""
    source: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "camera_id": self.camera_id,
            "name": self.name,
            "protocol": self.protocol,
            "device_id": self.device_id,
            "channel_id": self.channel_id,
            "onvif_endpoint": self.onvif_endpoint,
            "rtsp_url": self.rtsp_url,
            "gb_registered": self.gb_registered,
            "last_error": self.last_error,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


class CameraSimulator:
    """Start RTSP, ONVIF and GB/T 28181 virtual cameras as one test lab."""

    def __init__(
        self,
        *,
        count: int = 4,
        protocols: Iterable[str] = SUPPORTED_PROTOCOLS,
        rtsp_host: str = "127.0.0.1",
        rtsp_port: int = 8554,
        onvif_host: str = "127.0.0.1",
        onvif_port: int = 8000,
        platform_host: str = "127.0.0.1",
        platform_port: int = 5060,
        platform_id: str = "340200000000000000",
        domain: str = "3402000000",
        password: str = "",
        ffmpeg: FFmpegInstallation | None = None,
        media_sources: Iterable[str] = (),
    ) -> None:
        if count < 1:
            raise ValueError("simulator count must be positive")
        normalized = tuple(
            dict.fromkeys(str(value).strip().lower() for value in protocols if str(value).strip())
        )
        if not normalized or any(value not in SUPPORTED_PROTOCOLS for value in normalized):
            raise ValueError("protocols must contain onvif and/or gb28181")
        self.count = count
        self.protocols = normalized
        self.rtsp_host = rtsp_host
        self.rtsp_port = rtsp_port
        self.onvif_host = onvif_host
        self.onvif_port = onvif_port
        self.platform_host = platform_host
        self.platform_port = platform_port
        self.platform_id = platform_id
        self.domain = domain
        self.password = password
        self.ffmpeg = ffmpeg or require_ffmpeg()
        self.media_sources = tuple(str(item).strip() for item in media_sources if str(item).strip())
        self.cameras = self._build_cameras()
        self._rtsp_server: RtspCameraServer | None = None
        self._onvif_server: OnvifCameraServer | None = None
        self._gb_clients: list[Gb28181DeviceClient] = []
        self.started = False

    def _build_cameras(self) -> list[SimulatedCamera]:
        cameras: list[SimulatedCamera] = []
        for index in range(1, self.count + 1):
            protocol = self.protocols[(index - 1) % len(self.protocols)]
            camera_id = f"sim-cam-{index:02d}"
            name = f"模拟摄像头 {index:02d}"
            # Real recorded clips let the platform be exercised with the
            # footage a customer actually has on hand.
            if self.media_sources:
                source = self.media_sources[(index - 1) % len(self.media_sources)]
            else:
                source = f"testsrc2=size=640x360:rate=12,hue=h={(index * 37) % 360}"
            gb_device = f"3402000000132{index:07d}"
            gb_channel = f"3402000000131{index:07d}"
            cameras.append(
                SimulatedCamera(
                    index=index,
                    camera_id=camera_id,
                    name=name,
                    protocol=protocol,
                    device_id=gb_device if protocol == "gb28181" else camera_id,
                    channel_id=gb_channel if protocol == "gb28181" else camera_id,
                    source=source,
                )
            )
        return cameras

    def start(self) -> None:
        if self.started:
            return
        try:
            self._rtsp_server = RtspCameraServer(
                self.ffmpeg, host=self.rtsp_host, port=self.rtsp_port
            )
            for camera in self.cameras:
                self._rtsp_server.add_camera(
                    RtspCameraSpec(
                        camera_id=camera.camera_id, name=camera.name, source=camera.source
                    )
                )
            self._rtsp_server.start()
            self.rtsp_port = self._rtsp_server.port
            for camera in self.cameras:
                camera.rtsp_url = self._rtsp_server.stream_url(camera.camera_id)

            onvif_cameras = [camera for camera in self.cameras if camera.protocol == "onvif"]
            if onvif_cameras:
                specs = [
                    OnvifCameraSpec(
                        camera_id=camera.camera_id,
                        name=camera.name,
                        stream_uri=camera.rtsp_url,
                    )
                    for camera in onvif_cameras
                ]
                self._onvif_server = OnvifCameraServer(
                    specs, host=self.onvif_host, port=self.onvif_port
                )
                self._onvif_server.start()
                self.onvif_port = (
                    int(self._onvif_server._server.server_address[1])
                    if self._onvif_server._server
                    else self.onvif_port
                )
                for camera in onvif_cameras:
                    camera.onvif_endpoint = self._onvif_server.endpoint(camera.camera_id)

            for camera in self.cameras:
                if camera.protocol != "gb28181":
                    continue
                spec = Gb28181DeviceSpec(
                    device_id=camera.device_id,
                    channel_id=camera.channel_id,
                    name=camera.name,
                    platform_host=self.platform_host,
                    platform_port=self.platform_port,
                    platform_id=self.platform_id,
                    domain=self.domain,
                    password=self.password,
                    source=camera.source,
                )
                client = Gb28181DeviceClient(spec, self.ffmpeg)
                self._gb_clients.append(client)
                client.start()
            self.started = True
        except Exception:
            self.stop()
            raise

    def wait_until_ready(self, *, timeout: float = 8.0) -> bool:
        if not self.started:
            return False
        deadline = time.monotonic() + timeout
        gb_cameras = [camera for camera in self.cameras if camera.protocol == "gb28181"]
        while time.monotonic() < deadline:
            self.refresh_status()
            if all(camera.gb_registered for camera in gb_cameras):
                return True
            time.sleep(0.1)
        self.refresh_status()
        return all(camera.gb_registered for camera in gb_cameras)

    def refresh_status(self) -> None:
        for camera, client in zip(self._gb_cameras_for_cameras(), self._gb_clients, strict=False):
            camera.gb_registered = client.registered
            camera.last_error = client.last_error

    def _gb_cameras_for_cameras(self) -> list[SimulatedCamera]:
        return [camera for camera in self.cameras if camera.protocol == "gb28181"]

    def send_alarm(self, *, description: str = "Simulated intrusion alarm") -> int:
        sent = 0
        for camera, client in zip(self._gb_cameras_for_cameras(), self._gb_clients, strict=False):
            if not client.registered:
                continue
            client.send_alarm(description=description)
            sent += 1
            camera.metadata["last_alarm_at"] = time.time()
        if self._onvif_server is not None:
            for camera in self.cameras:
                if camera.protocol != "onvif":
                    continue
                self._onvif_server.push_event(camera.camera_id)
                sent += 1
                camera.metadata["last_alarm_at"] = time.time()
        return sent

    def stop(self) -> None:
        for client in self._gb_clients:
            client.stop()
        self._gb_clients.clear()
        if self._onvif_server is not None:
            self._onvif_server.stop()
            self._onvif_server = None
        if self._rtsp_server is not None:
            self._rtsp_server.stop()
            self._rtsp_server = None
        self.started = False

    def snapshot(self) -> list[dict[str, object]]:
        self.refresh_status()
        return [camera.as_dict() for camera in self.cameras]

    def __enter__(self) -> CameraSimulator:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


__all__ = ["SUPPORTED_PROTOCOLS", "CameraSimulator", "SimulatedCamera"]
