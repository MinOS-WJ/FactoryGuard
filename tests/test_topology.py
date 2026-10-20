from datetime import UTC, datetime

from factoryguard.domain import (
    Area,
    CameraDevice,
    Detection,
    Frame,
    Rule,
    RuleType,
    Site,
    StreamRole,
    Topology,
    VideoStream,
)
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.ui.worker import MonitorConfig, StreamRuntimeConfig


def _topology() -> Topology:
    topology = Topology(sites=(Site("site-1", "Main factory"),))
    topology.add_area(Area("area-1", "site-1", "Warehouse"))
    topology.add_area(Area("area-2", "site-1", "Line A", parent_id="area-1"))
    topology.add_camera(CameraDevice("camera-1", "area-2", "Gate camera"))
    topology.add_stream(VideoStream("stream-main", "camera-1", "Main", "rtsp://main", StreamRole.MAIN))
    topology.add_stream(VideoStream("stream-sub", "camera-1", "Sub", "rtsp://sub", StreamRole.SUB))
    return topology


def test_nested_stream_path_and_runtime_config():
    topology = _topology()
    path = topology.stream_path("stream-sub")
    assert path.ids == {
        "site_id": "site-1",
        "area_id": "area-2",
        "camera_id": "camera-1",
        "stream_id": "stream-sub",
    }
    assert [area.id for area in path.areas] == ["area-1", "area-2"]
    assert len(topology.streams_for_area("area-1")) == 2

    config = MonitorConfig(topology=topology)
    assert {item.stream_id for item in config.resolved_streams()} == {"stream-main", "stream-sub"}
    explicit = MonitorConfig(
        streams=(StreamRuntimeConfig(stream_id="stream-main", url="rtsp://override"),),
    )
    assert explicit.resolved_streams()[0].url == "rtsp://override"

    disabled_topology = Topology(
        sites=(Site("site-disabled", "Disabled factory", enabled=False),),
        areas=(Area("area-disabled", "site-disabled", "Zone"),),
        cameras=(CameraDevice("camera-disabled", "area-disabled", "Camera"),),
        streams=(VideoStream("stream-disabled", "camera-disabled", "Stream", "rtsp://disabled"),),
    )
    assert MonitorConfig(topology=disabled_topology).resolved_streams() == ()

    out_of_order = Topology(
        sites=(Site("site-2", "Second factory"),),
        areas=(
            Area("child", "site-2", "Child", parent_id="parent"),
            Area("parent", "site-2", "Parent"),
        ),
    )
    assert [area.id for area in out_of_order.areas] == ["parent", "child"]


def test_topology_persists_and_rule_scope_isolated(tmp_path):
    topology = _topology()
    store = SQLiteStore(tmp_path / "topology.db")
    store.save_topology(topology)
    loaded = store.load_topology()
    assert loaded.stream_path("stream-main").display_name.endswith("Gate camera / Main")

    rule = Rule(
        id="rule-1",
        stream_id="stream-main",
        area_id="area-2",
        name="gate",
        type=RuleType.POLYGON,
        geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
        confirm_frames=1,
    )
    evaluator = RuleEvaluator([rule])
    now = datetime.now(UTC)
    detection = Detection("person", 0.9, (10, 10, 20, 80), "track-1")
    assert evaluator.evaluate(Frame("stream-sub", 1, now, 100, 100, detections=(detection,), area_id="area-2")) == []
    assert len(evaluator.evaluate(Frame("stream-main", 2, now, 100, 100, detections=(detection,), area_id="area-2"))) == 1
    store.close()


def test_topology_entity_enable_state_controls_runtime_and_audit(tmp_path):
    topology = _topology()
    database = tmp_path / "entity-enabled.db"
    store = SQLiteStore(database)
    store.save_topology(topology)
    assert store.set_entity_enabled("camera", "camera-1", False, actor="operator")
    loaded = store.load_topology()
    assert MonitorConfig(topology=loaded).resolved_streams() == ()
    audit = store._connection_or_raise().execute(
        "SELECT action, detail FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1", ("camera-1",)
    ).fetchone()
    assert tuple(audit) == ("topology.enabled.changed", "kind=camera; enabled=0")
    store.close()
