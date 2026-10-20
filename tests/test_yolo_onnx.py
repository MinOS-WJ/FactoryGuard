import numpy as np
import pytest

from factoryguard.inference.yolo_onnx import _decode


def test_decode_yolo_xywh_output_to_detection():
    output = np.zeros((1, 84, 2), dtype=np.float32)
    output[0, :4, 0] = (320, 320, 100, 160)
    output[0, 4, 0] = 0.9  # person
    output[0, :4, 1] = (320, 320, 100, 160)
    output[0, 5, 1] = 0.8  # bicycle, should be retained as a separate class
    detections = _decode(output, (640, 640), 1.0, (0.0, 0.0), tuple(["person", "bicycle"] + ["x"] * 78), 0.25, 0.45)
    assert [d.label for d in detections] == ["person", "bicycle"]
    assert detections[0].bbox == (270.0, 240.0, 370.0, 400.0)


def test_decode_accepts_anchor_major_output_without_small_dimension_heuristic():
    output = np.zeros((1, 2, 84), dtype=np.float32)
    output[0, 0, :4] = (320, 320, 100, 160)
    output[0, 0, 4] = 0.9
    detections = _decode(output, (640, 640), 1.0, (0.0, 0.0), tuple(["person"] + ["x"] * 79), 0.25, 0.45)
    assert len(detections) == 1
    assert detections[0].label == "person"


def test_decode_rejects_malformed_output():
    with pytest.raises(ValueError, match="unexpected YOLO output shape"):
        _decode(np.zeros((84, 2), dtype=np.float32), (640, 640), 1.0, (0.0, 0.0), tuple(["person"] + ["x"] * 79), 0.25, 0.45)
