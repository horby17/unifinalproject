from contextlib import contextmanager
import unittest

from flask import template_rendered

import main
import test_recording_routes as fixtures


class ResultsTests(unittest.TestCase):
    def setUp(self):
        fixtures.RecordingRouteTests.setUp(self)
        with self.db_factory() as db:
            db.add(main.Athlete(id=2, document_id="second", first_name="Otro", last_name="Competidor",
                                age=25, sex="M", body_weight=80, category="83", level="Principiante",
                                years_training=1))
            for athlete_id, lift, result, reason in [
                (1, "Squat", "Bueno", None),
                (1, "Squat", "Nulo", "Profundidad insuficiente"),
                (1, "Bench", "Sin evaluar", None),
                (2, "Deadlift", "Nulo", "Modelo PoseLandmarker no encontrado. Define POSE_LANDMARKER_MODEL_PATH o agrega el archivo en /home/horby/Documentos/websystemcvia/models/pose_landmarker_lite.task"),
            ]:
                db.add(main.Attempt(athlete_id=athlete_id, lift_type=lift, ai_result=result,
                                    fail_reason=reason, image_path="/uploads/attempts/images/test.jpg",
                                    video_path="/uploads/attempts/videos/test.webm"))
            db.commit()

    @contextmanager
    def captured(self):
        contexts = []
        def receive(sender, template, context, **extra):
            contexts.append(context)
        template_rendered.connect(receive, main.app)
        try:
            yield contexts
        finally:
            template_rendered.disconnect(receive, main.app)

    def test_summary_groups_competitor_and_lift(self):
        with self.captured() as contexts:
            response = self.client.get("/results")
        self.assertEqual(response.status_code, 200)
        context = contexts[-1]
        self.assertEqual(context["total"], 4)
        summary = {(row.id, row.lift_type): (row.total, row.good, row.null, row.unevaluated)
                   for row in context["summary"]}
        self.assertEqual(summary[(1, "Squat")], (2, 1, 1, 0))
        self.assertEqual(summary[(1, "Bench")], (1, 0, 0, 1))
        self.assertEqual(summary[(2, "Deadlift")], (1, 0, 1, 0))

    def test_dashboard_statistics_use_all_results_and_exclude_unevaluated_from_rate(self):
        with self.captured() as contexts:
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        stats = contexts[-1]["statistics"]
        self.assertEqual(stats["counts"], {"Bueno": 1, "Nulo": 2, "Sin evaluar": 1})
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["evaluated"], 3)
        self.assertEqual(stats["success_rate"], 33.3)
        self.assertEqual([point["rate"] for point in stats["trend"]], [100, 50, 33.3])
        self.assertEqual(len(stats["groups"]), 3)
        html = response.get_data(as_text=True)
        self.assertNotIn("Acciones rapidas", html)
        self.assertNotIn("/images/logo/logo.svg", html)
        self.assertIn("bench-press.png", html)

    def test_dashboard_statistics_filters_and_no_evaluated_results(self):
        with self.captured() as contexts:
            self.client.get("/dashboard?athlete_id=1&lift_type=Bench")
        stats = contexts[-1]["statistics"]
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["counts"]["Sin evaluar"], 1)
        self.assertIsNone(stats["success_rate"])
        self.assertEqual(stats["trend"], [])
        self.assertEqual(stats["groups"][0]["movement"], "Bench")
        html = self.client.get("/dashboard?athlete_id=2&lift_type=Bench").get_data(as_text=True)
        self.assertIn("No hay resultados para los filtros seleccionados.", html)

    def test_dashboard_statistics_not_limited_to_recent_attempts(self):
        with self.db_factory() as db:
            for _ in range(8):
                db.add(main.Attempt(athlete_id=1, lift_type="Squat", ai_result="Bueno", image_path="", video_path=""))
            db.commit()
        with self.captured() as contexts:
            self.client.get("/dashboard")
        self.assertEqual(contexts[-1]["statistics"]["total"], 12)
        self.assertEqual(len(contexts[-1]["attempts"]), 5)

    def test_combined_filters_apply_to_detail_and_summary(self):
        with self.captured() as contexts:
            response = self.client.get("/results?athlete_id=1&lift_type=Squat&result=Nulo")
        self.assertEqual(response.status_code, 200)
        context = contexts[-1]
        self.assertEqual(context["total"], 1)
        attempt, athlete = context["attempts"][0]
        self.assertEqual((athlete.id, attempt.lift_type, attempt.ai_result), (1, "Squat", "Nulo"))
        self.assertEqual(len(context["summary"]), 1)
        self.assertEqual(context["summary"][0].null, 1)

    def test_technical_reason_hidden_in_new_and_recent_tables(self):
        for route in ["/results", "/dashboard"]:
            html = self.client.get(route).get_data(as_text=True)
            self.assertIn("Análisis automático no disponible en este momento.", html)
            self.assertIn("Profundidad insuficiente", html)
            for technical in ["PoseLandmarker", "POSE_LANDMARKER_MODEL_PATH", "/home/horby", ".task"]:
                self.assertNotIn(technical, html)

    def test_pagination_uses_all_records_and_keeps_filters(self):
        with self.db_factory() as db:
            for _ in range(23):
                db.add(main.Attempt(athlete_id=1, lift_type="Squat", ai_result="Bueno",
                                    image_path="", video_path=""))
            db.commit()
        with self.captured() as contexts:
            response = self.client.get("/results?athlete_id=1&lift_type=Squat&page=1")
        self.assertEqual(contexts[-1]["total"], 25)
        self.assertEqual(len(contexts[-1]["attempts"]), 20)
        self.assertEqual(contexts[-1]["summary"][0].total, 25)
        self.assertIn("page=2", response.get_data(as_text=True))
        with self.captured() as contexts:
            self.client.get("/results?athlete_id=1&lift_type=Squat&page=999")
        self.assertEqual(contexts[-1]["page"], 2)
        self.assertEqual(len(contexts[-1]["attempts"]), 5)

    def test_empty_selection_and_login(self):
        self.assertEqual(main.app.test_client().get("/results").status_code, 302)
        html = self.client.get("/results?athlete_id=2&lift_type=Bench").get_data(as_text=True)
        self.assertIn("No hay resultados para los filtros seleccionados.", html)
        self.assertIn("No hay intentos guardados para esta selección.", html)

    def test_malformed_filters_do_not_break_page(self):
        response = self.client.get("/results?athlete_id=incorrecto&lift_type=otro&result=otro&page=incorrecto")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
