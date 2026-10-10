import concurrent.futures
import threading
from unittest.mock import Mock
import cv2
import numpy as np
import pytest
from app import vision
from app.vision import Engine, VisionError


def yunet_row(x, y, size):
    # x, y, w, h, five landmarks (eyes, nose, mouth corners), score
    return [x, y, size, size, x + size * 0.3, y + size * 0.4, x + size * 0.7,
            y + size * 0.4, x + size * 0.5, y + size * 0.6, x + size * 0.35,
            y + size * 0.8, x + size * 0.65, y + size * 0.8, 0.99]


def bare_engine(future, *faces):
    engine = Engine.__new__(Engine)
    engine.busy = None
    engine.pool = Mock()
    engine.pool.submit.return_value = future
    engine.yunet = Mock()
    engine.yunet.detect.return_value = (None, np.asarray(faces, np.float32))
    engine.yolo_boxes = Mock()
    return engine


def yolo_result(boxes):
    future = Mock()
    future.result.return_value = boxes
    return future


FRAME = np.zeros((600, 800, 3), np.uint8)


def test_yolo_timeout_uses_yunet_with_fifteen_second_deadline():
    future = Mock()
    future.result.side_effect = concurrent.futures.TimeoutError()
    engine = bare_engine(future, yunet_row(10, 10, 100))
    face, backend = engine.detect(FRAME)
    future.result.assert_called_once_with(timeout=15)
    assert backend == "yunet" and face.shape == (15,)


def test_several_faces_use_the_largest_one():
    engine = bare_engine(
        yolo_result([[300, 20, 90, 90], [10, 10, 150, 150]]),
        yunet_row(300, 20, 90),
        yunet_row(10, 10, 150),
    )
    face, backend = engine.detect(FRAME)
    assert face[2] == 150 and backend == "yolo"


def test_yolo_without_a_face_is_not_rescued_by_the_weaker_detector():
    engine = bare_engine(yolo_result([]), yunet_row(10, 10, 100))
    with pytest.raises(VisionError):
        engine.detect(FRAME)
    engine.yunet.detect.assert_not_called()


def test_both_detectors_must_see_the_chosen_face():
    engine = bare_engine(yolo_result([[600, 400, 100, 100]]), yunet_row(10, 10, 100))
    with pytest.raises(VisionError, match="разные лица"):
        engine.detect(FRAME)


def test_far_away_face_asks_to_come_closer():
    engine = bare_engine(yolo_result([[10, 10, 60, 60]]), yunet_row(10, 10, 60))
    with pytest.raises(VisionError, match="ближе"):
        engine.detect(FRAME)


def test_reference_photo_with_two_similar_faces_is_ambiguous():
    boxes = [[10, 10, 150, 150], [300, 20, 140, 140]]
    similar = bare_engine(
        yolo_result(boxes), yunet_row(10, 10, 150), yunet_row(300, 20, 140)
    )
    with pytest.raises(VisionError, match="несколько лиц"):
        similar.detect(FRAME, dominant=True)
    assert similar.detect(FRAME)[0][2] == 150
    background = bare_engine(
        yolo_result(boxes[:1]), yunet_row(10, 10, 150), yunet_row(300, 20, 80)
    )
    assert background.detect(FRAME, dominant=True)[0][2] == 150


