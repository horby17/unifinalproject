"""Recording lifecycle driven by the frames already processed by the camera stream."""

from collections import deque
from pathlib import Path
import threading
import time
import uuid

import cv2


class LiftRecording:
    def __init__(self, owner, athlete_id, lift_type, mode, output_dir, persist,
                 clock=time.monotonic, writer_factory=None):
        self.id = uuid.uuid4().hex
        self.owner = owner
        self.athlete_id = athlete_id
        self.lift_type = lift_type
        self.mode = mode
        self.output_dir = Path(output_dir)
        self.persist = persist
        self.clock = clock
        self.writer_factory = writer_factory or cv2.VideoWriter
        self.lock = threading.RLock()
        self.state = "waiting" if mode == "automatic" else "starting"
        self.message = "Esperando postura inicial" if mode == "automatic" else "Preparando grabación"
        self.writer = None
        self.last_frame = None
        self.last_metrics = {}
        self.buffer = deque(maxlen=20)
        self.started_at = None
        self.last_pose_at = None
        self.last_write_at = None
        self.created_at = clock()
        self.pose_frames = 0
        self.ready_frames = 0
        self.movement_frames = 0
        self.return_frames = 0
        self.ready = False
        self.knee_min = None
        self.evidence_frame = None
        self.result = None
        self.error = None

    def snapshot(self):
        with self.lock:
            return {
                "id": self.id, "mode": self.mode, "state": self.state,
                "message": self.message, "result": self.result, "error": self.error,
            }

    def _start(self, frame, now):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.video_filename = f"{self.id}.webm"
        height, width = frame.shape[:2]
        self.writer = self.writer_factory(
            str(self.output_dir / self.video_filename),
            cv2.VideoWriter_fourcc(*"VP80"), 20.0, (width, height),
        )
        if not self.writer.isOpened():
            self.writer.release()
            self.writer = None
            self.state = "error"
            self.error = "No se pudo iniciar la grabación de video."
            self.message = self.error
            return
        for buffered_frame in self.buffer:
            self.writer.write(buffered_frame)
        self.buffer.clear()
        self.state = "recording"
        self.message = "Grabando levantamiento" if self.mode == "automatic" else "Grabación normal en curso"
        self.started_at = now
        self.last_pose_at = now
        self.last_write_at = None

    def process(self, frame, has_pose, metrics, phase=None, status=None, reason=None):
        with self.lock:
            if self.state in {"done", "cancelled", "error"}:
                return
            now = self.clock()
            self.last_frame = frame.copy()
            self.last_metrics = dict(metrics)
            self.pose_frames = self.pose_frames + 1 if has_pose else 0
            knee = metrics.get("knee_angle") if has_pose else None
            hip = metrics.get("hip_angle") if has_pose else None
            if self.mode == "normal" and self.state == "starting":
                self._start(frame, now)
            elif self.state == "waiting":
                self.buffer.append(frame.copy())
                if not has_pose:
                    self.ready_frames = self.movement_frames = 0
                    self.ready = False
                    self.message = "Esperando una postura visible"
                    return
                if self.lift_type == "Squat":
                    upright = knee is not None and hip is not None and knee >= 165 and hip >= 155
                    self.ready_frames = self.ready_frames + 1 if upright else 0
                    self.ready = self.ready or self.ready_frames >= 3
                    moving = self.ready and knee is not None and knee < 150
                    self.movement_frames = self.movement_frames + 1 if moving else 0
                    start = self.movement_frames >= 3
                    self.message = "Listo: inicia la sentadilla" if self.ready else "Colócate de pie para iniciar"
                else:
                    initial = "SETUP" if self.lift_type == "Deadlift" else "IDLE"
                    self.ready = self.ready or (phase == initial and self.pose_frames >= 3)
                    active_phases = {"PULL"} if self.lift_type == "Deadlift" else {"UNRACK", "DESCENT", "CHEST", "ASCENT"}
                    start = self.ready and phase in active_phases
                    self.message = "Listo: inicia el levantamiento" if self.ready else "Esperando postura inicial"
                if start:
                    self._start(frame, now)

            if self.state != "recording":
                return
            if self.last_write_at is None or now - self.last_write_at >= 0.05:
                self.writer.write(frame)
                self.last_write_at = now
            if has_pose:
                self.last_pose_at = now
            if self.mode == "normal":
                if now - self.started_at >= 300:
                    self.finish(reason="Duración máxima de cinco minutos alcanzada")
                return
            if knee is not None:
                if self.knee_min is None or knee < self.knee_min:
                    self.knee_min = knee
                    if self.lift_type == "Squat":
                        self.evidence_frame = frame.copy()
            if self.lift_type == "Squat":
                upright = knee is not None and hip is not None and knee >= 165 and hip >= 155
                self.return_frames = self.return_frames + 1 if upright else 0
                if self.return_frames >= 3:
                    valid = self.knee_min is not None and self.knee_min <= 100
                    self.finish("Bueno" if valid else "Nulo", None if valid else "Profundidad insuficiente")
            elif status in {"VALIDO", "NO_VALIDO"}:
                self.finish("Bueno" if status == "VALIDO" else "Nulo", reason)
            if self.state == "recording" and now - self.last_pose_at >= 2:
                self.finish("Nulo", "Se perdió la postura durante el levantamiento")
            elif self.state == "recording" and now - self.started_at >= 30:
                self.finish("Nulo", "No se completó el levantamiento en 30 segundos")

    def finish(self, result=None, reason=None):
        with self.lock:
            if self.state in {"done", "cancelled", "error"}:
                return
            if self.writer is None:
                self.state = "cancelled"
                self.message = reason or "Grabación cancelada antes de iniciar"
                self.buffer.clear()
                return
            self.writer.release()
            self.writer = None
            result = "Sin evaluar" if self.mode == "normal" else result or "Nulo"
            try:
                evidence = self.evidence_frame if self.evidence_frame is not None else self.last_frame
                metrics = dict(self.last_metrics)
                if self.knee_min is not None:
                    metrics["knee_min"] = self.knee_min
                self.result = self.persist(self, evidence, result, reason, metrics)
                self.state = "done"
                self.message = "Intento automático guardado" if self.mode == "automatic" else "Video guardado"
            except Exception:
                self.state = "error"
                self.error = "No se pudo guardar el intento. El video permanece en el servidor."
                self.message = self.error
                raise
