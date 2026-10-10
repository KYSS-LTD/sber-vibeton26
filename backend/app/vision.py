"""CPU inference: YOLO detector, YuNet landmarks, SFace identity, MiniFASNet PAD."""

import concurrent.futures
import threading
import time
import cv2
import numpy as np
import onnxruntime as ort
from .config import MODEL_DIR, FACE_THRESHOLD

_lock = threading.Lock()
_engine = None


class VisionError(ValueError):
    pass


def decode_image(content):
    if len(content) > 4_000_000:
        raise VisionError("Снимок слишком большой")
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] * image.shape[1] > 12_000_000:
        raise VisionError("Некорректное изображение")
    return image


class Engine:
    def __init__(self):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.yolo = ort.InferenceSession(
            str(MODEL_DIR / "yolov8n-face.onnx"),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.pad = ort.InferenceSession(
            str(MODEL_DIR / "minifas.onnx"),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.yunet = cv2.FaceDetectorYN.create(
            str(MODEL_DIR / "yunet.onnx"), "", (320, 320), 0.8, 0.3, 5000
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
            if confidence < 0.5:
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
        ids = cv2.dnn.NMSBoxes(boxes, scores, 0.5, 0.45)
        return [boxes[int(i)] for i in np.asarray(ids).reshape(-1)]

    def detect(self, image, use_yolo=True):
        backend = "yunet"
        if use_yolo:
            # Never queue an unbounded series behind a stalled detector.
            if self.busy is None or self.busy.done():
                self.busy = self.pool.submit(self.yolo_boxes, image)
                try:
                    boxes = self.busy.result(timeout=15)
                    if len(boxes) != 1:
                        raise VisionError("В кадре должно быть ровно одно лицо")
                    backend = "yolo"
                except concurrent.futures.TimeoutError:
                    pass
                except VisionError:
                    raise
                except Exception:
                    pass
        self.yunet.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self.yunet.detect(image)
        if faces is None or len(faces) != 1:
            raise VisionError("В кадре должно быть ровно одно лицо")
        face = faces[0]
        if min(face[2], face[3]) < 80:
            raise VisionError("Подойдите ближе к камере")
        if backend == "yolo":
            bx, by, bw, bh = boxes[0]
            fx, fy, fw, fh = face[:4]
            if not (
                bx - bw * 0.2 <= fx + fw / 2 <= bx + bw * 1.2
                and by - bh * 0.2 <= fy + fh / 2 <= by + bh * 1.2
            ):
                raise VisionError("Детекторы обнаружили разные лица")
        return face, backend

    def template(self, image, face):
        crop = self.sface.alignCrop(image, face)
        value = self.sface.feature(crop).reshape(-1)
        return value / np.linalg.norm(value)

    def quality(self, image, face):
        x, y, w, h = map(int, face[:4])
        crop = image[max(0, y) : y + h, max(0, x) : x + w]
        blur = float(
            cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        )
        brightness = float(crop.mean())
        if blur < 30 or not 35 < brightness < 225:
            raise VisionError("Недостаточная резкость или освещение")
        return {"blur": blur, "brightness": brightness, "face_width": w}

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
                face, _ = self.detect(image)
                qualities.append(self.quality(image, face))
                templates.append(self.template(image, face))
                aligned = self.sface.alignCrop(image, face)
                gray = cv2.resize(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), (9, 8))
                fingerprints.append(gray[:, 1:] > gray[:, :-1])
        for i in range(3):
            for j in range(i):
                if float(np.dot(templates[i], templates[j])) < FACE_THRESHOLD:
                    raise VisionError("Эталоны принадлежат разным людям")
                if (
                    float(np.dot(templates[i], templates[j])) > 0.9999
                    or np.count_nonzero(fingerprints[i] != fingerprints[j]) <= 2
                ):
                    raise VisionError("Нужны три разных снимка")
        return [t.tolist() for t in templates], qualities

    def verify(self, contents, references, actions):
        if not 8 <= len(contents) <= 32:
            return {
                "passed": False,
                "reason": "Недостаточно кадров для проверки живости",
            }
        scores = []
        pad_scores = []
        poses = []
        backends = []
        started = time.monotonic()
        with self.inference_lock:
            for content in contents:
                if time.monotonic() - started > 20:
                    return {
                        "passed": False,
                        "reason": "Проверка лица превысила допустимое время",
                    }
                image = decode_image(content)
                face, backend = self.detect(image, use_yolo=not backends)
                self.quality(image, face)
                template = self.template(image, face)
                similarities = sorted(
                    float(np.dot(template, np.asarray(ref, np.float32)))
                    for ref in references
                )
                scores.append(similarities[1])
                pad_scores.append(self.spoof_score(image, face))
                backends.append(backend)
                # Five YuNet landmarks: right eye, left eye, nose, mouth corners.
                eye_mid = (face[4] + face[6]) / 2
                eye_distance = max(abs(face[6] - face[4]), 1)
                poses.append(float((face[8] - eye_mid) / eye_distance))
        if min(scores) < FACE_THRESHOLD:
            return {
                "passed": False,
                "reason": "Лицо не совпало минимум с двумя эталонами",
                "score": min(scores),
            }
        if min(pad_scores) < 0.8:
            return {
                "passed": False,
                "reason": "Не пройдена проверка живости",
                "pad": min(pad_scores),
            }
        # Random order, neutral -> first head turn -> opposite head turn -> neutral.
        baseline = poses[0]
        first = "left" if actions[0] == "left" else "right"
        # The camera is not mirrored: the employee's left is the image's right.
        signs = [1, -1] if first == "left" else [-1, 1]
        index = 1
        for sign in signs:
            while index < len(poses) and (poses[index] - baseline) * sign < 0.18:
                index += 1
            if index >= len(poses):
                return {
                    "passed": False,
                    "reason": "Не выполнен случайный порядок поворотов головы",
                }
            index += 1
        if abs(poses[-1] - baseline) > 0.15:
            return {"passed": False, "reason": "Вернитесь лицом к камере"}
        return {
            "passed": True,
            "score": min(scores),
            "pad": min(pad_scores),
            "detector": backends[0],
            "model": "sface-2021dec",
            "threshold": FACE_THRESHOLD,
        }


def engine():
    global _engine
    with _lock:
        if _engine is None:
            _engine = Engine()
    return _engine