def textured(seed=0, blur=0.0, gain=1.0, offset=0.0):
    noise = np.random.default_rng(seed).normal(0, 1, (240, 240)).astype(np.float32)
    noise = cv2.GaussianBlur(noise, (0, 0), 1.2)
    noise /= noise.std()
    if blur:
        noise = cv2.GaussianBlur(noise, (0, 0), blur)
        noise /= noise.std()
    gray = np.clip(noise * 50 * gain + offset, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


FACE_BOX = np.asarray(yunet_row(20, 20, 200), np.float32)


def test_quality_accepts_normal_and_soft_frames_and_rejects_unusable_ones():
    engine = Engine.__new__(Engine)
    engine.quality(textured(offset=120), FACE_BOX)
    engine.quality(textured(blur=0.8, offset=120), FACE_BOX)
    engine.quality(textured(gain=0.6, offset=60), FACE_BOX)
    with pytest.raises(VisionError, match="темно"):
        engine.quality(textured(gain=0.1, offset=8), FACE_BOX)
    with pytest.raises(VisionError, match="светло"):
        engine.quality(np.full((240, 240, 3), 252, np.uint8), FACE_BOX)
    with pytest.raises(VisionError, match="контраст"):
        engine.quality(np.full((240, 240, 3), 120, np.uint8), FACE_BOX)
    with pytest.raises(VisionError, match="размыт"):
        engine.quality(textured(blur=30, offset=120), FACE_BOX)


def test_quality_does_not_depend_on_the_face_size():
    engine = Engine.__new__(Engine)
    small = textured(seed=1, offset=120)
    large = cv2.resize(small, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    box_small = np.asarray(yunet_row(10, 10, 220), np.float32)
    box_large = np.asarray(yunet_row(30, 30, 660), np.float32)
    a = engine.quality(small, box_small)["sharpness"]
    b = engine.quality(large, box_large)["sharpness"]
    assert 0.25 < a / b < 4


def test_dim_and_flat_frames_are_reexposed_but_normal_ones_are_untouched():
    engine = Engine.__new__(Engine)
    normal = textured(offset=120)
    assert engine.prepare(normal, {"brightness": 120.0, "contrast": 55.0}) is normal
    dim = textured(gain=0.4, offset=45)
    quality = engine.quality(dim, FACE_BOX)
    fixed = engine.prepare(dim, quality)
    assert fixed.mean() > dim.mean() + 30 and fixed.std() > dim.std() * 1.8


def scripted_engine(monkeypatch, frames):
    """frames[i] is a VisionError or (similarity to the first reference, liveness)."""
    engine = Engine.__new__(Engine)
    engine.inference_lock = threading.Lock()
    monkeypatch.setattr(vision, "decode_image", lambda content: content[0])

    def detect(index, use_yolo=True):
        if isinstance(frames[index], VisionError):
            raise frames[index]
        return np.zeros(15, np.float32), "yolo"

    def template(index, face):
        similarity = frames[index][0]
        value = np.zeros(128, np.float32)
        value[0] = similarity
        value[1] = (1 - similarity**2) ** 0.5
        return value

    engine.detect = detect
    engine.quality = lambda index, face: {"sharpness": 1.0 + index}
    engine.prepare = lambda index, quality: index
    engine.template = template
    engine.spoof_score = lambda index, face: frames[index][1]
    return engine


REFERENCES = [np.eye(128, dtype=np.float32)[i].tolist() for i in (0, 2, 3)]


def run(monkeypatch, frames):
    engine = scripted_engine(monkeypatch, frames)
    return engine.verify([bytes([i]) for i in range(len(frames))], REFERENCES)


def test_straight_look_passes_without_any_head_turn(monkeypatch):
    missing = VisionError("Лицо в кадре не найдено")
    result = run(monkeypatch, [missing] * 5 + [(0.7, 0.95)] * 11)
    assert result["passed"] is True
    assert result["frames"] == 11 and result["frame"] == 15


def test_only_the_best_reference_has_to_match(monkeypatch):
    # References two and three are orthogonal to the probe, like turned heads.
    assert run(monkeypatch, [(0.6, 0.95)] * 8)["passed"] is True


def test_a_few_bad_frames_do_not_fail_the_passage(monkeypatch):
    frames = [(0.7, 0.95)] * 10 + [(0.1, 0.2)] * 2
    assert run(monkeypatch, frames)["passed"] is True


def test_three_good_frames_are_required_and_the_reason_is_reported(monkeypatch):
    dark = VisionError("Слишком темно: добавьте освещения")
    result = run(monkeypatch, [dark] * 6 + [(0.7, 0.95)] * 2)
    assert result["passed"] is False and result["reason"] == str(dark)


def test_wrong_person_and_spoof_are_rejected(monkeypatch):
    wrong = run(monkeypatch, [(0.2, 0.95)] * 8)
    assert wrong["passed"] is False and "не совпало" in wrong["reason"]
    spoof = run(monkeypatch, [(0.7, 0.3)] * 8)
    assert spoof["passed"] is False and "живости" in spoof["reason"]


def test_too_few_frames_fail_closed():
    assert Engine.__new__(Engine).verify([], [])["passed"] is False
