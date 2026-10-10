import concurrent.futures
from unittest.mock import Mock
import numpy as np
import pytest
from app.vision import Engine, VisionError


def bare_engine(future):
    engine = Engine.__new__(Engine)
    engine.busy = None
    engine.pool = Mock()
    engine.pool.submit.return_value = future
    engine.yunet = Mock()
    engine.yunet.detect.return_value = (
        None,
        np.asarray(
            [[10, 10, 100, 100, 30, 40, 70, 40, 50, 60, 35, 80, 65, 80, 0.99]],
            np.float32,
        ),
    )
    engine.yolo_boxes = Mock()
    return engine


def test_yolo_timeout_uses_yunet_with_fifteen_second_deadline():
    future = Mock()
    future.result.side_effect = concurrent.futures.TimeoutError()
    engine = bare_engine(future)
    face, backend = engine.detect(np.zeros((200, 200, 3), np.uint8))
    future.result.assert_called_once_with(timeout=15)
    assert backend == "yunet" and face.shape == (15,)


def test_multiple_faces_never_trigger_weaker_detector_fallback():
    future = Mock()
    future.result.return_value = [[0, 0, 100, 100], [100, 0, 100, 100]]
    engine = bare_engine(future)
    with pytest.raises(VisionError):
        engine.detect(np.zeros((200, 200, 3), np.uint8))
    engine.yunet.detect.assert_not_called()


def test_spoof_or_wrong_face_does_not_run_acceptance_fallback():
    engine = Engine.__new__(Engine)
    assert engine.verify([], [], ["left", "right"])["passed"] is False
