from __future__ import annotations

import csv

import pytest

from factoryguard.domain.models import Area, CameraDevice, Site, StreamRole, VideoStream
from factoryguard.domain.topology import EntityRegistry
from factoryguard.topology_io import topology_from_csv, topology_to_csv


def test_topology_csv_round_trips_nested_multi_stream_inventory():
    topology = EntityRegistry(
        sites=(Site("site-1", "Main", "Factory"),),
        areas=(
            Area("area-1", "site-1", "Warehouse"),
            Area("area-2", "site-1", "Line A", parent_id="area-1"),
        ),
        cameras=(CameraDevice("camera-1", "area-2", "Gate", vendor="Acme", address="10.0.0.2"),),
        streams=(
            VideoStream("stream-main", "camera-1", "Main", "rtsp://main", StreamRole.MAIN),
            VideoStream("stream-sub", "camera-1", "Sub", "rtsp://sub", StreamRole.SUB, input_size=416),
        ),
    )
    restored = topology_from_csv(topology_to_csv(topology))
    assert restored.to_dict() == topology.to_dict()
    assert restored.stream_path("stream-sub").areas[-1].id == "area-2"


def test_topology_csv_accepts_legacy_rows_without_input_size():
    csv_text = topology_to_csv(
        EntityRegistry(
            sites=(Site("site-1", "Main"),),
            areas=(Area("area-1", "site-1", "Yard"),),
            cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
            streams=(VideoStream("stream-1", "camera-1", "Main", "rtsp://main"),),
        )
    )
    lines = csv_text.splitlines()
    header = next(line for line in lines if line.startswith("site_id,"))
    fields = next(csv.reader([header]))
    fields.remove("stream_input_size")
    output = [",".join(fields)]
    for line in lines:
        if line == header:
            continue
        values = next(csv.reader([line]))
        input_index = header.split(",").index("stream_input_size")
        del values[input_index]
        output.append(",".join(values))
    restored = topology_from_csv("\n".join(output))
    assert restored.streams[0].input_size == 640

def test_topology_csv_supports_camera_without_stream():
    topology = EntityRegistry(
        sites=(Site("site-1", "Main"),),
        areas=(Area("area-1", "site-1", "Yard"),),
        cameras=(CameraDevice("camera-1", "area-1", "Unconfigured"),),
    )
    restored = topology_from_csv(topology_to_csv(topology))
    assert restored.to_dict() == topology.to_dict()


def test_topology_csv_redacts_credentials_and_requires_reentry():
    topology = EntityRegistry(
        sites=(Site("site-1", "Factory"),),
        areas=(Area("area-1", "site-1", "Workshop"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
        streams=(
            VideoStream(
                "stream-1",
                "camera-1",
                "Main",
                "rtsp://admin:secret@camera.example/live",
                StreamRole.MAIN,
            ),
        ),
    )
    exported = topology_to_csv(topology)

    assert "secret" not in exported
    assert "rtsp://***:***@camera.example/live" in exported
    with pytest.raises(ValueError, match="redacted credentials"):
        topology_from_csv(exported)

def test_topology_csv_rejects_conflicting_duplicate_rows():
    csv_text = topology_to_csv(
        EntityRegistry(
            sites=(Site("site-1", "Main"),),
            areas=(Area("area-1", "site-1", "Yard"),),
            cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
            streams=(VideoStream("stream-1", "camera-1", "Main", "rtsp://main"),),
        )
    )
    lines = csv_text.splitlines()
    lines.append(lines[1].replace("Gate", "Different"))
    with pytest.raises(ValueError, match="conflicting camera"):
        topology_from_csv("\n".join(lines))
