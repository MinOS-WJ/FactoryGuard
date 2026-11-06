"""Built-in mappings for common camera-native alarm codes."""

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
    _template(
        "移动侦测",
        {
            "name": "移动侦测告警",
            "native_event_codes": ["motiondetect", "motion"],
            "actions": ["event", "notify"],
        },
    ),
    _template(
        "区域入侵",
        {
            "name": "区域入侵告警",
            "native_event_codes": ["fielddetection", "fielddetect", "intrusion"],
            "actions": ["event", "notify"],
        },
    ),
    _template(
        "视频遮挡",
        {
            "name": "视频遮挡告警",
            "native_event_codes": ["tamperdetection", "tamper"],
            "actions": ["event", "notify"],
        },
    ),
    _template(
        "视频丢失",
        {
            "name": "视频信号丢失",
            "native_event_codes": ["videolosssignal", "videoloss"],
            "actions": ["event", "notify"],
        },
    ),
    _template(
        "设备防拆",
        {
            "name": "设备防拆告警",
            "native_event_codes": ["alarm", "tamperalarm"],
            "actions": ["event", "notify"],
        },
    ),
)


def builtin_template_names() -> tuple[str, ...]:
    return tuple(str(item["name"]) for item in BUILTIN_RULE_TEMPLATES)


__all__ = ["BUILTIN_RULE_TEMPLATES", "builtin_template_names"]
