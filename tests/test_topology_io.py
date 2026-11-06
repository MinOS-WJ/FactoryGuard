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
            VideoStream("stream-sub", "camera-1", "Sub", "rtsp://sub", StreamRole.SUB),
        ),
    )
    restored = topology_from_csv(topology_to_csv(topology))
    assert restored.to_dict() == topology.to_dict()
    assert restored.stream_path("stream-sub").areas[-1].id == "area-2"


def test_topology_csv_does_not_export_visual_model_columns():
    csv_text = topology_to_csv(
        EntityRegistry(
            sites=(Site("site-1", "Main"),),
            areas=(Area("area-1", "site-1", "Yard"),),
            cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
            streams=(VideoStream("stream-1", "camera-1", "Main", "rtsp://main"),),
        )
    )
    header = next(line for line in csv_text.splitlines() if line.startswith("site_id,"))
    fields = next(csv.reader([header]))
    assert len(fields) == len(set(fields))
    assert "stream_model" not in fields
    assert "stream_confidence" not in fields
    assert "stream_input_size" not in fields


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
