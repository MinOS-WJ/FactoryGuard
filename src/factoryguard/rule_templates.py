"""Small built-in industry rule template library for small factories."""

from __future__ import annotations

from typing import Any


def _template(name: str, rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "factoryguard_rule_template",
        "name": name,
        "rule": rule,
    }


BUILTIN_RULE_TEMPLATES: tuple[dict[str, Any], ...] = (
    _template("intrusion", {"name": "Intrusion", "type": "polygon", "geometry": [[0, 0], [1280, 0], [1280, 720], [0, 720]], "target_labels": ["person"], "min_confidence": 0.45, "min_box_height": 32, "confirm_frames": 3, "cooldown_seconds": 60, "condition": "intrusion", "actions": ["event", "snapshot", "clip", "notify"]}),
    _template("line_cross", {"name": "Line Cross", "type": "line", "geometry": [[0, 360], [1280, 360]], "target_labels": ["person"], "direction": "both", "condition": "line_cross", "cooldown_seconds": 60, "actions": ["event", "snapshot", "clip", "notify"]}),
    _template("line_count", {"name": "Line Count", "type": "line", "geometry": [[0, 360], [1280, 360]], "target_labels": ["person"], "direction": "both", "condition": "line_count", "count_threshold": 10, "window_seconds": 300, "cooldown_seconds": 300, "actions": ["event", "snapshot", "clip", "notify"]}),
    _template("occupancy", {"name": "Occupancy", "type": "polygon", "geometry": [[0, 0], [1280, 0], [1280, 720], [0, 720]], "target_labels": ["person"], "condition": "occupancy", "count_threshold": 5, "cooldown_seconds": 60, "actions": ["event", "snapshot", "notify"]}),
    _template("dwell", {"name": "Dwell", "type": "polygon", "geometry": [[0, 0], [1280, 0], [1280, 720], [0, 720]], "target_labels": ["person"], "condition": "dwell", "dwell_seconds": 15, "cooldown_seconds": 60, "actions": ["event", "snapshot", "clip", "notify"]}),
)


def builtin_template_names() -> tuple[str, ...]:
    return tuple(str(item["name"]) for item in BUILTIN_RULE_TEMPLATES)


__all__ = ["BUILTIN_RULE_TEMPLATES", "builtin_template_names"]
