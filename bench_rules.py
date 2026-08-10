import enum
import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple


LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_ELBOW = 13
RIGHT_ELBOW = 14
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24


class BenchPhase(str, enum.Enum):
    IDLE = "IDLE"
    UNRACK = "UNRACK"
    DESCENT = "DESCENT"
    CHEST = "CHEST"
    ASCENT = "ASCENT"
    LOCKOUT = "LOCKOUT"
    RACK = "RACK"
    END = "END"


@dataclass
class BenchResult:
    phase: BenchPhase
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


def _avg_point(points):
    valid = [p for p in points if p is not None]
    if not valid:
        return None
    x = sum(p[0] for p in valid) / len(valid)
    y = sum(p[1] for p in valid) / len(valid)
    return (x, y)


def compute_bench_metrics(landmarks: Sequence) -> Dict[str, Optional[float]]:
    l_sh = _get_xy(landmarks, LEFT_SHOULDER)
    r_sh = _get_xy(landmarks, RIGHT_SHOULDER)
    l_el = _get_xy(landmarks, LEFT_ELBOW)
    r_el = _get_xy(landmarks, RIGHT_ELBOW)
    l_wr = _get_xy(landmarks, LEFT_WRIST)
    r_wr = _get_xy(landmarks, RIGHT_WRIST)
    l_hip = _get_xy(landmarks, LEFT_HIP)
    r_hip = _get_xy(landmarks, RIGHT_HIP)

    shoulder = _avg_point([l_sh, r_sh])
    hip = _avg_point([l_hip, r_hip])
    wrist = _avg_point([l_wr, r_wr]) or l_wr or r_wr

    torso_len = None
    scale_len = None
    if shoulder and hip:
        dx = shoulder[0] - hip[0]
        dy = shoulder[1] - hip[1]
        torso_len = math.hypot(dx, dy)
        vertical_len = abs(dy)
        if torso_len < 1e-4:
            torso_len = None
        if vertical_len >= 0.04:
            scale_len = vertical_len
        elif torso_len:
            scale_len = torso_len * 0.35

    elbow_angle_l = None
    elbow_angle_r = None
    if l_sh and l_el and l_wr:
        elbow_angle_l = _calculate_angle(l_sh, l_el, l_wr)
    if r_sh and r_el and r_wr:
        elbow_angle_r = _calculate_angle(r_sh, r_el, r_wr)

    elbow_angles = [a for a in (elbow_angle_l, elbow_angle_r) if a is not None]
    elbow_avg = sum(elbow_angles) / len(elbow_angles) if elbow_angles else None

    wrist_height_norm = None
    if wrist and hip and torso_len:
        # higher wrist => larger value (hip_y - wrist_y) / torso_len (euclidean)
        wrist_height_norm = (hip[1] - wrist[1]) / torso_len
        wrist_height_norm = max(-1.5, min(1.5, wrist_height_norm))

    return {
        "wrist_y": wrist[1] if wrist else None,
        "shoulder_y": shoulder[1] if shoulder else None,
        "hip_y": hip[1] if hip else None,
        "torso_len": torso_len,
        "scale_len": scale_len,
        "wrist_height_norm": wrist_height_norm,
        "elbow_angle_l": elbow_angle_l,
        "elbow_angle_r": elbow_angle_r,
        "elbow_angle": elbow_avg,
    }


