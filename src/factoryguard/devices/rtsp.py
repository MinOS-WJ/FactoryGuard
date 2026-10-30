from __future__ import annotations

"""Manual RTSP/RTP compatibility adapter for cameras without ONVIF."""

from datetime import datetime

from factoryguard.domain.models import DeviceCapabilities, DeviceProtocol, VideoDevice
from factoryguard.media.ffmpeg import require_ffmpeg

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


class RtspAdapter(DeviceAdapter):
    protocol = DeviceProtocol.RTSP

    def __init__(self, device: VideoDevice, credentials: DeviceCredentials | None = None) -> None:
        super().__init__(device, credentials)
        self._stream_uri = credentials.with_password_in_url(device.endpoint) if credentials else device.endpoint

    def probe(self, *, timeout: float = 5.0) -> ProbeResult:
        require_open(self)
        try:
            ffmpeg = require_ffmpeg()
            width, height = ffmpeg.probe_resolution(self._stream_uri, timeout=timeout)
            return ProbeResult(
                ok=True,
                detail=f"RTSP 取流成功 · {width}×{height}",
                capabilities=self.get_capabilities(),
                diagnostics={"transport": "tcp", "width": width, "height": height},
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return ProbeResult(ok=False, detail=str(exc))

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(model="RTSP video source", serial_number=self.device.id)

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(live_video=True, profiles=("RTSP/RTP",))

    def list_channels(self) -> tuple[DeviceChannel, ...]:
        return (DeviceChannel(id=self.device.id, name=self.device.name),)

    def get_live_stream(self, channel_id: str, *, role: str = "sub") -> LiveStream:
        require_open(self)
        return LiveStream(uri=self._stream_uri, profile_id=channel_id, transport="rtsp")

    def search_recordings(
        self,
        channel_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[Recording, ...]:
        raise RuntimeError("手工 RTSP 设备未提供录像检索能力")

    def get_recording_uri(self, recording_id: str) -> LiveStream:
        raise RuntimeError("手工 RTSP 设备未提供录像回放能力")

    def subscribe_events(self, channel_id: str | None = None) -> EventSubscription:
        raise RuntimeError("手工 RTSP 设备未提供原生事件能力")


class ManualAdapter(RtspAdapter):
    protocol = DeviceProtocol.MANUAL


__all__ = ["ManualAdapter", "RtspAdapter"]
