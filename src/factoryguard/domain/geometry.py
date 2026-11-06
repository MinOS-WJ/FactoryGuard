"""Compatibility stub for removed pixel geometry matching."""

from __future__ import annotations

from typing import Any


class VisualRuleRemoved(RuntimeError):
    """Raised when legacy pixel-rule geometry is invoked."""


def _removed(*_args: Any, **_kwargs: Any) -> None:
    raise VisualRuleRemoved("画面几何规则已移除，平台只接收设备原生告警")


def polygon_is_valid(*args: Any, **kwargs: Any) -> bool:
    _removed(*args, **kwargs)


def point_in_polygon(*args: Any, **kwargs: Any) -> bool:
    _removed(*args, **kwargs)


def bbox_overlaps_polygon(*args: Any, **kwargs: Any) -> bool:
    _removed(*args, **kwargs)


def crossing_direction(*args: Any, **kwargs: Any) -> str | None:
    _removed(*args, **kwargs)


__all__ = [
    "VisualRuleRemoved",
    "bbox_overlaps_polygon",
    "crossing_direction",
    "point_in_polygon",
    "polygon_is_valid",
]
