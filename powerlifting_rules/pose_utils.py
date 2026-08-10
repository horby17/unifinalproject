import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

# MediaPipe Pose landmark indices (33 points)
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_ELBOW = 13
RIGHT_ELBOW = 14
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24
LEFT_KNEE = 25
RIGHT_KNEE = 26
LEFT_ANKLE = 27
RIGHT_ANKLE = 28

POSE_CONNECTIONS: Sequence[Tuple[int, int]] = (
    (11, 13), (13, 15),
    (12, 14), (14, 16),
    (11, 12),
    (11, 23), (12, 24),
    (23, 24),
    (23, 25), (25, 27),
    (24, 26), (26, 28),
)


@dataclass
class PoseFrameData:
    timestamp_ms: int
    landmarks: Optional[Sequence]
    angles: Dict[str, Optional[float]]


def calculate_angle(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    """Returns the angle ABC in degrees (0..180)."""
    import math

    angle = math.degrees(
        math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(a[1] - b[1], a[0] - b[0])
    )
    angle = abs(angle)
    if angle > 180:
        angle = 360 - angle
    return angle


class PoseEstimator:
    """Wraps MediaPipe PoseLandmarker (Tasks API) for webcam/video frames."""

    def __init__(
        self,
        model_path: str,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                "No se encontro el modelo Pose Landmarker (.task). "
                f"Ruta esperada: {model_path}"
            )

        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=min_pose_detection_confidence,
            min_pose_presence_confidence=min_pose_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_segmentation_masks=False,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._base_ts = int(time.monotonic() * 1000)
        self._frame_idx = 0

    def process(self, frame_bgr) -> PoseFrameData:
        timestamp_ms = self._base_ts + self._frame_idx * 33
        self._frame_idx += 1

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.pose_landmarks:
            return PoseFrameData(
                timestamp_ms=timestamp_ms,
                landmarks=None,
                angles={"knee": None, "hip": None, "elbow": None, "shoulder": None},
            )

        landmarks = result.pose_landmarks[0]
        angles = self._compute_angles(landmarks)
        return PoseFrameData(timestamp_ms=timestamp_ms, landmarks=landmarks, angles=angles)

    def _get_xy(
        self,
        landmarks: Sequence,
        index: int,
        min_visibility: float = 0.35,
    ) -> Optional[Tuple[float, float]]:
        point = landmarks[index]
        visibility = getattr(point, "visibility", 1.0)
        if visibility < min_visibility:
            return None
        return (point.x, point.y)

    def _compute_angles(self, landmarks: Sequence) -> Dict[str, Optional[float]]:
        left_hip = self._get_xy(landmarks, LEFT_HIP)
        left_knee = self._get_xy(landmarks, LEFT_KNEE)
        left_ankle = self._get_xy(landmarks, LEFT_ANKLE)

        left_shoulder = self._get_xy(landmarks, LEFT_SHOULDER)
        left_elbow = self._get_xy(landmarks, LEFT_ELBOW)
        left_wrist = self._get_xy(landmarks, LEFT_WRIST)

        angles = {"knee": None, "hip": None, "elbow": None, "shoulder": None}

        if left_hip and left_knee and left_ankle:
            angles["knee"] = calculate_angle(left_hip, left_knee, left_ankle)
        if left_shoulder and left_hip and left_knee:
            angles["hip"] = calculate_angle(left_shoulder, left_hip, left_knee)
        if left_shoulder and left_elbow and left_wrist:
            angles["elbow"] = calculate_angle(left_shoulder, left_elbow, left_wrist)
        if left_hip and left_shoulder and left_elbow:
            angles["shoulder"] = calculate_angle(left_hip, left_shoulder, left_elbow)

        return angles


def draw_landmarks(frame_bgr, landmarks: Optional[Sequence]) -> None:
    if landmarks is None:
        return

    h, w = frame_bgr.shape[:2]
    for s, e in POSE_CONNECTIONS:
        p1 = landmarks[s]
        p2 = landmarks[e]
        x1, y1 = int(p1.x * w), int(p1.y * h)
        x2, y2 = int(p2.x * w), int(p2.y * h)
        cv2.line(frame_bgr, (x1, y1), (x2, y2), (0, 220, 120), 2)

    for point in landmarks:
        x, y = int(point.x * w), int(point.y * h)
        cv2.circle(frame_bgr, (x, y), 3, (0, 100, 255), -1)
