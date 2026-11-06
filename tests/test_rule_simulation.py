import pytest

from factoryguard.domain.rule_simulation import VisualRuleRemoved, simulate_rule


def test_visual_rule_simulation_is_disabled():
    with pytest.raises(VisualRuleRemoved, match="画面规则模拟已移除"):
        simulate_rule(object())
