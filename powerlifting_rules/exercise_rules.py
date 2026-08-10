import csv
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Sequence

from pose_utils import LEFT_HIP, LEFT_KNEE, LEFT_SHOULDER


@dataclass
class EvaluationState:
    exercise: str
    phase: str
    is_valid: bool
    rep_completed: bool
    rep_count: int
    message: str


class PowerliftingJudge:
    """State machine + validation rules for Squat only."""

    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        self.rep_count = 0
        self.phase = "Inicio"
        self.exercise = "Squat"

        self._bottom_reached = False
        self._ascent_started = False
        self._last_primary: Optional[float] = None
        self._frames_since_bottom = 0
        self._failed_rep = False

        os.makedirs(self.output_dir, exist_ok=True)
        self.log_path = os.path.join(self.output_dir, "repetitions_log.csv")
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "exercise", "valid", "image_path"])

    def update(
        self,
        angles: Dict[str, Optional[float]],
        landmarks: Optional[Sequence],
    ) -> EvaluationState:
        return self._update_squat(angles)

    def _update_squat(self, angles: Dict[str, Optional[float]]) -> EvaluationState:
        knee = angles.get("knee")
        hip = angles.get("hip")

        if knee is None or hip is None:
            return EvaluationState(self.exercise, self.phase, False, False, self.rep_count, "Sin deteccion")

        rep_completed = False
        is_valid = False
        message = ""

        if self._last_primary is not None:
            delta = knee - self._last_primary
            if not self._bottom_reached and delta < -1.5:
                self.phase = "Bajada"
            if knee <= 90:
                self.phase = "Punto mas bajo"
                self._bottom_reached = True
                self._frames_since_bottom = 0
            if self._bottom_reached and delta > 1.5:
                self.phase = "Subida"
                self._ascent_started = True

            if self._bottom_reached:
                self._frames_since_bottom += 1

            if self._bottom_reached and self._ascent_started and delta < -1.0:
                self._failed_rep = True

        lockout = knee >= 165 and hip >= 165
        if self._bottom_reached and self._ascent_started:
            if lockout and not self._failed_rep:
                self.phase = "Bloqueo final"
                rep_completed = True
                is_valid = True
                self.rep_count += 1
                message = "Sentadilla valida"
                self._reset_cycle()
            elif self._frames_since_bottom > 90:
                self.phase = "Bloqueo final"
                rep_completed = True
                is_valid = False
                message = "Nulo: sin bloqueo final"
                self._reset_cycle()

        self._last_primary = knee
        return EvaluationState(self.exercise, self.phase, is_valid, rep_completed, self.rep_count, message)

    def _reset_cycle(self) -> None:
        self._bottom_reached = False
        self._ascent_started = False
        self._last_primary = None
        self._frames_since_bottom = 0
        self._failed_rep = False
        self.phase = "Inicio"

    def save_valid_rep(self, frame, exercise: str) -> str:
        ts = datetime.now()
        stamp = ts.strftime("%Y%m%d_%H%M%S")
        filename = f"{exercise.replace(' ', '_').lower()}_{stamp}.jpg"
        image_path = os.path.join(self.output_dir, filename)
        import cv2

        cv2.imwrite(image_path, frame)

        with open(self.log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([ts.isoformat(timespec="seconds"), exercise, "Bueno", image_path])

        return image_path
