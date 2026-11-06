"""Populate the merchant console with realistic multi-area demo data."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factoryguard.domain.incidents import Incident, IncidentSeverity, IncidentSource, IncidentStatus
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.sqlite import SQLiteStore


def seed(store: SQLiteStore) -> None:
    site = Site("demo-site", "星河智造园", "商户与物业联合管理的综合园区")
    areas = (
        Area("demo-area-gate", site.id, "东门"),
        Area("demo-area-warehouse", site.id, "成品仓库"),
        Area("demo-area-workshop", site.id, "装配车间"),
        Area("demo-area-parking", site.id, "访客停车场"),
    )
    camera_specs = (
        (
            "gate",
            "东门全景",
            DeviceProtocol.ONVIF,
            "http://192.168.10.21/onvif/device_service",
            "online",
        ),
        (
            "warehouse",
            "仓库北门",
            DeviceProtocol.ONVIF,
            "http://192.168.10.22/onvif/device_service",
            "online",
        ),
        (
            "workshop",
            "车间通道",
            DeviceProtocol.GB28181,
            "gb28181://34020000001320000003",
            "online",
        ),
        ("parking", "停车场入口", DeviceProtocol.RTSP, "rtsp://192.168.10.24/stream2", "online"),
        (
            "warehouse-inside",
            "仓库内部",
            DeviceProtocol.RTSP,
            "rtsp://192.168.10.25/stream2",
            "offline",
        ),
        (
            "gate-side",
            "东门侧翼",
            DeviceProtocol.ONVIF,
            "http://192.168.10.26/onvif/device_service",
            "unknown",
        ),
    )
    area_for = {
        "gate": "demo-area-gate",
        "gate-side": "demo-area-gate",
        "warehouse": "demo-area-warehouse",
        "warehouse-inside": "demo-area-warehouse",
        "workshop": "demo-area-workshop",
        "parking": "demo-area-parking",
    }
    devices: list[VideoDevice] = []
    cameras: list[CameraDevice] = []
    streams: list[VideoStream] = []
    for suffix, name, protocol, endpoint, _status in camera_specs:
        device_id = f"demo-device-{suffix}"
        camera_id = f"demo-camera-{suffix}"
        stream_id = f"demo-stream-{suffix}"
        area_id = area_for[suffix]
        devices.append(
            VideoDevice(
                id=device_id,
                site_id=site.id,
                area_id=area_id,
                name=name,
                protocol=protocol,
                endpoint=endpoint,
                vendor="Hikvision" if protocol is not DeviceProtocol.GB28181 else "大华",
                analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                capabilities=DeviceCapabilities(live_video=True, native_events=True),
                health_status="ready" if suffix != "warehouse-inside" else "offline",
            )
        )
        cameras.append(
            CameraDevice(
                id=camera_id,
                area_id=area_id,
                name=name,
                vendor="演示设备",
                address=endpoint,
                device_id=device_id,
                channel_id="ch1",
            )
        )
        streams.append(
            VideoStream(
                id=stream_id,
                camera_id=camera_id,
                name="子码流",
                url=f"rtsp://demo.local/{suffix}/sub",
                role=StreamRole.SUB,
                width=1280,
                height=720,
                fps=2.0,
                analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                device_id=device_id,
            )
        )
    store.save_topology(
        EntityRegistry(
            sites=(site,), areas=areas, devices=devices, cameras=cameras, streams=streams
        )
    )
    for spec, stream in zip(camera_specs, streams, strict=True):
        state = spec[4]
        detail = (
            "演示数据：视频流连接正常"
            if state == "online"
            else "演示数据：设备网络中断"
            if state == "offline"
            else "等待首次检测"
        )
        store.set_stream_health(stream.id, state, detail, actor="demo-seed")
    now = datetime.now(UTC)
    incidents = (
        Incident.create(
            title="仓库北门检测到人员闯入",
            description="设备上报区域入侵事件，请安保人员前往仓库北门确认现场情况。",
            severity=IncidentSeverity.HIGH,
            site_id=site.id,
            area_id="demo-area-warehouse",
            camera_id="demo-camera-warehouse",
            source=IncidentSource.DEVICE,
            reporter="ONVIF 设备告警",
            occurred_at=now - timedelta(minutes=18),
        ),
        Incident.create(
            title="停车场入口摄像头离线",
            description="平台连续 3 次未取得视频流，可能为网络或供电异常。",
            severity=IncidentSeverity.MEDIUM,
            site_id=site.id,
            area_id="demo-area-parking",
            camera_id="demo-camera-parking",
            source=IncidentSource.SYSTEM,
            reporter="设备健康检查",
            occurred_at=now - timedelta(minutes=42),
        ),
        Incident.create(
            title="东门访客车辆滞留",
            description="用户通过现场问询上报访客车辆在东门停留时间异常，已通知物业巡查。",
            severity=IncidentSeverity.LOW,
            site_id=site.id,
            area_id="demo-area-gate",
            source=IncidentSource.AI_AGENT,
            reporter="物业值班员",
            occurred_at=now - timedelta(hours=2, minutes=7),
        ).with_status(IncidentStatus.ACKNOWLEDGED),
    )
    for incident in incidents:
        store.create_incident(incident, actor="demo-seed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/factoryguard.db")
    args = parser.parse_args()
    store = SQLiteStore(args.db)
    try:
        seed(store)
    finally:
        store.close()
    print(f"merchant demo data seeded into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
