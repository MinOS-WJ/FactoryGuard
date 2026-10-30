"""Standard video device adapters."""

from .base import DeviceAdapter, DeviceChannel, DeviceCredentials, DeviceInfo, LiveStream, ProbeResult, Recording
from .gb28181.server import Gb28181Adapter, Gb28181Platform
from .onvif import OnvifAdapter, OnvifDiscovery
from .rtsp import ManualAdapter, RtspAdapter

__all__ = [
    "DeviceAdapter",
    "DeviceChannel",
    "DeviceCredentials",
    "DeviceInfo",
    "Gb28181Adapter",
    "Gb28181Platform",
    "LiveStream",
    "ManualAdapter",
    "OnvifAdapter",
    "OnvifDiscovery",
    "ProbeResult",
    "Recording",
    "RtspAdapter",
]
