"""Compatibility stub for removed visual rule simulation."""

from __future__ import annotations

from typing import Any


class VisualRuleRemoved(RuntimeError):
    """Raised when legacy visual rule simulation is invoked."""


def replay_rule_from_track(*_args: Any, **_kwargs: Any) -> Any:
    raise VisualRuleRemoved("画面轨迹模拟已移除，平台只接收设备原生告警")


def simulate_rule(*_args: Any, **_kwargs: Any) -> Any:
    raise VisualRuleRemoved("画面规则模拟已移除，平台只接收设备原生告警")


__all__ = ["VisualRuleRemoved", "replay_rule_from_track", "simulate_rule"]
