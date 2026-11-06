from factoryguard.domain import models


def test_visual_detection_models_are_removed():
    for name in ("Detection", "Frame", "RuleType", "RuleMode"):
        assert not hasattr(models, name)
    assert models.Rule is models.NativeEventRule
