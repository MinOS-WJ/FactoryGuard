import pytest

from factoryguard.domain.rules import RuleEvaluator, VisualRuleRemoved


def test_visual_rule_evaluator_is_disabled():
    with pytest.raises(VisualRuleRemoved, match="画面规则评估已移除"):
        RuleEvaluator([])
