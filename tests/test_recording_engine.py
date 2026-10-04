import tempfile
import unittest

import cv2
import numpy as np

from recording_engine import LiftRecording


class FakeWriter:
    def __init__(self, *args):
        self.frames = []
        self.released = False

    def isOpened(self):
        return True

    def write(self, frame):
        self.frames.append(frame.copy())

    def release(self):
        self.released = True


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1.0
        self.saved = []
        self.frame = np.zeros((48, 64, 3), dtype=np.uint8)

    def make(self, lift="Squat", mode="automatic", writer=FakeWriter):
        def persist(recording, frame, result, reason, metrics):
            self.saved.append((result, reason, metrics, frame.copy()))
            return {"ai_result": result, "fail_reason": reason}
        return LiftRecording("admin", 1, lift, mode, self.temp.name, persist,
                             clock=lambda: self.now, writer_factory=writer)

    def feed(self, recording, knee=175, hip=175, has_pose=True, phase=None, status=None, reason=None, count=1):
        for _ in range(count):
            self.now += .1
            recording.process(self.frame, has_pose, {"knee_angle": knee, "hip_angle": hip}, phase, status, reason)

    def start_squat(self, recording):
        self.feed(recording, count=3)
        self.feed(recording, knee=140, hip=140, count=3)
        self.assertEqual(recording.state, "recording")

    def test_waits_for_initial_posture_and_confirmed_movement(self):
        r = self.make()
        self.feed(r, knee=None, hip=None, has_pose=False, count=10)
        self.feed(r, knee=90, hip=90, count=5)
        self.assertEqual(r.state, "waiting")
        self.feed(r, count=3)
        self.feed(r, knee=140, hip=140, count=2)
        self.assertEqual(r.state, "waiting")
        self.feed(r, knee=140, hip=140)
        self.assertEqual(r.state, "recording")

    def test_valid_squat_saves_once_with_depth_evidence(self):
        r = self.make()
        self.start_squat(r)
        self.frame[:] = 75
        self.feed(r, knee=85, hip=90)
        self.frame[:] = 0
        self.feed(r, count=3)
        self.assertEqual(r.state, "done")
        self.assertEqual(self.saved[0][0], "Bueno")
        self.assertEqual(self.saved[0][2]["knee_min"], 85)
        self.assertTrue(np.all(self.saved[0][3] == 75))
        self.feed(r, count=20)
        r.finish()
        self.assertEqual(len(self.saved), 1)

    def test_shallow_squat_is_saved_as_nulo(self):
        r = self.make()
        self.start_squat(r)
        self.feed(r, knee=120, hip=130)
        self.feed(r, count=3)
        self.assertEqual(self.saved[0][:2], ("Nulo", "Profundidad insuficiente"))

    def test_pose_loss_saves_failed_attempt(self):
        r = self.make()
        self.start_squat(r)
        self.now += 2.1
        self.feed(r, knee=None, hip=None, has_pose=False)
        self.assertEqual(self.saved[0][0], "Nulo")
        self.assertIn("perdió", self.saved[0][1])

    def test_timeout_saves_failed_attempt(self):
        r = self.make()
        self.start_squat(r)
        self.now += 31
        self.feed(r, knee=130, hip=140)
        self.assertEqual(self.saved[0][0], "Nulo")
        self.assertIn("30 segundos", self.saved[0][1])

    def test_deadlift_saves_terminal_result(self):
        r = self.make("Deadlift")
        self.feed(r, phase="SETUP", count=3)
        self.feed(r, phase="PULL")
        self.feed(r, phase="LOCKOUT", status="VALIDO")
        self.assertEqual(self.saved[0][0], "Bueno")

    def test_bench_waits_for_rack_instead_of_pending_lockout(self):
        r = self.make("Bench")
        self.feed(r, phase="IDLE", count=3)
        self.feed(r, phase="UNRACK")
        self.feed(r, phase="LOCKOUT", status="PENDIENTE")
        self.assertEqual(r.state, "recording")
        self.feed(r, phase="RACK", status="VALIDO")
        self.assertEqual(self.saved[0][0], "Bueno")

    def test_bench_failure_saved(self):
        r = self.make("Bench")
        self.feed(r, phase="IDLE", count=3)
        self.feed(r, phase="UNRACK")
        self.feed(r, phase="END", status="NO_VALIDO", reason="re-bajada en ascenso")
        self.assertEqual(self.saved[0][:2], ("Nulo", "re-bajada en ascenso"))

    def test_normal_records_without_pose_until_user_stops(self):
        r = self.make(mode="normal")
        self.feed(r, knee=None, hip=None, has_pose=False, count=30)
        self.assertEqual(r.state, "recording")
        self.assertEqual(self.saved, [])
        writer = r.writer
        r.finish()
        self.assertTrue(writer.released)
        self.assertEqual(self.saved[0][0], "Sin evaluar")

    def test_cancelling_while_waiting_does_not_create_attempt(self):
        r = self.make()
        r.finish()
        self.assertEqual(r.state, "cancelled")
        self.assertEqual(self.saved, [])

    def test_interrupted_automatic_attempt_is_nulo(self):
        r = self.make()
        self.start_squat(r)
        r.finish(reason="Cámara detenida")
        self.assertEqual(self.saved[0][:2], ("Nulo", "Cámara detenida"))

    def test_generated_video_can_be_read(self):
        r = self.make(mode="normal", writer=cv2.VideoWriter)
        self.feed(r, count=10)
        r.finish()
        cap = cv2.VideoCapture(str(r.output_dir / r.video_filename))
        try:
            self.assertTrue(cap.isOpened())
            ok, frame = cap.read()
            self.assertTrue(ok)
            self.assertEqual(frame.shape, self.frame.shape)
        finally:
            cap.release()


if __name__ == "__main__":
    unittest.main()
