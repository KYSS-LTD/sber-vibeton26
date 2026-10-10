import os
import numpy as np
import pytest
from app.vision import Engine, VisionError

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        os.getenv("TEST_MODELS") != "1",
        reason="Set TEST_MODELS=1 with installed model volume",
    ),
]


def test_actual_onnx_models_load_and_empty_frames_fail_closed():
    engine = Engine()
    if os.getenv("VISION_DEVICE") == "cuda":
        assert "CUDAExecutionProvider" in engine.yolo.get_providers()
        assert "CUDAExecutionProvider" in engine.pad.get_providers()
    assert engine.pad.get_inputs()[0].shape == [1, 3, 80, 80]
    image = np.zeros((480, 640, 3), np.uint8)
    assert engine.yolo_boxes(image) == []
    with pytest.raises(VisionError, match="одно лицо"):
        engine.detect(image)
