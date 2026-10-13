import threading
import cv2
import numpy as np
from django.conf import settings

_lock = threading.Lock()
_det = None
_rec = None


class FaceError(Exception):
    pass


def _load():
    global _det, _rec
    if _det is None:
        _det = cv2.FaceDetectorYN.create(
            str(settings.MODELS_DIR / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.8, 0.3, 5000)
        _rec = cv2.FaceRecognizerSF.create(
            str(settings.MODELS_DIR / "face_recognition_sface_2021dec.onnx"), "")


def embed_with_box(image_bytes: bytes):
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FaceError("Invalid image")
    h, w = img.shape[:2]
    scale = 1280 / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
        h, w = img.shape[:2]

    with _lock:
        _load()
        _det.setInputSize((w, h))
        _, faces = _det.detect(img)
        if faces is None or len(faces) == 0:
            raise FaceError("No face detected")
        face = max(faces, key=lambda f: f[2] * f[3])   # largest face
        if face[2] < 60 or face[3] < 60:
            raise FaceError("Face too small")
        aligned = _rec.alignCrop(img, face)
        feat = _rec.feature(aligned)

    v = feat.flatten().astype(np.float32)
    box = [float(face[0]) / w, float(face[1]) / h, float(face[2]) / w, float(face[3]) / h]  # x, y, w, h as 0..1
    return v / np.linalg.norm(v), box


def embed(image_bytes: bytes) -> np.ndarray:
    return embed_with_box(image_bytes)[0]