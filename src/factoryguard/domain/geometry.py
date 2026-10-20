from __future__ import annotations

from math import isclose

from .models import BBox, Point

EPSILON = 1e-9


def polygon_is_valid(polygon: tuple[Point, ...] | list[Point]) -> bool:
    """Return False for degenerate or self-intersecting polygons."""
    points = tuple(polygon)
    if len(points) < 3 or abs(_signed_area(points)) < EPSILON:
        return False
    edges = list(zip(points, points[1:] + points[:1]))
    for i, (a1, a2) in enumerate(edges):
        for j, (b1, b2) in enumerate(edges):
            if i >= j or j in (i - 1, i + 1) or {i, j} == {0, len(edges) - 1}:
                continue
            if segments_intersect(a1, a2, b1, b2):
                return False
    return True


def point_in_polygon(point: Point, polygon: tuple[Point, ...] | list[Point]) -> bool:
    """Ray-casting test; points on an edge count as inside."""
    if not polygon_is_valid(tuple(polygon)):
        return False
    x, y = point
    inside = False
    points = tuple(polygon)
    for current, following in zip(points, points[1:] + points[:1]):
        if _point_on_segment(point, current, following):
            return True
        x1, y1 = current
        x2, y2 = following
        if (y1 > y) != (y2 > y):
            cross_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < cross_x:
                inside = not inside
    return inside


def bbox_overlaps_polygon(bbox: BBox, polygon: tuple[Point, ...], min_ratio: float = 0.0) -> bool:
    """Use the bbox anchor by default, optionally require corner overlap ratio."""
    if min_ratio <= 0:
        x1, _y1, x2, y2 = bbox
        return point_in_polygon(((x1 + x2) / 2.0, y2), polygon)
    corners = ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]), (bbox[0], bbox[3]))
    return sum(point_in_polygon(corner, polygon) for corner in corners) / 4 >= min_ratio


def segments_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    if o1 * o2 < -EPSILON and o3 * o4 < -EPSILON:
        return True
    return (
        isclose(o1, 0.0, abs_tol=EPSILON) and _point_on_segment(b1, a1, a2)
        or isclose(o2, 0.0, abs_tol=EPSILON) and _point_on_segment(b2, a1, a2)
        or isclose(o3, 0.0, abs_tol=EPSILON) and _point_on_segment(a1, b1, b2)
        or isclose(o4, 0.0, abs_tol=EPSILON) and _point_on_segment(a2, b1, b2)
    )


def crossing_direction(previous: Point, current: Point, line: tuple[Point, Point]) -> str | None:
    if not segments_intersect(previous, current, line[0], line[1]):
        return None
    dx = current[0] - previous[0]
    dy = current[1] - previous[1]
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def _signed_area(points: tuple[Point, ...]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_on_segment(p: Point, a: Point, b: Point) -> bool:
    return (
        isclose(_orientation(a, b, p), 0.0, abs_tol=EPSILON)
        and min(a[0], b[0]) - EPSILON <= p[0] <= max(a[0], b[0]) + EPSILON
        and min(a[1], b[1]) - EPSILON <= p[1] <= max(a[1], b[1]) + EPSILON
    )
