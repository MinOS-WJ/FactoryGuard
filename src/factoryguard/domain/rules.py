"""Compatibility stub for removed visual rule evaluation."""

from __future__ import annotations

from typing import Any


class VisualRuleRemoved(RuntimeError):
    """Raised when legacy frame-rule evaluation is invoked."""


class RuleEvaluator:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise VisualRuleRemoved("画面规则评估已移除，平台只接收设备原生告警")


__all__ = ["RuleEvaluator", "VisualRuleRemoved"]
