from __future__ import annotations

"""Application service coordinating device adapters and native event ingestion."""

import asyncio
import threading
from dataclasses import replace
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from factoryguard.devices.base import DeviceAdapter, DeviceCredentials, ProbeResult
from factoryguard.devices.gb28181.server import Gb28181Adapter, Gb28181Platform
from factoryguard.devices.onvif import OnvifAdapter, OnvifDiscovery
from factoryguard.devices.rtsp import ManualAdapter, RtspAdapter
from factoryguard.domain.models import (
    DeviceProtocol,
    EventSource,
    NativeEvent,
    Rule,
    VideoDevice,
)
from factoryguard.security.credentials import CredentialVault, default_vault
from factoryguard.storage.sqlite import SQLiteStore

from .native_events import NativeEventIngestor


class DeviceAdapterFactory:
    @staticmethod
    def create(
        device: VideoDevice,
        *,
        credentials: DeviceCredentials | None = None,
        gb_platform: Gb28181Platform | None = None,
    ) -> DeviceAdapter:
        if device.protocol is DeviceProtocol.ONVIF:
            return OnvifAdapter(device, credentials)
        if device.protocol is DeviceProtocol.GB28181:
            if gb_platform is None:
                raise ValueError("GB/T 28181 adapter requires a started platform service")
            return Gb28181Adapter(device, credentials, platform=gb_platform)
        if device.protocol is DeviceProtocol.RTSP:
            return RtspAdapter(device, credentials)
        return ManualAdapter(device, credentials)


