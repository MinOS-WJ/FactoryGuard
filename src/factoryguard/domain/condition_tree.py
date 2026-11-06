"""Compatibility stub for removed visual condition trees."""

from __future__ import annotations

from typing import Any


class VisualRuleRemoved(RuntimeError):
    """Raised when legacy pixel-rule logic is invoked."""


def _removed(*_args: Any, **_kwargs: Any) -> None:
    raise VisualRuleRemoved("画面条件规则已移除，平台只接收设备原生告警")


def normalize_condition_nodes(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
    _removed(*args, **kwargs)


def condition_tree_summary(*args: Any, **kwargs: Any) -> str:
    _removed(*args, **kwargs)


def evaluate_condition_tree(*args: Any, **kwargs: Any) -> bool:
    _removed(*args, **kwargs)


def iter_leaf_nodes(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
    _removed(*args, **kwargs)


def condition_tree_errors(*args: Any, **kwargs: Any) -> list[tuple[str, str]]:
    _removed(*args, **kwargs)


__all__ = [
    "VisualRuleRemoved",
    "condition_tree_errors",
    "condition_tree_summary",
    "evaluate_condition_tree",
    "iter_leaf_nodes",
    "normalize_condition_nodes",
]
