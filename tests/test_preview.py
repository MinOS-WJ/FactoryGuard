from __future__ import annotations

import pytest

preview_module = pytest.importorskip("factoryguard.ui.preview")


def test_preview_widget_is_camera_only_and_has_no_detection_overlay_api():
    assert not hasattr(preview_module.PreviewWidget, "set_frame")
    assert not hasattr(preview_module.PreviewWidget, "set_image_file")
    assert not hasattr(preview_module, "save_frame_image")
    assert callable(preview_module.numpy_to_qimage)
