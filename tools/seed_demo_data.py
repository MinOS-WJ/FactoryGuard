"""External demo-data injector. This is intentionally not part of the app UI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factoryguard.domain.models import Area, CameraDevice, Rule, RuleType, Site, StreamRole, VideoStream
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.sqlite import SQLiteStore


def build_topology() -> EntityRegistry:
    return EntityRegistry(
        sites=(Site("demo-site", "演示厂区", "外部注入的结构化规则演示拓扑"),),
        areas=(Area("demo-area-gate", "demo-site", "东门"), Area("demo-area-warehouse", "demo-site", "成品仓库")),
        cameras=(
            CameraDevice("demo-camera-gate", "demo-area-gate", "东门摄像机", "DemoCam", "rtsp://demo/gate"),
            CameraDevice("demo-camera-warehouse", "demo-area-warehouse", "仓库摄像机", "DemoCam", "rtsp://demo/warehouse"),
        ),
        streams=(
            VideoStream("demo-stream-gate-main", "demo-camera-gate", "东门主码流", "rtsp://demo/gate/main", StreamRole.MAIN, 1280, 720, 2.0, "models/yolo11n.onnx", 0.25),
            VideoStream("demo-stream-warehouse-main", "demo-camera-warehouse", "仓库主码流", "rtsp://demo/warehouse/main", StreamRole.MAIN, 1280, 720, 2.0, "models/yolo11n.onnx", 0.25),
        ),
    )


def build_rules() -> tuple[Rule, ...]:
    return (
        Rule("demo-rule-intrusion", stream_id="demo-stream-gate-main", site_id="demo-site", area_id="demo-area-gate", camera_id="demo-camera-gate", name="东门区域入侵", type=RuleType.POLYGON, geometry=((80, 80), (1180, 80), (1180, 640), (80, 640)), confirm_frames=3, actions=("event", "snapshot", "clip", "notify"), publication_status="draft"),
        Rule("demo-rule-line-count", stream_id="demo-stream-gate-main", site_id="demo-site", area_id="demo-area-gate", camera_id="demo-camera-gate", name="东门过线计数", type=RuleType.LINE, geometry=((0, 360), (1280, 360)), direction="down", condition="line_count", count_threshold=5, window_seconds=300, cooldown_seconds=300, actions=("event", "snapshot", "notify"), publication_status="draft"),
        Rule("demo-rule-occupancy", stream_id="demo-stream-warehouse-main", site_id="demo-site", area_id="demo-area-warehouse", camera_id="demo-camera-warehouse", name="仓库人数超限", type=RuleType.POLYGON, geometry=((80, 80), (1180, 80), (1180, 640), (80, 640)), condition="occupancy", count_threshold=8, cooldown_seconds=60, actions=("event", "snapshot", "notify"), publication_status="draft"),
        Rule("demo-rule-advanced", stream_id="demo-stream-warehouse-main", site_id="demo-site", area_id="demo-area-warehouse", camera_id="demo-camera-warehouse", name="仓库组合条件示例", type=RuleType.POLYGON, geometry=((80, 80), (1180, 80), (1180, 640), (80, 640)), condition="intrusion", condition_nodes=({"id": "demo-group", "op": "or", "children": ({"id": "demo-intrusion", "condition": "intrusion", "confirm_frames": 2}, {"id": "demo-not-occupancy", "op": "not", "children": ({"id": "demo-occupancy", "condition": "occupancy", "count_threshold": 3},)})},), actions=("event", "snapshot", "clip", "notify"), publication_status="draft"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/factoryguard.db")
    parser.add_argument("--actor", default="external-demo")
    args = parser.parse_args()
    store = SQLiteStore(args.db)
    try:
        store.save_topology(build_topology())
        for rule in build_rules():
            store.save_rule(rule, actor=args.actor, note="external demo data")
    finally:
        store.close()
    print(f"seeded external demo data into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