class BenchStateMachine:
    def __init__(self, view: str = "frontal") -> None:
        self.phase = BenchPhase.IDLE
        self.status = "PENDIENTE"
        self.reason: Optional[str] = None
        self.event_emitted: Optional[str] = None
        self.last_wrist_y: Optional[float] = None
        self.unrack_frames = 0
        self.descent_frames = 0
        self.pause_frames = 0
        self.ascent_frames = 0
        self.lockout_frames = 0
        self.rack_frames = 0
        self.rebend_frames = 0
        self.start_ts: Optional[int] = None
        self.lockout_ts: Optional[int] = None

        # Recommended thresholds (tune as needed)
        self.min_unrack_frames = 6
        self.min_descent_frames = 4
        self.min_pause_frames = 4
        self.min_ascent_frames = 4
        self.min_lockout_frames = 6
        self.min_rack_frames = 4
        self.max_duration_ms = 15000

        self.delta_norm = 0.015  # descent/ascent step threshold
        self.chest_band_low = 0.10
        self.chest_band_high = 0.32
        self.lockout_height = 0.28
        self.rack_offset = 0.03
        self.elbow_lockout = 165

        self.set_view(view)

    def set_view(self, view: str) -> None:
        view = (view or "frontal").lower()
        if view not in {"frontal", "lateral", "diagonal"}:
            view = "frontal"
        if view == "lateral":
            self.delta_norm = 0.012
            self.chest_band_low = 0.08
            self.chest_band_high = 0.28
            self.lockout_height = 0.25
            self.rack_offset = 0.03
            self.min_pause_frames = 3
            self.min_lockout_frames = 5
        elif view == "diagonal":
            self.delta_norm = 0.014
            self.chest_band_low = 0.09
            self.chest_band_high = 0.30
            self.lockout_height = 0.27
            self.rack_offset = 0.035
            self.min_pause_frames = 4
            self.min_lockout_frames = 6

    def _reset(self) -> None:
        self.phase = BenchPhase.IDLE
        self.status = "PENDIENTE"
        self.reason = None
        self.event_emitted = None
        self.last_wrist_y = None
        self.unrack_frames = 0
        self.descent_frames = 0
        self.pause_frames = 0
        self.ascent_frames = 0
        self.lockout_frames = 0
        self.rack_frames = 0
        self.rebend_frames = 0
        self.start_ts = None
        self.lockout_ts = None

    def update(self, landmarks: Optional[Sequence], timestamp_ms: int) -> BenchResult:
        event = None
        if landmarks is None:
            return BenchResult(self.phase, self.status, self.reason, {"wrist_height_norm": None}, None)

        metrics = compute_bench_metrics(landmarks)
        wrist_y = metrics.get("wrist_y")
        shoulder_y = metrics.get("shoulder_y")
        torso_len = metrics.get("torso_len")
        scale_len = metrics.get("scale_len")
        wrist_norm = metrics.get("wrist_height_norm")
        elbow_angle = metrics.get("elbow_angle")

        if self.start_ts and timestamp_ms - self.start_ts > self.max_duration_ms:
            if self.status != "VALIDO":
                self.phase = BenchPhase.END
                self.status = "NO_VALIDO"
                self.reason = "timeout sin completar"
                if self.event_emitted != "fail":
                    event = "fail"
                    self.event_emitted = "fail"
            return BenchResult(self.phase, self.status, self.reason, metrics, event)

        if wrist_y is None or shoulder_y is None or torso_len is None or wrist_norm is None or scale_len is None:
            return BenchResult(self.phase, self.status, self.reason, metrics, None)

        chest_level_low = shoulder_y + self.chest_band_low * scale_len
        chest_level_high = shoulder_y + self.chest_band_high * scale_len

        if self.phase == BenchPhase.IDLE:
            arms_extended = elbow_angle is not None and elbow_angle >= self.elbow_lockout
            wrists_high = wrist_norm >= self.lockout_height
            if arms_extended and wrists_high:
                self.unrack_frames += 1
            else:
                self.unrack_frames = 0
            if self.unrack_frames >= self.min_unrack_frames:
                self.phase = BenchPhase.UNRACK
                self.start_ts = timestamp_ms
        elif self.phase == BenchPhase.UNRACK:
            if self.last_wrist_y is not None and wrist_y - self.last_wrist_y >= self.delta_norm * scale_len:
                self.descent_frames += 1
            else:
                self.descent_frames = 0
            if self.descent_frames >= self.min_descent_frames:
                self.phase = BenchPhase.DESCENT
        elif self.phase == BenchPhase.DESCENT:
            if chest_level_low <= wrist_y <= chest_level_high:
                self.pause_frames += 1
            else:
                self.pause_frames = 0
            if self.pause_frames >= self.min_pause_frames:
                self.phase = BenchPhase.CHEST
        elif self.phase == BenchPhase.CHEST:
            if self.last_wrist_y is not None and self.last_wrist_y - wrist_y >= self.delta_norm * scale_len:
                self.ascent_frames += 1
            else:
                self.ascent_frames = 0
            if self.ascent_frames >= self.min_ascent_frames:
                self.phase = BenchPhase.ASCENT
        elif self.phase == BenchPhase.ASCENT:
            if self.last_wrist_y is not None and wrist_y - self.last_wrist_y >= self.delta_norm * scale_len:
                self.rebend_frames += 1
            else:
                self.rebend_frames = 0
            if self.rebend_frames >= 4:
                self.phase = BenchPhase.END
                self.status = "NO_VALIDO"
                self.reason = "re-bajada en ascenso"
                if self.event_emitted != "fail":
                    event = "fail"
                    self.event_emitted = "fail"
                self.last_wrist_y = wrist_y
                return BenchResult(self.phase, self.status, self.reason, metrics, event)

            if elbow_angle is not None and elbow_angle >= self.elbow_lockout and wrist_norm >= self.lockout_height:
                self.lockout_frames += 1
            else:
                self.lockout_frames = 0
            if self.lockout_frames >= self.min_lockout_frames:
                self.phase = BenchPhase.LOCKOUT
                self.status = "PENDIENTE"
                if self.event_emitted != "lockout":
                    event = "lockout"
                    self.event_emitted = "lockout"
                self.lockout_ts = timestamp_ms
        elif self.phase == BenchPhase.LOCKOUT:
            if wrist_norm >= self.lockout_height + self.rack_offset:
                self.rack_frames += 1
            else:
                self.rack_frames = 0
            if self.rack_frames >= self.min_rack_frames:
                self.phase = BenchPhase.RACK
                self.status = "VALIDO"
                self.reason = None
                if self.event_emitted != "rack":
                    event = "rack"
                    self.event_emitted = "rack"
        elif self.phase == BenchPhase.RACK:
            self.phase = BenchPhase.END
        elif self.phase == BenchPhase.END:
            pass

        self.last_wrist_y = wrist_y
        return BenchResult(self.phase, self.status, self.reason, metrics, event)
