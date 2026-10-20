import pytest

from factoryguard.domain.models import Detection, Rule, RuleType


def test_detection_rejects_invalid_confidence_and_bbox():
    with pytest.raises(ValueError, match="confidence"):
        Detection("person", 1.1, (0, 0, 1, 1))
    with pytest.raises(ValueError, match="bbox"):
        Detection("person", 0.9, (5, 0, 1, 1))


def test_rule_rejects_invalid_geometry_and_direction():
    with pytest.raises(ValueError, match="self-intersecting"):
        Rule("r1", "CH01", "bad", RuleType.POLYGON, ((0, 0), (10, 10), (0, 10), (10, 0)))
    with pytest.raises(ValueError, match="direction"):
        Rule("r2", "CH01", "bad", RuleType.LINE, ((0, 0), (10, 0)), direction="sideways")
