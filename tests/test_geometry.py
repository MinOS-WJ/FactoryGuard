from factoryguard.domain.geometry import (
    crossing_direction,
    point_in_polygon,
    polygon_is_valid,
)


def test_polygon_contains_anchor_and_rejects_self_intersection():
    square = ((0, 0), (10, 0), (10, 10), (0, 10))
    assert polygon_is_valid(square)
    assert point_in_polygon((5, 5), square)
    assert not point_in_polygon((15, 5), square)
    assert not polygon_is_valid(((0, 0), (10, 10), (0, 10), (10, 0)))


def test_line_crossing_direction():
    assert crossing_direction((5, -1), (5, 1), ((0, 0), (10, 0))) == "down"
    assert crossing_direction((5, -1), (5, -2), ((0, 0), (10, 0))) is None
