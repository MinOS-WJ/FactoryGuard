import pytest

from factoryguard.domain.condition_tree import VisualRuleRemoved, normalize_condition_nodes


def test_visual_condition_tree_is_disabled():
    with pytest.raises(VisualRuleRemoved, match="画面条件规则已移除"):
        normalize_condition_nodes(())
