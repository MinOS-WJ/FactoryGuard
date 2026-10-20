from __future__ import annotations

import numpy as np
import pytest

from factoryguard.domain.models import Detection
from factoryguard.inference.base import InferenceHealth
from factoryguard.inference.remote import CentralInferenceClient
from factoryguard.inference.server import CentralInferenceServer


class _FakeEngine:
    def health_check(self):
        return InferenceHealth("ready", "fake-gpu", model_version="yolo-test")

    def infer_image(self, image):
        assert image.shape == (32, 48, 3)
        return (Detection("person", 0.91, (4.0, 5.0, 20.0, 30.0), "track-1"),)


def test_central_inference_client_and_server_round_trip():
    server = CentralInferenceServer(_FakeEngine(), host="127.0.0.1", port=0, api_key="test-key")
    server.start()
    try:
        client = CentralInferenceClient(server.url, api_key="test-key", timeout=5)
        health = client.health_check()
        detections = client.infer_image(np.zeros((32, 48, 3), dtype=np.uint8), stream_id="CH01")
    finally:
        server.stop()

    assert health.state == "ready"
    assert health.provider == "fake-gpu"
    assert health.model_version == "yolo-test"
    assert len(detections) == 1
    assert detections[0].label == "person"
    assert detections[0].track_id == "track-1"


def test_central_inference_server_rejects_wrong_api_key():
    server = CentralInferenceServer(_FakeEngine(), host="127.0.0.1", port=0, api_key="test-key")
    server.start()
    try:
        client = CentralInferenceClient(server.url, api_key="wrong-key", timeout=5)
        with pytest.raises(RuntimeError, match="401"):
            client.infer_image(np.zeros((32, 48, 3), dtype=np.uint8))
    finally:
        server.stop()
