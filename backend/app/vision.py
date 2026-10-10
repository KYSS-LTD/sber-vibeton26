"""YOLO/MiniFASNet on CUDA or CPU; YuNet landmarks and SFace identity on CPU."""

import concurrent.futures
import threading
import time
import os
from collections import Counter
import cv2
import numpy as np
import onnxruntime as ort
from .config import MODEL_DIR, FACE_THRESHOLD, PAD_THRESHOLD

_lock = threading.Lock()
_engine = None

MIN_FRAMES = 8
MAX_FRAMES = 32
# Frames without a usable face (a phone in front of the face, motion blur) are
# skipped; the decision is taken on the median of at least this many good ones.
MIN_GOOD_FRAMES = 3
MIN_FACE_SIZE = 80
YOLO_CONFIDENCE = 0.35

# Statistics of the face patch (grey, PATCH x PATCH). Frames outside the
# MIN/MAX limits are rejected; dim, glaring or flat frames inside them are
# re-exposed towards TARGET_* before recognition instead of being rejected.
PATCH = 112
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 25, 245
MIN_CONTRAST = 10
MIN_SHARPNESS = 0.003  # variance of the Laplacian relative to contrast squared
DIM, GLARE, LOW_CONTRAST = 85, 190, 38
TARGET_BRIGHTNESS, TARGET_CONTRAST = 115, 55


class VisionError(ValueError):
    pass


def decode_image(content):
    if len(content) > 4_000_000:
        raise VisionError("Снимок слишком большой")
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] * image.shape[1] > 12_000_000:
        raise VisionError("Некорректное изображение")
    return image


def same_face(box, face):
    bx, by, bw, bh = box
    fx, fy, fw, fh = face[:4]
    return (
        0.5 <= bw / max(fw, 1) <= 2.0
        and bx - bw * 0.2 <= fx + fw / 2 <= bx + bw * 1.2
        and by - bh * 0.2 <= fy + fh / 2 <= by + bh * 1.2
    )


