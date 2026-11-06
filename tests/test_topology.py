from factoryguard.domain import (
    Area,
    CameraDevice,
    Site,
    StreamRole,
    Topology,
    VideoStream,
)
from factoryguard.storage.sqlite import SQLiteStore


def _topology() -> Topology:
    topology = Topology(sites=(Site("site-1", "Main factory"),))
    topology.add_area(Area("area-1", "site-1", "Warehouse"))
    topology.add_area(Area("area-2", "site-1", "Line A", parent_id="area-1"))
    topology.add_camera(CameraDevice("camera-1", "area-2", "Gate camera"))
    topology.add_stream(
        VideoStream("stream-main", "camera-1", "Main", "rtsp://main", StreamRole.MAIN)
    )
    topology.add_stream(VideoStream("stream-sub", "camera-1", "Sub", "rtsp://sub", StreamRole.SUB))
    return topology


def test_nested_stream_path():
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

    out_of_order = Topology(
        sites=(Site("site-2", "Second factory"),),
        areas=(
            Area("child", "site-2", "Child", parent_id="parent"),
            Area("parent", "site-2", "Parent"),
        ),
    )
    assert [area.id for area in out_of_order.areas] == ["parent", "child"]


def test_topology_persists(tmp_path):
    topology = _topology()
    store = SQLiteStore(tmp_path / "topology.db")
    store.save_topology(topology)
    loaded = store.load_topology()
    assert loaded.stream_path("stream-main").display_name.endswith("Gate camera / Main")
    store.close()


def test_topology_entity_enable_state_controls_runtime_and_audit(tmp_path):
    topology = _topology()
    database = tmp_path / "entity-enabled.db"
    store = SQLiteStore(database)
    store.save_topology(topology)
    assert store.set_entity_enabled("camera", "camera-1", False, actor="operator")
    loaded = store.load_topology()
    assert not loaded.get_camera("camera-1").enabled
    audit = (
        store._connection_or_raise()
        .execute(
            "SELECT action, detail FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1",
            ("camera-1",),
        )
        .fetchone()
    )
    assert tuple(audit) == ("topology.enabled.changed", "kind=camera; enabled=0")
    store.close()
