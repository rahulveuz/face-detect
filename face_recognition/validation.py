import cv2
import numpy as np

class QualityValidationError(Exception):
    pass


def validate_registration_quality(img: np.ndarray, faces: np.ndarray) -> None:
    """
    Validates a face photo before allowing registration.
    Raises QualityValidationError if any biometric standard is violated.
    """
    # 1. Exactly one face must be present
    if faces is None or len(faces) == 0:
        raise QualityValidationError("No face detected. Please face the camera directly.")
    if len(faces) > 1:
        raise QualityValidationError(
            f"Multiple faces detected ({len(faces)}). Ensure only one person is in frame."
        )

    face = faces[0]
    x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
    img_h, img_w = img.shape[:2]

    # 2. Minimum resolution
    if w < 90 or h < 90:
        raise QualityValidationError("Face is too far from camera. Move closer.")

    # 3. Edge boundary clearance (not cut off at the borders)
    margin_px = 10
    if x < margin_px or y < margin_px or (x + w) > (img_w - margin_px) or (y + h) > (img_h - margin_px):
        raise QualityValidationError("Face is partially cut off at the edge. Please center your face.")

    # 4. Frontal Pose Validation (Eye & Nose landmark alignment)
    # Landmarks: re_x, re_y, le_x, le_y, nose_x, nose_y, rm_x, rm_y, lm_x, lm_y
    if len(face) >= 14:
        re_x, le_x = face[4], face[6]
        nose_x = face[8]

        # Horizontal yaw ratio: nose should be roughly centered between eyes
        dist_left = abs(nose_x - le_x)
        dist_right = abs(nose_x - re_x)
        total_eye_dist = abs(le_x - re_x)

        if total_eye_dist > 0:
            yaw_ratio = abs(dist_left - dist_right) / total_eye_dist
            if yaw_ratio > 0.40:  # Head turned too far to left or right (> 15 deg)
                raise QualityValidationError("Head is turned sideways. Look straight into the lens.")

    # 5. Blur & Exposure Check
    crop = img[max(0, y):min(img_h, y+h), max(0, x):min(img_w, x+w)]
    if crop.size > 0:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        
        # Blur check via Laplacian variance
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if lap_var < 35.0:
            raise QualityValidationError("Photo is blurry or out of focus. Hold still and retake.")

        # Exposure check
        mean_brightness = np.mean(gray)
        if mean_brightness < 40:
            raise QualityValidationError("Lighting is too dark. Move to a well-lit area.")
        if mean_brightness > 220:
            raise QualityValidationError("Lighting is overexposed or washed out.")