class Engine:
    def __init__(self):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        cuda = os.getenv("VISION_DEVICE", "cpu") == "cuda"
        if cuda:
            ort.preload_dlls(directory="")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if cuda else ["CPUExecutionProvider"]
        self.yolo = ort.InferenceSession(
            str(MODEL_DIR / "yolov8n-face.onnx"),
            sess_options=opts,
            providers=providers,
        )
        self.pad = ort.InferenceSession(
            str(MODEL_DIR / "minifas.onnx"),
            sess_options=opts,
            providers=providers,
        )
        if cuda and any("CUDAExecutionProvider" not in session.get_providers() for session in (self.yolo, self.pad)):
            raise RuntimeError("CUDA requested but the inference provider could not load")
        self.yunet = cv2.FaceDetectorYN.create(
            str(MODEL_DIR / "yunet.onnx"), "", (320, 320), 0.7, 0.3, 5000
        )
        self.sface = cv2.FaceRecognizerSF.create(str(MODEL_DIR / "sface.onnx"), "")
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.busy = None
        self.inference_lock = threading.Lock()

    def yolo_boxes(self, image):
        h, w = image.shape[:2]
        ratio = 640 / max(h, w)
        nh, nw = round(h * ratio), round(w * ratio)
        canvas = np.full((640, 640, 3), 114, np.uint8)
        y = (640 - nh) // 2
        x = (640 - nw) // 2
        canvas[y : y + nh, x : x + nw] = cv2.resize(image, (nw, nh))
        tensor = (
            cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
            .transpose(2, 0, 1)[None]
            .astype(np.float32)
            / 255
        )
        output = self.yolo.run(None, {self.yolo.get_inputs()[0].name: tensor})[0][0]
        if output.shape[0] < output.shape[1]:
            output = output.T
        boxes = []
        scores = []
        for row in output:
            confidence = float(row[4])
            if confidence < YOLO_CONFIDENCE:
                continue
            cx, cy, bw, bh = row[:4]
            boxes.append(
                [
                    (cx - bw / 2 - x) / ratio,
                    (cy - bh / 2 - y) / ratio,
                    bw / ratio,
                    bh / ratio,
                ]
            )
            scores.append(confidence)
        ids = cv2.dnn.NMSBoxes(boxes, scores, YOLO_CONFIDENCE, 0.45)
        return [boxes[int(i)] for i in np.asarray(ids).reshape(-1)]

    def detect(self, image, use_yolo=True, dominant=False):
        """Return the largest (closest) face; other faces in the frame are ignored.

        With dominant=True (reference photos) a second face of comparable size is
        an error, because it is unclear whose reference is being enrolled.
        """
        boxes = None
        if use_yolo:
            # Never queue an unbounded series behind a stalled detector.
            if self.busy is None or self.busy.done():
                self.busy = self.pool.submit(self.yolo_boxes, image)
                try:
                    boxes = self.busy.result(timeout=15)
                except concurrent.futures.TimeoutError:
                    pass
                except Exception:
                    pass
            if boxes is not None and len(boxes) == 0:
                raise VisionError("Лицо в кадре не найдено")
        self.yunet.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self.yunet.detect(image)
        if faces is None or len(faces) == 0:
            raise VisionError("Лицо в кадре не найдено")
        order = sorted(
            range(len(faces)), key=lambda i: faces[i][2] * faces[i][3], reverse=True
        )
        if boxes:
            # Both detectors must see the chosen face.
            order = [
                i for i in order if any(same_face(box, faces[i]) for box in boxes)
            ]
            if not order:
                raise VisionError("Детекторы обнаружили разные лица")
        face = faces[order[0]]
        if min(face[2], face[3]) < MIN_FACE_SIZE:
            raise VisionError("Подойдите ближе к камере")
        if dominant and any(
            faces[i][2] * faces[i][3] > face[2] * face[3] / 2
            for i in range(len(faces))
            if i != order[0]
        ):
            raise VisionError("На снимке несколько лиц одного размера")
        return face, "yolo" if boxes else "yunet"

    def template(self, image, face):
        crop = self.sface.alignCrop(image, face)
        value = self.sface.feature(crop).reshape(-1)
        return value / np.linalg.norm(value)

    def quality(self, image, face):
        height, width = image.shape[:2]
        x, y, w, h = face[:4]
        left, top = max(0, int(x)), max(0, int(y))
        right, bottom = min(width, int(x + w)), min(height, int(y + h))
        if right - left < 8 or bottom - top < 8:
            raise VisionError("Лицо в кадре не найдено")
        gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        # A fixed patch size makes the metrics independent of the face size.
        patch = cv2.resize(
            gray,
            (PATCH, PATCH),
            interpolation=cv2.INTER_AREA if min(gray.shape) > PATCH else cv2.INTER_CUBIC,
        )
        brightness = float(patch.mean())
        contrast = float(patch.std())
        sharpness = float(cv2.Laplacian(patch, cv2.CV_64F).var()) / max(
            contrast * contrast, 1.0
        )
        if brightness < MIN_BRIGHTNESS:
            raise VisionError("Слишком темно: добавьте освещения")
        if brightness > MAX_BRIGHTNESS:
            raise VisionError("Слишком светло: уберите засветку")
        if contrast < MIN_CONTRAST:
            raise VisionError("Слишком низкий контраст изображения")
        if sharpness < MIN_SHARPNESS:
            raise VisionError("Снимок размыт: лицо не в фокусе")
        return {
            "sharpness": sharpness,
            "brightness": brightness,
            "contrast": contrast,
            "face_width": int(w),
        }

    def prepare(self, image, quality):
        """Re-expose dim, glaring or flat frames; normal ones are returned as is."""
        brightness, contrast = quality["brightness"], quality["contrast"]
        if DIM <= brightness <= GLARE and contrast >= LOW_CONTRAST:
            return image
        gain = min(max(TARGET_CONTRAST / contrast, 1.0), 3.0)
        levels = (np.arange(256) - brightness) * gain + TARGET_BRIGHTNESS
        return cv2.LUT(image, np.clip(levels, 0, 255).astype(np.uint8))

    def spoof_score(self, image, face):
        x, y, w, h = face[:4]
        cx = x + w / 2
        cy = y + h / 2
        scale = min(2.7, (image.shape[1] - 1) / w, (image.shape[0] - 1) / h)
        bw = w * scale
        bh = h * scale
        left = int(max(0, min(cx - bw / 2, image.shape[1] - bw)))
        top = int(max(0, min(cy - bh / 2, image.shape[0] - bh)))
        crop = image[top : int(top + bh), left : int(left + bw)]
        tensor = cv2.resize(crop, (80, 80)).transpose(2, 0, 1)[None].astype(np.float32)
        out = self.pad.run(None, {self.pad.get_inputs()[0].name: tensor})[0].reshape(-1)
        probs = np.exp(out - out.max())
        probs /= probs.sum()
        return float(probs[1])

    def enroll(self, contents):
        if len(contents) != 3:
            raise VisionError("Нужны ровно три снимка")
        templates = []
        qualities = []
        fingerprints = []
        with self.inference_lock:
            for content in contents:
                image = decode_image(content)
                face, _ = self.detect(image, dominant=True)
                quality = self.quality(image, face)
                image = self.prepare(image, quality)
                qualities.append(quality)
                templates.append(self.template(image, face))
                aligned = self.sface.alignCrop(image, face)
                gray = cv2.resize(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), (9, 8))
                fingerprints.append(gray[:, 1:] > gray[:, :-1])
        # Slots are frontal/left/right, so the left-vs-right pair legitimately
        # diverges the most; require agreement on at least 2 of the 3 pairs,
        # which still ties all three photos to one person.
        matches = 0
        for i in range(3):
            for j in range(i):
                similarity = float(np.dot(templates[i], templates[j]))
                if (
                    similarity > 0.9999
                    or np.count_nonzero(fingerprints[i] != fingerprints[j]) <= 2
                ):
                    raise VisionError("Нужны три разных снимка")
                if similarity >= FACE_THRESHOLD:
                    matches += 1
        if matches < 2:
            raise VisionError("Эталоны принадлежат разным людям")
        return [t.tolist() for t in templates], qualities

    def verify(self, contents, references):
        """Frontal-only check: identity and liveness on the median of good frames."""
        if not MIN_FRAMES <= len(contents) <= MAX_FRAMES:
            return {
                "passed": False,
                "reason": "Недостаточно кадров для проверки",
            }
        references = np.asarray(references, np.float32)
        accepted = []
        skipped = Counter()
        started = time.monotonic()
        with self.inference_lock:
            for index, content in enumerate(contents):
                if time.monotonic() - started > 20:
                    return {
                        "passed": False,
                        "reason": "Проверка лица превысила допустимое время",
                    }
                try:
                    image = decode_image(content)
                    face, backend = self.detect(image, use_yolo=not accepted)
                    quality = self.quality(image, face)
                    template = self.template(self.prepare(image, quality), face)
                    pad = self.spoof_score(image, face)
                except VisionError as error:
                    skipped[str(error)] += 1
                    continue
                accepted.append(
                    {
                        "frame": index,
                        "backend": backend,
                        # Best of the references: they are not all frontal.
                        "score": float(np.max(references @ template)),
                        "pad": pad,
                        "sharpness": quality["sharpness"],
                    }
                )
        if len(accepted) < MIN_GOOD_FRAMES:
            reason = (
                skipped.most_common(1)[0][0] if skipped else "Лицо в кадре не найдено"
            )
            return {"passed": False, "reason": reason, "frames": len(accepted)}
        score = float(np.median([f["score"] for f in accepted]))
        pad = float(np.median([f["pad"] for f in accepted]))
        # The sharpest verified frame is kept as the evidence photo.
        outcome = {
            "score": score,
            "pad": pad,
            "frames": len(accepted),
            "frame": max(accepted, key=lambda f: f["sharpness"])["frame"],
        }
        if score < FACE_THRESHOLD:
            return {
                "passed": False,
                "reason": "Лицо не совпало с эталонами",
                **outcome,
            }
        if pad < PAD_THRESHOLD:
            return {
                "passed": False,
                "reason": "Не пройдена проверка живости",
                **outcome,
            }
        return {
            "passed": True,
            **outcome,
            "detector": accepted[0]["backend"],
            "model": "sface-2021dec",
            "threshold": FACE_THRESHOLD,
        }


def engine():
    global _engine
    with _lock:
        if _engine is None:
            _engine = Engine()
    return _engine
