import enum
import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple


LEFT_SHOULDER = 11
LEFT_HIP = 23
LEFT_KNEE = 25
LEFT_ANKLE = 27


class DeadliftPhase(str, enum.Enum):
    SETUP = "SETUP"
    PULL = "PULL"
    LOCKOUT = "LOCKOUT"
    DOWN = "DOWN"


@dataclass
class DeadliftResult:
    phase: DeadliftPhase
    status: str
    reason: Optional[str]
    metrics: Dict[str, Optional[float]]
    event: Optional[str]


def _calculate_angle(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    angle = math.degrees(
        math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(a[1] - b[1], a[0] - b[0])
    )
    angle = abs(angle)
    if angle > 180:
        angle = 360 - angle
    return angle


def _get_xy(landmarks: Sequence, index: int, min_visibility: float = 0.35) -> Optional[Tuple[float, float]]:
    point = landmarks[index]
    visibility = getattr(point, "visibility", 1.0)
    if visibility < min_visibility:
        return None
    return (point.x, point.y)


def compute_deadlift_metrics(landmarks: Sequence) -> Dict[str, Optional[float]]:
    hip = _get_xy(landmarks, LEFT_HIP)
    knee = _get_xy(landmarks, LEFT_KNEE)
    ankle = _get_xy(landmarks, LEFT_ANKLE)
    shoulder = _get_xy(landmarks, LEFT_SHOULDER)

    metrics: Dict[str, Optional[float]] = {
        "knee_angle": None,
        "hip_angle": None,
        "back_angle": None,
    }

    if hip and knee and ankle:
        metrics["knee_angle"] = _calculate_angle(hip, knee, ankle)
    if shoulder and hip and knee:
        metrics["hip_angle"] = _calculate_angle(shoulder, hip, knee)
    if shoulder and hip:
        dx = shoulder[0] - hip[0]
        dy = hip[1] - shoulder[1]
        if dy == 0:
            metrics["back_angle"] = 90.0
        else:
            metrics["back_angle"] = math.degrees(math.atan2(abs(dx), abs(dy)))

    return metrics


class DeadliftState:
    def __init__(self) -> None:
        self.phase = DeadliftPhase.SETUP
        self.status = "PENDIENTE"
        self.reason: Optional[str] = None
        self.prev_metrics: Optional[Dict[str, Optional[float]]] = None
        self.metrics_min: Dict[str, Optional[float]] = {"knee_min": None, "hip_min": None}
        self.metrics_max: Dict[str, Optional[float]] = {"knee_max": None, "hip_max": None}
        self.lockout_reached = False
        self.event_emitted: Optional[str] = None
        self.down_frames = 0
        self.pull_frames = 0

    def _update_extremes(self, metrics: Dict[str, Optional[float]]) -> None:
        knee = metrics["knee_angle"]
        hip = metrics["hip_angle"]
        if knee is not None:
            self.metrics_min["knee_min"] = (
                knee if self.metrics_min["knee_min"] is None else min(self.metrics_min["knee_min"], knee)
            )
            self.metrics_max["knee_max"] = (
                knee if self.metrics_max["knee_max"] is None else max(self.metrics_max["knee_max"], knee)
            )
        if hip is not None:
            self.metrics_min["hip_min"] = (
                hip if self.metrics_min["hip_min"] is None else min(self.metrics_min["hip_min"], hip)
            )
            self.metrics_max["hip_max"] = (
                hip if self.metrics_max["hip_max"] is None else max(self.metrics_max["hip_max"], hip)
            )

    def _lockout_completed(self, metrics: Dict[str, Optional[float]]) -> bool:
        knee = metrics["knee_angle"]
        hip = metrics["hip_angle"]
        return knee is not None and hip is not None and knee >= 165 and hip >= 165

    def update(self, landmarks: Optional[Sequence], timestamp_ms: int) -> DeadliftResult:
        event = None
        if landmarks is None:
            return DeadliftResult(self.phase, self.status, self.reason, {"knee_angle": None, "hip_angle": None, "back_angle": None}, None)

        metrics = compute_deadlift_metrics(landmarks)
        self._update_extremes(metrics)

        knee = metrics["knee_angle"]
        hip = metrics["hip_angle"]
        lockout_now = self._lockout_completed(metrics)

        if self.phase == DeadliftPhase.SETUP:
            if self.prev_metrics and knee is not None and hip is not None:
                prev_knee = self.prev_metrics.get("knee_angle")
                prev_hip = self.prev_metrics.get("hip_angle")
                if prev_knee is not None and prev_hip is not None:
                    knee_up = knee - prev_knee
                    hip_up = hip - prev_hip
                    if knee_up >= 3 and hip_up >= 3 and knee >= 100 and hip >= 100:
                        self.phase = DeadliftPhase.PULL
                        self.pull_frames = 0
        elif self.phase == DeadliftPhase.PULL:
            self.pull_frames += 1
            if lockout_now:
                self.phase = DeadliftPhase.LOCKOUT
                self.status = "VALIDO"
                self.reason = None
                if self.event_emitted != "lockout":
                    event = "lockout"
                    self.event_emitted = "lockout"
            else:
                knee_drop = (
                    self.metrics_max["knee_max"] is not None
                    and knee is not None
                    and knee < self.metrics_max["knee_max"] - 8
                )
                hip_drop = (
                    self.metrics_max["hip_max"] is not None
                    and hip is not None
                    and hip < self.metrics_max["hip_max"] - 8
                )
                if knee_drop and hip_drop and self.pull_frames >= 6:
                    self.down_frames += 1
                else:
                    self.down_frames = 0
                if self.down_frames >= 6:
                    self.phase = DeadliftPhase.DOWN
                    self.status = "NO_VALIDO"
                    self.reason = "sin bloqueo completo"
                    if self.event_emitted != "fail":
                        event = "fail"
                        self.event_emitted = "fail"
        elif self.phase == DeadliftPhase.LOCKOUT:
            if knee is not None and hip is not None:
                if knee < 160 or hip < 160:
                    self.phase = DeadliftPhase.DOWN
        elif self.phase == DeadliftPhase.DOWN:
            if lockout_now and self.event_emitted != "lockout":
                self.phase = DeadliftPhase.LOCKOUT
                self.status = "VALIDO"
                self.reason = None
                event = "lockout"
                self.event_emitted = "lockout"

        self.prev_metrics = metrics
        return DeadliftResult(self.phase, self.status, self.reason, metrics, event)


_DEFAULT_STATE = DeadliftState()


def evaluate_deadlift(landmarks: Optional[Sequence], timestamp_ms: int, state: Optional[DeadliftState] = None) -> DeadliftResult:
    active_state = state or _DEFAULT_STATE
    return active_state.update(landmarks, timestamp_ms)
