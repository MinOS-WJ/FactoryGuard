"""Composable condition-tree primitives for low-code rules.

Rules keep their legacy single-condition fields for compatibility. When
``condition_nodes`` is non-empty, it is treated as an explicit tree of leaf
conditions and logical operators (AND / OR / NOT).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

CONDITION_NAMES = {"intrusion", "line_cross", "line_count", "occupancy", "dwell"}
LOGICAL_OPS = {"and", "or", "not"}


def normalize_condition_nodes(value: Iterable[Mapping[str, Any]] | None) -> tuple[dict[str, Any], ...]:
    """Validate a condition-tree definition and return a normalized copy."""
    if not value:
        return ()
    normalized = tuple(_normalize_node(node) for node in value)
    ids = _node_ids(normalized)
    if len(ids) != len(set(ids)):
        raise ValueError("condition node ids must be unique")
    return normalized


def condition_tree_summary(nodes: tuple[dict[str, Any], ...]) -> str:
    """Return a compact human-readable representation of the tree."""
    if not nodes:
        return ""
    return " AND ".join(_summarize(node) for node in nodes)


def evaluate_condition_tree(
    nodes: tuple[dict[str, Any], ...],
    results: Mapping[str, bool],
) -> bool:
    """Evaluate a condition tree using already-computed leaf truth values."""
    if not nodes:
        return False
    return all(_evaluate_node(node, results) for node in nodes)


def iter_leaf_nodes(
    nodes: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """Return leaves in depth-first order so callers can evaluate each one."""
    leaves: list[dict[str, Any]] = []
    _collect_leaves(nodes, leaves)
    return tuple(leaves)


def condition_tree_errors(nodes: tuple[dict[str, Any], ...]) -> list[tuple[str, str]]:
    """Return ``(node_id, message)`` validation errors without raising."""
    if not nodes:
        return []
    errors: list[tuple[str, str]] = []
    ids = _node_ids(nodes)
    for duplicate in sorted({value for value in ids if ids.count(value) > 1}):
        errors.append((duplicate, "node id is duplicated"))
    for node in nodes:
        _node_errors(node, (), errors)
    return errors


def _node_errors(node: dict[str, Any], path: tuple[str, ...], errors: list[tuple[str, str]]) -> None:
    node_id = str(node.get("id", ""))
    if not node_id:
        errors.append(("", "condition node id is required"))
        return
    if "op" in node:
        op = str(node.get("op", "")).strip().lower()
        if op not in LOGICAL_OPS:
            errors.append((node_id, f"unsupported logical operator: {op!r}"))
            return
        children = node.get("children", ())
        if not isinstance(children, (list, tuple)):
            errors.append((node_id, "logical node children must be a list"))
            return
        if not children:
            errors.append((node_id, "logical node must have at least one child"))
            return
        if op == "not" and len(children) != 1:
            errors.append((node_id, "not node requires exactly one child"))
            return
        for index, child in enumerate(children):
            _node_errors(child, (*path, node_id), errors)
        return
    condition = str(node.get("condition", "")).strip().lower()
    if condition not in CONDITION_NAMES:
        errors.append((node_id, f"unsupported condition: {condition!r}"))
        return
    direction = str(node.get("direction", "both")).strip().lower()
    if direction not in {"both", "up", "down", "left", "right"}:
        errors.append((node_id, "condition direction must be both, up, down, left or right"))

def _normalize_node(node: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(node, Mapping):
        raise TypeError("condition node must be a mapping")
    node_id = str(node.get("id", "")).strip()
    if not node_id:
        raise ValueError("condition node id is required")
    op = str(node.get("op", "")).strip().lower()
    if op:
        if op not in LOGICAL_OPS:
            raise ValueError(f"unsupported logical operator: {op!r}")
        children = node.get("children", ())
        if not isinstance(children, (list, tuple)):
            raise TypeError("logical node children must be a list")
        if not children:
            raise ValueError("logical node must have at least one child")
        if op == "not" and len(children) != 1:
            raise ValueError("not node requires exactly one child")
        return {
            "id": node_id,
            "op": op,
            "children": [_normalize_node(child) for child in children],
        }
    condition = str(node.get("condition", "")).strip().lower()
    if condition not in CONDITION_NAMES:
        raise ValueError(
            f"unsupported condition: {condition!r}; expected one of {sorted(CONDITION_NAMES)}"
        )
    direction = str(node.get("direction", "both")).strip().lower()
    if direction not in {"both", "up", "down", "left", "right"}:
        raise ValueError("condition direction must be both, up, down, left or right")
    return {
        "id": node_id,
        "condition": condition,
        "direction": direction,
        "count_threshold": int(node.get("count_threshold", 1)),
        "window_seconds": float(node.get("window_seconds", 0.0)),
        "dwell_seconds": float(node.get("dwell_seconds", 0.0)),
        "confirm_frames": int(node.get("confirm_frames", 1)),
        "cooldown_seconds": float(node.get("cooldown_seconds", 60.0)),
    }


def _node_ids(nodes: tuple[dict[str, Any], ...]) -> list[str]:
    ids: list[str] = []
    for node in nodes:
        ids.append(str(node["id"]))
        if "op" in node:
            ids.extend(_node_ids(tuple(node["children"])))
    return ids


def _collect_leaves(nodes: tuple[dict[str, Any], ...], leaves: list[dict[str, Any]]) -> None:
    for node in nodes:
        if "op" in node:
            _collect_leaves(tuple(node["children"]), leaves)
        else:
            leaves.append(node)


def _evaluate_node(node: dict[str, Any], results: Mapping[str, bool]) -> bool:
    if "op" in node:
        child_values = [_evaluate_node(child, results) for child in node["children"]]
        if node["op"] == "and":
            return all(child_values)
        if node["op"] == "or":
            return any(child_values)
        return not child_values[0]
    return bool(results.get(node["id"], False))


def _summarize(node: dict[str, Any]) -> str:
    if "op" in node:
        if node["op"] == "not":
            return f"NOT({_summarize(node['children'][0])})"
        joiner = " AND " if node["op"] == "and" else " OR "
        return "(" + joiner.join(_summarize(child) for child in node["children"]) + ")"
    extras = []
    if node["condition"] in {"line_cross", "line_count"}:
        extras.append(node["direction"])
    if node["condition"] == "line_count":
        extras.append(f"{node['window_seconds']:g}s>={node['count_threshold']}")
    elif node["condition"] == "occupancy":
        extras.append(f">={node['count_threshold']}")
    elif node["condition"] == "dwell":
        extras.append(f">={node['dwell_seconds']:g}s")
    suffix = f"({','.join(extras)})" if extras else ""
    return f"{node['condition']}{suffix}"


__all__ = [
    "CONDITION_NAMES",
    "condition_tree_summary",
    "condition_tree_errors",
    "evaluate_condition_tree",
    "iter_leaf_nodes",
    "normalize_condition_nodes",
]