from __future__ import annotations

"""Uniform interfaces for RTSP, ONVIF and GB/T 28181 video devices."""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from factoryguard.domain.models import (
    AnalysisPolicy,
    DeviceCapabilities,
    DeviceProtocol,
    NativeEvent,
    VideoDevice,
)


@dataclass(frozen=True, slots=True)
class DeviceCredentials:
    """Credential payload kept outside configuration exports."""

    username: str = ""
    password: str = ""
    token: str = ""

    def __post_init__(self) -> None:
        if any("\n" in str(value) or "\r" in str(value) for value in (self.username, self.password, self.token)):
            raise ValueError("credentials must be single-line values")

    @property
    def is_empty(self) -> bool:
        return not any((self.username, self.password, self.token))

    def with_password_in_url(self, url: str) -> str:
        if not self.password or "@" in url.split("://", 1)[-1]:
            return url
        scheme, rest = url.split("://", 1)
        return f"{scheme}://{self.username}:{self.password}@{rest}"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    manufacturer: str = ""
    model: str = ""
    firmware: str = ""
    serial_number: str = ""
    hardware_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "manufacturer": self.manufacturer,
            "model": self.model,
            "firmware": self.firmware,
            "serial_number": self.serial_number,
            "hardware_id": self.hardware_id,
        }


@dataclass(frozen=True, slots=True)
class DeviceChannel:
    id: str
    name: str
    profile_token: str = ""
    source_token: str = ""
    encoding: str = ""
    width: int = 0
    height: int = 0
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class LiveStream:
    uri: str
    profile_id: str = ""
    transport: str = "rtsp"
    width: int = 0
    height: int = 0

    def with_credentials(self, credentials: DeviceCredentials) -> "LiveStream":
        return LiveStream(
            uri=credentials.with_password_in_url(self.uri),
            profile_id=self.profile_id,
            transport=self.transport,
            width=self.width,
            height=self.height,
        )


@dataclass(frozen=True, slots=True)
class Recording:
    id: str
    channel_id: str
    start_at: datetime
    end_at: datetime
    source: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    detail: str
    latency_ms: float | None = None
    info: DeviceInfo | None = None
    capabilities: DeviceCapabilities = DeviceCapabilities()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventSubscription:
    """Native event stream whose protocol resources can be released."""

    source: str
    iterator: Iterator[NativeEvent]

    def close(self) -> None:
        close = getattr(self.iterator, "close", None)
        if callable(close):
            close()


class DeviceAdapter(ABC):
    """Protocol adapter. Business rules and storage stay outside this layer."""

    protocol: DeviceProtocol

    def __init__(self, device: VideoDevice, credentials: DeviceCredentials | None = None) -> None:
        self.device = device
        self.credentials = credentials or DeviceCredentials()
        self.closed = False

    @abstractmethod
    def probe(self, *, timeout: float = 5.0) -> ProbeResult:
        raise NotImplementedError

    @abstractmethod
    def get_device_info(self) -> DeviceInfo:
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self) -> DeviceCapabilities:
        raise NotImplementedError

    @abstractmethod
    def list_channels(self) -> tuple[DeviceChannel, ...]:
        raise NotImplementedError

    @abstractmethod
    def get_live_stream(self, channel_id: str, *, role: str = "sub") -> LiveStream:
        raise NotImplementedError

    @abstractmethod
    def search_recordings(
        self,
        channel_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[Recording, ...]:
        raise NotImplementedError

    @abstractmethod
    def get_recording_uri(self, recording_id: str) -> LiveStream:
        raise NotImplementedError

    @abstractmethod
    def subscribe_events(self, channel_id: str | None = None) -> EventSubscription:
        raise NotImplementedError

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "DeviceAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def require_open(adapter: DeviceAdapter) -> None:
    if adapter.closed:
        raise RuntimeError(f"{adapter.protocol.value} adapter is closed")


__all__ = [
    "DeviceAdapter",
    "DeviceCapabilities",
    "DeviceChannel",
    "DeviceCredentials",
    "DeviceInfo",
    "DeviceProtocol",
    "EventSubscription",
    "LiveStream",
    "ProbeResult",
    "Recording",
    "AnalysisPolicy",
    "NativeEvent",
    "require_open",
]