class DeviceManager:
    """Non-UI device service. Qt workers call these methods in worker threads."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        vault: CredentialVault | None = None,
        gb_platform: Gb28181Platform | None = None,
    ) -> None:
        self.store = store
        self.vault = vault or default_vault()
        self.gb_platform = gb_platform
        self._subscriptions: dict[str, Any] = {}
        self._subscription_threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._gb_thread: threading.Thread | None = None
        self._gb_loop: asyncio.AbstractEventLoop | None = None
        self._ingestor: NativeEventIngestor | None = None

    def set_ingestor(self, ingestor: NativeEventIngestor) -> None:
        self._ingestor = ingestor

    def credentials_for(self, device: VideoDevice) -> DeviceCredentials:
        if not device.credentials_ref:
            return DeviceCredentials()
        blob = self.store.get_device_credential_blob(device.credentials_ref)
        return self.vault.decode(blob) if blob else DeviceCredentials()

    def save_credentials(self, device: VideoDevice, credentials: DeviceCredentials, *, actor: str = "ui") -> VideoDevice:
        reference = device.credentials_ref or f"cred-{device.id}"
        self.store.save_device_credential(reference, self.vault.encode(credentials), actor=actor)
        updated = VideoDevice(
            id=device.id,
            site_id=device.site_id,
            area_id=device.area_id,
            name=device.name,
            protocol=device.protocol,
            endpoint=device.endpoint,
            vendor=device.vendor,
            model=device.model,
            firmware=device.firmware,
            serial_number=device.serial_number,
            device_id=device.device_id,
            credentials_ref=reference,
            capabilities=device.capabilities,
            analysis_policy=device.analysis_policy,
            enabled=device.enabled,
        )
        self.store.save_device(updated, actor=actor)
        return updated

    def adapter_for(self, device: VideoDevice) -> DeviceAdapter:
        return DeviceAdapterFactory.create(
            device,
            credentials=self.credentials_for(device),
            gb_platform=self.gb_platform,
        )

    def probe(self, device: VideoDevice, *, timeout: float = 5.0) -> ProbeResult:
        adapter = self.adapter_for(device)
        try:
            result = adapter.probe(timeout=timeout)
            channels = ()
            if result.ok and result.capabilities.live_video:
                try:
                    channels = adapter.list_channels()
                except (KeyError, RuntimeError, ValueError):
                    channels = ()
            if channels:
                result = replace(
                    result,
                    capabilities=replace(
                        result.capabilities,
                        media_profiles=tuple(
                            f"{channel.id}:{channel.name}:{channel.encoding}:{channel.width}x{channel.height}"
                            for channel in channels
                        ),
                    ),
                )
        finally:
            adapter.close()
        now = datetime.now(UTC).isoformat()
        updated = replace(
            device,
            capabilities=result.capabilities if result.ok else device.capabilities,
            last_probe_at=now,
            last_ok_at=now if result.ok else device.last_ok_at,
            health_status="ready" if result.ok else "degraded",
            health_detail=result.detail,
        )
        self.store.save_device(updated, actor="device-probe")
        return result

    def discover_onvif(self, *, timeout: float = 3.0) -> tuple[Any, ...]:
        return OnvifDiscovery().discover(timeout=timeout)

    def start_native_subscription(
        self,
        device: VideoDevice,
        rules: Sequence[Rule],
        *,
        channels: Sequence[str] = (),
    ) -> None:
        if device.id in self._subscription_threads:
            return
        stop_event = threading.Event()
        self._stop_events[device.id] = stop_event
        database_path = self.store.path
        native_rules = tuple(rule for rule in rules if rule.enabled)

        def record(store: SQLiteStore, *, healthy: bool, detail: str, last_event_at: datetime | None = None) -> None:
            store.set_device_subscription(
                device.id,
                source=str(device.protocol),
                state="healthy" if healthy else "degraded",
                detail=detail,
                last_event_at=last_event_at,
                actor="device-event",
            )

        def run() -> None:
            adapter = self.adapter_for(device)
            local_store = SQLiteStore(database_path)
            ingestor = NativeEventIngestor(local_store, native_rules, channels=channels)
            try:
                subscription = adapter.subscribe_events()
                self._subscriptions[device.id] = subscription
                record(local_store, healthy=True, detail="原生事件订阅已建立")
                for native_event in subscription.iterator:
                    if stop_event.is_set():
                        break
                    ingestor.ingest(native_event, actor="device-event")
                    record(
                        local_store,
                        healthy=True,
                        detail="正在接收设备事件",
                        last_event_at=native_event.occurred_at,
                    )
                if not stop_event.is_set():
                    record(local_store, healthy=False, detail="设备事件订阅已结束")
            except Exception as exc:  # noqa: BLE001 - surface subscription failure for fallback
                record(local_store, healthy=False, detail=str(exc))
            finally:
                adapter.close()
                local_store.close()

        thread = threading.Thread(target=run, name=f"native-{device.id}", daemon=True)
        self._subscription_threads[device.id] = thread
        thread.start()

    def stop_native_subscription(self, device_id: str) -> None:
        stop_event = self._stop_events.pop(device_id, None)
        if stop_event is not None:
            stop_event.set()
        subscription = self._subscriptions.pop(device_id, None)
        if subscription is not None:
            subscription.close()
        self._subscription_threads.pop(device_id, None)

    def start_gb_service(self, *, host: str = "0.0.0.0", port: int = 5060) -> None:
        if self.gb_platform is None or self._gb_thread is not None:
            return

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._gb_loop = loop
            try:
                loop.run_until_complete(self.gb_platform.start(host=host, port=port))
                loop.run_forever()
            finally:
                try:
                    loop.run_until_complete(self.gb_platform.close())
                finally:
                    loop.close()
                    self._gb_loop = None

        self._gb_thread = threading.Thread(target=run, name="gb28181-platform", daemon=True)
        self._gb_thread.start()

    def stop_gb_service(self) -> None:
        loop = self._gb_loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if self._gb_thread is not None:
            self._gb_thread.join(timeout=2.0)
        self._gb_thread = None

    def gb_registered_devices(self) -> tuple[dict[str, Any], ...]:
        if self.gb_platform is None:
            return ()
        return tuple(
            {
                "device_id": registration.device_id,
                "address": registration.address,
                "expires_at": registration.expires_at.isoformat(),
                "channels": tuple(channel.__dict__ for channel in registration.channels.values()),
            }
            for registration in self.gb_platform.registrations.values()
        )

    def close(self) -> None:
        for device_id in tuple(self._stop_events):
            self.stop_native_subscription(device_id)
        self.stop_gb_service()
        if self.gb_platform is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.gb_platform.close())
            else:
                loop.create_task(self.gb_platform.close())


__all__ = ["DeviceAdapterFactory", "DeviceManager"]
