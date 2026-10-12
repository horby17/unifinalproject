import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MPLCONFIGDIR", "/tmp/unifinalproject-test-matplotlib")

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main


class RecordingRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        engine = create_engine(f"sqlite:///{self.temp.name}/test.db")
        self.addCleanup(engine.dispose)
        main.Base.metadata.create_all(engine)
        self.db_factory = sessionmaker(bind=engine, expire_on_commit=False)
        with self.db_factory() as db:
            db.add(main.Athlete(id=1, document_id="test", first_name="Atleta", last_name="Prueba",
                                age=25, sex="M", body_weight=80, category="83", level="Principiante",
                                years_training=1))
            db.commit()
        for name, value in [("SessionLocal", self.db_factory), ("RECORDINGS", {}),
                            ("DB_PATH", os.path.join(self.temp.name, "test.db")), ("ENGINE", engine),
                            ("ATTEMPT_UPLOAD_DIR", self.temp.name)]:
            context = patch.object(main, name, value)
            context.start()
            self.addCleanup(context.stop)
        main.init_db()
        with closing(sqlite3.connect(main.DB_PATH)) as conn, conn:
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("other", "unused-test-hash"))
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session["user"] = "admin"

    def create(self, mode="normal", lift_type="Squat"):
        return self.client.post("/api/recordings", json={"athlete_id": 1, "lift_type": lift_type, "mode": mode})

    def test_authentication_and_input_validation(self):
        self.assertEqual(main.app.test_client().post("/api/recordings", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/recordings", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/recordings", json={"athlete_id": 999, "lift_type": "Squat", "mode": "normal"}).status_code, 400)

    def test_normal_recording_persists_video_and_attempt(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        recording_id = response.json["id"]
        recording = main.RECORDINGS[recording_id]
        for _ in range(4):
            recording.process(np.zeros((48, 64, 3), dtype=np.uint8), False, {})
        response = self.client.post(f"/api/recordings/{recording_id}", json={"action": "stop"})
        self.assertEqual(response.json["state"], "done")
        result = response.json["result"]
        self.assertEqual(result["ai_result"], "Sin evaluar")
        with self.db_factory() as db:
            self.assertEqual(db.query(main.Attempt).count(), 1)
            self.assertEqual(db.get(main.Attempt, result["attempt_id"]).athlete_id, 1)
        with self.client.get(result["image_path"]) as image:
            self.assertEqual(image.status_code, 200)
        with self.client.get(result["video_path"]) as video:
            self.assertEqual(video.status_code, 200)
        self.assertEqual(main.app.test_client().get(result["video_path"]).status_code, 302)

    def test_automatic_result_and_capture_are_persisted(self):
        response = self.create("automatic", "Bench")
        recording_id = response.json["id"]
        recording = main.RECORDINGS[recording_id]
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        for _ in range(3):
            recording.process(frame, True, {"elbow_angle": 175}, phase="IDLE")
        recording.process(frame, True, {"elbow_angle": 170}, phase="UNRACK")
        recording.process(frame, True, {"elbow_angle": 175}, phase="RACK", status="VALIDO")
        response = self.client.get(f"/api/recordings/{recording_id}")
        self.assertEqual(response.json["result"]["ai_result"], "Bueno")
        with self.client.get(response.json["result"]["image_path"]) as image:
            self.assertEqual(image.status_code, 200)
        with self.db_factory() as db:
            self.assertEqual(db.query(main.Attempt).count(), 1)

    def test_cancel_waiting_allows_next_attempt(self):
        response = self.create("automatic")
        self.assertEqual(self.create().status_code, 409)
        response = self.client.post(f"/api/recordings/{response.json['id']}", json={"action": "stop"})
        self.assertEqual(response.json["state"], "cancelled")
        self.assertEqual(self.create().status_code, 201)
        with self.db_factory() as db:
            self.assertEqual(db.query(main.Attempt).count(), 0)

    def test_other_user_cannot_control_recording(self):
        recording_id = self.create().json["id"]
        other = main.app.test_client()
        with other.session_transaction() as session:
            session["user"] = "other"
        self.assertEqual(other.get(f"/api/recordings/{recording_id}").status_code, 404)
        self.assertEqual(other.post(f"/api/recordings/{recording_id}", json={"action": "stop"}).status_code, 404)
        self.assertEqual(other.get(f"/pose/stream?recording_id={recording_id}").status_code, 404)

    def test_stream_records_automatic_attempt_and_releases_camera(self):
        recording_id = self.create("automatic", "Squat").json["id"]
        readings = [(175, 175)] * 3 + [(140, 140)] * 3 + [(85, 90)] + [(175, 175)] * 3

        class Camera:
            def __init__(self):
                self.remaining = len(readings)
                self.released = False
            def read(self):
                self.remaining -= 1
                return self.remaining >= 0, np.zeros((48, 64, 3), dtype=np.uint8)
            def release(self):
                self.released = True

        camera = Camera()
        metrics = iter({"knee_angle": knee, "hip_angle": hip, "elbow_angle": None} for knee, hip in readings)
        with patch.object(main, "open_camera", return_value=camera), \
             patch.object(main, "detect_pose_from_frame", return_value=object()), \
             patch.object(main, "get_primary_landmarks", return_value=[object()]), \
             patch.object(main, "serialize_landmarks", return_value=[]), \
             patch.object(main, "build_pose_metrics", side_effect=lambda _: next(metrics)), \
             patch.object(main, "draw_pose_overlay", side_effect=lambda frame, *args, **kwargs: frame):
            with self.client.get(f"/pose/stream?recording_id={recording_id}") as response:
                self.assertEqual(response.data.count(b"Content-Type: image/jpeg"), len(readings))
        self.assertTrue(camera.released)
        status = self.client.get(f"/api/recordings/{recording_id}").json
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["result"]["ai_result"], "Bueno")
        with self.db_factory() as db:
            self.assertEqual(db.query(main.Attempt).count(), 1)

    def test_template_contains_both_modes_and_automatic_capture(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for text in ["Evaluación del levantamiento", "Tipo de levantamiento", "Opción automática",
                     "Grabación normal", "Captura del intento automático"]:
            self.assertIn(text, html)
        self.assertNotIn("Capturar frame", html)


if __name__ == "__main__":
    unittest.main()
