import pytest

from factoryguard.domain.geometry import VisualRuleRemoved, point_in_polygon


def test_visual_geometry_is_disabled():
    with pytest.raises(VisualRuleRemoved, match="画面几何规则已移除"):
        point_in_polygon((0, 0), ((0, 0), (1, 0), (1, 1)))
