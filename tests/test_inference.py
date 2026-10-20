from datetime import UTC, datetime

from factoryguard.domain.models import Frame
from factoryguard.inference.base import cpu_health
from factoryguard.inference.mock import MockInferenceEngine


def test_cpu_health_never_selects_gpu():
    health = cpu_health()
    assert health.provider == "cpu"
    assert health.state == "degraded"


def test_mock_engine_is_usable_without_camera_or_model():
    frame = Frame("CH01", 1, datetime(2026, 1, 1, tzinfo=UTC), 640, 480)
    engine = MockInferenceEngine()
    assert engine.infer(frame) == ()
    assert engine.health_check().provider == "mock"
