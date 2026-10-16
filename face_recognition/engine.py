import threading
import cv2
import numpy as np
from django.conf import settings


_local = threading.local()


class FaceError(Exception):
    pass


def check_anti_spoofing(crop_bgr: np.ndarray, face_box_norm: list) -> tuple[bool, str]:
    """
    Multi-factor screen and presentation attack detector.
    Tolerates natural movement and motion blur while catching phone screens.
    """
    if crop_bgr.size == 0:
        return True, ""

    h, w = crop_bgr.shape[:2]

    # Test 1: Unnatural zoom / proximity check
    # Increased tolerance to 0.82 so natural leaning into camera doesn't trip it
    _, _, fw_norm, fh_norm = face_box_norm
    if fw_norm > 0.82 or fh_norm > 0.85:
        return False, "Face unnaturally close to camera (zoom attack detected)"

    # Test 2: Color Space & Backlight Chrominance Check (YCbCr & HSV)
    ycbcr = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2YCrCb)
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    
    Cr = ycbcr[:, :, 1]
    Cb = ycbcr[:, :, 2]
    S = hsv[:, :, 1]  # Saturation
    V = hsv[:, :, 2]  # Value / Brightness

    cr_std = float(np.std(Cr))
    cb_std = float(np.std(Cb))
    
    # Relaxed standard deviation threshold (from 4.5 to 3.0) to prevent lighting shift false positives
    if cr_std < 3.0 or cb_std < 3.0:
        return False, "Digital screen color gamut detected (uniform chroma)"

    # Test 3: Specular Glare & Backlight Saturation
    glare_pixels = np.sum((V > 250) & (S < 30))
    glare_ratio = float(glare_pixels) / float(h * w)
    if glare_ratio > 0.10:  # Raised threshold from 0.06 to 0.10 for office light reflections
        return False, "Screen backlight reflection / glare detected"

    # Test 4: Laplacian Texture Variance (Motion-Blur Friendly)
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    
    # Lowered from 35.0 to 14.0 so normal webcam motion-blur does NOT trigger flat photo warning
    if lap_var < 14.0:
        return False, "Flat image / printed photo detected (low texture variance)"

    return True, ""


def is_face_within_frame_safe_zone(face_box_norm: list, border_margin: float = 0.02) -> bool:
    """
    face_box_norm = [x, y, w, h] normalized between 0.0 and 1.0.
    Rejects the face if any edge touches or crosses border_margin (2% from image edge).
    """
    x, y, w, h = face_box_norm
    right = x + w
    bottom = y + h

    # Check all four boundaries
    if x <= border_margin:
        return False  # Left edge clipped
    if y <= border_margin:
        return False  # Top edge clipped
    if right >= (1.0 - border_margin):
        return False  # Right edge clipped
    if bottom >= (1.0 - border_margin):
        return False  # Bottom edge clipped

    return True


def _get_models():
    """Each worker thread gets its own model instance; no global lock contention."""
    if not hasattr(_local, "det") or not hasattr(_local, "rec"):
        _local.det = cv2.FaceDetectorYN.create(
            str(settings.MODELS_DIR / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.8, 0.3, 5000)
        _local.rec = cv2.FaceRecognizerSF.create(
            str(settings.MODELS_DIR / "face_recognition_sface_2021dec.onnx"), "")
    return _local.det, _local.rec


def embed_with_box(image_bytes: bytes):
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FaceError("Invalid image")
    h, w = img.shape[:2]
    scale = 1280 / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
        h, w = img.shape[:2]

    det, rec = _get_models()
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    if faces is None or len(faces) == 0:
        raise FaceError("No face detected")
    face = max(faces, key=lambda f: f[2] * f[3])   # largest face
    if face[2] < 60 or face[3] < 60:
        raise FaceError("Face too small")
    aligned = rec.alignCrop(img, face)
    feat = rec.feature(aligned)

    # Normalized face bounding box: x, y, w, h in 0..1 range
    box = [float(face[0]) / w, float(face[1]) / h, float(face[2]) / w, float(face[3]) / h]

    # Crop the raw face area for anti-spoofing tests
    x, y, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
    x, y = max(0, x), max(0, y)
    crop = img[y:y+fh, x:x+fw]

    # Run multi-factor anti-spoofing & zoom detection
    is_live, spoof_reason = check_anti_spoofing(crop, box)

    # Extract 5 landmarks: right eye, left eye, nose tip, right mouth corner, left mouth corner
    landmarks = []
    if len(face) >= 14:
        for idx in range(4, 14, 2):
            landmarks.append([float(face[idx]) / w, float(face[idx+1]) / h])

    v = feat.flatten().astype(np.float32)
    return v / np.linalg.norm(v), box, is_live, spoof_reason, landmarks




def embed(image_bytes: bytes) -> np.ndarray:
    return embed_with_box(image_bytes)[0]