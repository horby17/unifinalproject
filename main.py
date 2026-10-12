import json
import secrets
from contextlib import closing
import math
import os
import sqlite3
import threading
import time
import uuid
import cv2
import numpy as np
from flask import abort, flash, Flask, Response, jsonify, redirect, render_template, request, session, send_from_directory
from sqlalchemy import Column, Float, Integer, String, Text, create_engine, case, func
from sqlalchemy.orm import declarative_base, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from deadlift_rules import DeadliftState, evaluate_deadlift
from bench_rules import BenchStateMachine
from recording_engine import LiftRecording

print("OpenCV:", cv2.__version__)
print("Flask OK")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
ATTEMPT_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "attempts")
ENGINE = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=ENGINE, expire_on_commit=False, future=True)
Base = declarative_base()

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_DEFAULT_MODEL_PATH = os.path.join(_MODEL_DIR, "pose_landmarker_full.task")
if not os.path.exists(_DEFAULT_MODEL_PATH):
    _DEFAULT_MODEL_PATH = os.path.join(_MODEL_DIR, "pose_landmarker_lite.task")
POSE_LANDMARKER_MODEL_PATH = os.environ.get("POSE_LANDMARKER_MODEL_PATH", _DEFAULT_MODEL_PATH)
POSE_LANDMARKER = None
POSE_LANDMARKER_ERROR = None
POSE_LANDMARKER_LOCK = threading.Lock()

LATEST_POSE_RESULT = {
    "timestamp_ms": None,
    "has_pose": False,
    "body_angles": {"knee_angle": None, "hip_angle": None, "elbow_angle": None},
    "landmarks": [],
    "updated_at": None,
    "lift_type": "",
    "athlete_id": None,
    "phase": None,
    "status": None,
    "reason": None,
    "result": None,
    "error": None,
}
LATEST_POSE_RESULT_LOCK = threading.Lock()
RECORDINGS = {}
RECORDINGS_LOCK = threading.Lock()

LEFT_SHOULDER = 11
LEFT_ELBOW = 13
LEFT_WRIST = 15
LEFT_HIP = 23
LEFT_KNEE = 25
LEFT_ANKLE = 27

POSE_CONNECTIONS = (
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 12),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (24, 26),
    (25, 27),
    (26, 28),
    (27, 29),
    (28, 30),
    (29, 31),
    (30, 32),
)


class Athlete(Base):
    __tablename__ = "athletes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(50), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    sex = Column(String(20), nullable=False)
    body_weight = Column(Float, nullable=False)
    category = Column(String(100), nullable=False)
    level = Column(String(20), nullable=False)
    years_training = Column(Integer, nullable=False)
    notes = Column(Text, nullable=True)


class Attempt(Base):
    __tablename__ = "attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(Integer, nullable=False)
    lift_type = Column(String(20), nullable=False)
    weight = Column(Float, nullable=True)
    video_path = Column(String(255), nullable=False)
    image_path = Column(String(255), nullable=False)
    ai_result = Column(String(20), nullable=False)
    fail_reason = Column(Text, nullable=True)
    body_angles = Column(Text, nullable=True)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    Base.metadata.create_all(ENGINE)
    ensure_attempts_schema()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "is_active" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "role" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            conn.execute("UPDATE users SET role = 'admin' WHERE id = (SELECT id FROM users ORDER BY CASE WHEN username = 'admin' THEN 0 ELSE 1 END, id LIMIT 1)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lift_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              athlete_id INTEGER NOT NULL,
              attempt_id INTEGER,
              lift_type TEXT NOT NULL,
              event_type TEXT,
              result TEXT,
              reason TEXT,
              image_path TEXT,
              metrics_json TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        cur = conn.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash("admin123")),
            )
            conn.execute(
                "UPDATE users SET role = 'admin' WHERE username = 'admin'"
            )
        conn.commit()


def get_user(username):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
        return cur.fetchone()


def require_login():
    username = session.get("user")
    user = get_user(username) if username else None
    if not user or not user["is_active"]:
        session.clear()
        return True
    return False


@app.template_filter("public_reason")
def public_reason(reason):
    if not reason:
        return None
    if any(marker in reason.lower() for marker in (
        "poselandmarker", "pose_landmarker", "modelo de pose", "model_asset_path", "mediapipe",
    )):
        return "Análisis automático no disponible en este momento."
    return reason


def ensure_attempts_schema():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='attempts'"
        )
        if not cur.fetchone():
            return
        cols = conn.execute("PRAGMA table_info(attempts)").fetchall()
        weight_col = next((col for col in cols if col[1] == "weight"), None)
        if not weight_col or weight_col[3] == 0:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              athlete_id INTEGER NOT NULL,
              lift_type TEXT NOT NULL,
              weight REAL,
              video_path TEXT NOT NULL,
              image_path TEXT NOT NULL,
              ai_result TEXT NOT NULL,
              fail_reason TEXT,
              body_angles TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO attempts_new (id, athlete_id, lift_type, weight, video_path, image_path, ai_result, fail_reason, body_angles)
            SELECT id, athlete_id, lift_type, weight, video_path, image_path, ai_result, fail_reason, body_angles
            FROM attempts
            """
        )
        conn.execute("DROP TABLE attempts")
        conn.execute("ALTER TABLE attempts_new RENAME TO attempts")
        conn.commit()


def save_upload(file_storage, subdir):
    if not file_storage or not file_storage.filename:
        return None
    os.makedirs(os.path.join(ATTEMPT_UPLOAD_DIR, subdir), exist_ok=True)
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None
    unique_name = f"{uuid.uuid4().hex}-{filename}"
    rel_path = os.path.join("uploads", "attempts", subdir, unique_name)
    abs_path = os.path.join(os.path.dirname(__file__), rel_path)
    file_storage.save(abs_path)
    return f"/{rel_path}"


def save_video_blob(file_storage):
    return save_upload(file_storage, "videos")


def save_frame_image(frame, prefix="frame"):
    os.makedirs(os.path.join(ATTEMPT_UPLOAD_DIR, "images"), exist_ok=True)
    filename = f"{prefix}-{uuid.uuid4().hex}.jpg"
    rel_path = os.path.join("uploads", "attempts", "images", filename)
    abs_path = os.path.join(ATTEMPT_UPLOAD_DIR, "images", filename)
    if not cv2.imwrite(abs_path, frame):
        raise RuntimeError("No se pudo guardar la imagen del intento.")
    return f"/{rel_path}"


def calculate_angle(a, b, c):
    ang = math.degrees(
        math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(a[1] - b[1], a[0] - b[0])
    )
    ang = abs(ang)
    if ang > 180:
        ang = 360 - ang
    return ang


def get_pose_landmarker():
    global POSE_LANDMARKER, POSE_LANDMARKER_ERROR
    with POSE_LANDMARKER_LOCK:
        if POSE_LANDMARKER is not None:
            return POSE_LANDMARKER
        if POSE_LANDMARKER_ERROR is not None:
            raise RuntimeError(POSE_LANDMARKER_ERROR)
        if not os.path.exists(POSE_LANDMARKER_MODEL_PATH):
            app.logger.error("No se encontró el modelo de pose en %s", POSE_LANDMARKER_MODEL_PATH)
            POSE_LANDMARKER_ERROR = "Análisis automático no disponible en este momento."
            raise RuntimeError(POSE_LANDMARKER_ERROR)
        # Inicializacion unica del modelo Tasks (modo VIDEO para timestamps por frame).
        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=POSE_LANDMARKER_MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_segmentation_masks=False,
        )
        POSE_LANDMARKER = vision.PoseLandmarker.create_from_options(options)
        return POSE_LANDMARKER


def detect_pose_from_frame(frame_bgr, timestamp_ms):
    landmarker = get_pose_landmarker()
    rgb_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
    # detect_for_video requiere timestamp monotono en milisegundos.
    return landmarker.detect_for_video(mp_image, int(timestamp_ms))


def get_primary_landmarks(pose_result):
    if not pose_result.pose_landmarks:
        return None
    return pose_result.pose_landmarks[0]


def get_landmark_xy(landmarks, idx, min_visibility=0.35):
    point = landmarks[idx]
    visibility = getattr(point, "visibility", 1.0)
    if visibility < min_visibility:
        return None
    return point.x, point.y


def build_pose_metrics(landmarks):
    metrics = {"knee_angle": None, "hip_angle": None, "elbow_angle": None}
    hip = get_landmark_xy(landmarks, LEFT_HIP)
    knee = get_landmark_xy(landmarks, LEFT_KNEE)
    ankle = get_landmark_xy(landmarks, LEFT_ANKLE)
    shoulder = get_landmark_xy(landmarks, LEFT_SHOULDER)
    elbow = get_landmark_xy(landmarks, LEFT_ELBOW)
    wrist = get_landmark_xy(landmarks, LEFT_WRIST)

    if hip and knee and ankle:
        metrics["knee_angle"] = calculate_angle(hip, knee, ankle)
    if shoulder and hip and knee:
        metrics["hip_angle"] = calculate_angle(shoulder, hip, knee)
    if shoulder and elbow and wrist:
        metrics["elbow_angle"] = calculate_angle(shoulder, elbow, wrist)
    return metrics


def update_attempt_metrics(metrics, current):
    for source, target in (
        ("knee_angle", "knee_min"),
        ("hip_angle", "hip_min"),
        ("elbow_angle", "elbow_min"),
    ):
        value = current[source]
        if value is None:
            continue
        metrics[target] = value if metrics[target] is None else min(metrics[target], value)


def evaluate_lift_metrics(lift_type, metrics):
    if lift_type == "Squat":
        knee = metrics.get("knee_min")
        return "Bueno" if knee is not None and knee <= 100 else "Nulo"
    if lift_type == "Bench":
        elbow = metrics.get("elbow_min")
        return "Bueno" if elbow is not None and elbow <= 110 else "Nulo"
    if lift_type == "Deadlift":
        hip = metrics.get("hip_min")
        return "Bueno" if hip is not None and hip <= 160 else "Nulo"
    return "Nulo"


def serialize_landmarks(landmarks):
    return [
        {
            "id": idx,
            "x": point.x,
            "y": point.y,
            "z": point.z,
            "visibility": getattr(point, "visibility", None),
            "presence": getattr(point, "presence", None),
        }
        for idx, point in enumerate(landmarks)
    ]


def draw_pose_overlay(frame_bgr, landmarks, metrics, result_label=None, overlay_info=None, show_text=True):
    h, w = frame_bgr.shape[:2]
    for start, end in POSE_CONNECTIONS:
        p1 = landmarks[start]
        p2 = landmarks[end]
        x1, y1 = int(p1.x * w), int(p1.y * h)
        x2, y2 = int(p2.x * w), int(p2.y * h)
        cv2.line(frame_bgr, (x1, y1), (x2, y2), (0, 220, 120), 2)
    for point in landmarks:
        x, y = int(point.x * w), int(point.y * h)
        cv2.circle(frame_bgr, (x, y), 3, (0, 90, 255), -1)

    if not show_text:
        return frame_bgr

    knee_angle = metrics.get("knee_angle")
    hip_angle = metrics.get("hip_angle")
    elbow_angle = metrics.get("elbow_angle")
    back_angle = metrics.get("back_angle")

    cv2.putText(
        frame_bgr,
        f"Rodilla: {knee_angle:.1f}" if knee_angle is not None else "Rodilla: -",
        (10, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )
    cv2.putText(
        frame_bgr,
        f"Cadera: {hip_angle:.1f}" if hip_angle is not None else "Cadera: -",
        (10, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )
    cv2.putText(
        frame_bgr,
        f"Codo: {elbow_angle:.1f}" if elbow_angle is not None else "Codo: -",
        (10, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )
    if back_angle is not None:
        cv2.putText(
            frame_bgr,
            f"Espalda: {back_angle:.1f}",
            (10, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )
    if result_label:
        cv2.putText(
            frame_bgr,
            f"Resultado: {result_label}",
            (10, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 220, 120) if result_label == "Bueno" else (0, 120, 220),
            2,
        )
    if overlay_info:
        line = 125
        if overlay_info.get("exercise"):
            cv2.putText(
                frame_bgr,
                overlay_info["exercise"],
                (10, line),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
            )
            line += 25
        if overlay_info.get("phase"):
            cv2.putText(
                frame_bgr,
                f"Fase: {overlay_info['phase']}",
                (10, line),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
            )
            line += 25
        if overlay_info.get("status"):
            cv2.putText(
                frame_bgr,
                f"Estado: {overlay_info['status']}",
                (10, line),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 220, 120) if overlay_info["status"] == "VALIDO" else (0, 120, 220),
                2,
            )
            line += 25
        extra_lines = overlay_info.get("lines", [])
        for text in extra_lines:
            cv2.putText(
                frame_bgr,
                text,
                (10, line),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
            )
            line += 22
    return frame_bgr


def save_deadlift_evidence(frame_bgr, event_type, payload, output_dir="frames"):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if event_type == "lockout":
        filename = f"frame_lockout_{timestamp}.jpg"
        result_label = "VALIDO"
    else:
        filename = f"frame_fail_{timestamp}.jpg"
        result_label = "NO_VALIDO"
    image_path = os.path.join(output_dir, filename)
    cv2.imwrite(image_path, frame_bgr)
    payload_with_result = dict(payload)
    payload_with_result["resultado"] = result_label
    json_path = os.path.join(output_dir, filename.replace(".jpg", ".json"))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload_with_result, fh, ensure_ascii=False, indent=2)
    return image_path, json_path


def save_bench_evidence(frame_bgr, event_type, payload, output_dir="frames"):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if event_type == "lockout":
        filename = f"frame_lockout_{timestamp}.jpg"
        result_label = "PENDIENTE"
    elif event_type == "rack":
        filename = f"frame_rack_{timestamp}.jpg"
        result_label = "VALIDO"
    else:
        filename = f"frame_fail_{timestamp}.jpg"
        result_label = "NO_VALIDO"
    image_path = os.path.join(output_dir, filename)
    cv2.imwrite(image_path, frame_bgr)
    payload_with_result = dict(payload)
    payload_with_result["resultado"] = result_label
    json_path = os.path.join(output_dir, filename.replace(".jpg", ".json"))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload_with_result, fh, ensure_ascii=False, indent=2)
    return image_path, json_path


def save_lift_event_db(athlete_id, lift_type, event_type, image_path, result, reason, metrics, attempt_id=None):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            INSERT INTO lift_events (athlete_id, attempt_id, lift_type, event_type, image_path, result, reason, metrics_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                athlete_id,
                attempt_id,
                lift_type,
                event_type,
                image_path,
                result,
                reason,
                json.dumps(metrics),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()


def analyze_attempt(video_path, lift_type):
    abs_video_path = os.path.join(os.path.dirname(__file__), video_path.lstrip("/"))
    cap = cv2.VideoCapture(abs_video_path)
    if not cap.isOpened():
        return {
            "ai_result": "Nulo",
            "fail_reason": None,
            "body_angles": {},
            "image_path": None,
        }

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_every = max(1, total_frames // 20) if total_frames else 5
    frame_index = 0
    metrics = {"knee_min": None, "hip_min": None, "elbow_min": None}
    key_frame = None

    base_ts = int(time.monotonic() * 1000)
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_index % sample_every == 0:
            timestamp_ms = base_ts + frame_index * 33
            try:
                result = detect_pose_from_frame(frame, timestamp_ms)
            except RuntimeError as exc:
                print(f"PoseLandmarker error en analyze_attempt: {exc}")
                cap.release()
                return {
                    "ai_result": "Nulo",
                    "fail_reason": None,
                    "body_angles": {},
                    "image_path": None,
                }
            landmarks = get_primary_landmarks(result)
            if landmarks:
                frame_metrics = build_pose_metrics(landmarks)
                update_attempt_metrics(metrics, frame_metrics)
                if key_frame is None:
                    key_frame = draw_pose_overlay(frame.copy(), landmarks, frame_metrics)

        frame_index += 1

    cap.release()

    if key_frame is None:
        return {
            "ai_result": "Nulo",
            "fail_reason": None,
            "body_angles": metrics,
            "image_path": None,
        }

    image_path = save_frame_image(key_frame, "attempt")

    ai_result = evaluate_lift_metrics(lift_type, metrics)
    return {
        "ai_result": ai_result,
        "fail_reason": None,
        "body_angles": metrics,
        "image_path": image_path,
    }


CAMERA_DEVICE_PRIORITY = [
    "/dev/video2",
    "/dev/video3",
    "/dev/video0",
    "/dev/video1",
]


def _normalize_camera_candidates(preferred=None):
    candidates = []
    if isinstance(preferred, str) and preferred.startswith("/dev/video"):
        candidates.append(preferred)
    for device_path in CAMERA_DEVICE_PRIORITY:
        if device_path not in candidates:
            candidates.append(device_path)
    return candidates


def open_camera(preferred=None):
    candidates = _normalize_camera_candidates(preferred)
    errors = []

    for device_path in candidates:
        if not os.path.exists(device_path):
            errors.append(f"{device_path}: no existe")
            continue

        cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
        if not cap.isOpened():
            errors.append(f"{device_path}: no abre con V4L2")
            cap.release()
            continue

        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            print(f"[camera] Usando {device_path} con backend V4L2")
            return cap

        errors.append(f"{device_path}: abre pero no entrega frames validos")
        cap.release()

    raise RuntimeError("No se pudo abrir ninguna camara disponible. " + " | ".join(errors))


def get_camera(preferred=None):
    return open_camera(preferred)


@app.route("/")
def home():
    if require_login():
        return redirect("/login")
    status = request.args.get("status")
    with SessionLocal() as db:
        athletes = db.query(Athlete).order_by(Athlete.last_name.asc()).all()
    return render_template(
        "camera.html",
        username=session.get("user"),
        active_page="camera",
        page_title="Panel",
        status=status,
        athletes=athletes,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    company_logo = "/static/images/evolution-machine.jpg" if os.path.isfile(
        os.path.join(app.static_folder, "images", "evolution-machine.jpg")
    ) else None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user(username)
        if user and user["is_active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user"] = username
            return redirect("/dashboard")
        return render_template("login.html", error="Credenciales invalidas.", company_logo=company_logo)
    return render_template("login.html", error=None, company_logo=company_logo)


@app.route("/register", methods=["GET", "POST"])
def register():
    if require_login():
        return redirect("/login")
    if get_user(session["user"])["role"] != "admin":
        abort(403)
    return redirect("/settings")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/dashboard")
def dashboard():
    if require_login():
        return redirect("/login")
    athlete_id = request.args.get("athlete_id", type=int)
    lift_type = request.args.get("lift_type", "")
    if lift_type not in {"Squat", "Bench", "Deadlift"}:
        lift_type = ""
    with SessionLocal() as db:
        athletes = db.query(Athlete).order_by(Athlete.last_name, Athlete.first_name).all()
        query = db.query(Attempt, Athlete).join(Athlete, Athlete.id == Attempt.athlete_id)
        if athlete_id is not None:
            query = query.filter(Attempt.athlete_id == athlete_id)
        if lift_type:
            query = query.filter(Attempt.lift_type == lift_type)
        records = query.order_by(Attempt.id.asc()).all()
    counts = {"Bueno": 0, "Nulo": 0, "Sin evaluar": 0}
    groups = {}
    trend = []
    good = evaluated = 0
    for attempt, athlete in records:
        result = attempt.ai_result if attempt.ai_result in counts else "Sin evaluar"
        counts[result] += 1
        key = (athlete.id, attempt.lift_type)
        if key not in groups:
            groups[key] = {"athlete_id": athlete.id, "athlete": f"{athlete.first_name} {athlete.last_name}",
                           "movement": attempt.lift_type, "Bueno": 0, "Nulo": 0, "Sin evaluar": 0}
        groups[key][result] += 1
        if result in {"Bueno", "Nulo"}:
            evaluated += 1
            good += int(result == "Bueno")
            trend.append({"attempt": attempt.id, "rate": round(good * 100 / evaluated, 1)})
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (-(group["Bueno"] + group["Nulo"] + group["Sin evaluar"]),
                           group["athlete"].casefold(), group["athlete_id"], group["movement"]),
    )
    statistics = {"counts": counts, "groups": ordered_groups, "trend": trend[-30:],
                  "total": len(records), "evaluated": evaluated,
                  "success_rate": round(good * 100 / evaluated, 1) if evaluated else None}
    return render_template(
        "dashboard.html", username=session.get("user"), active_page="dashboard", page_title="Inicio",
        attempts=list(reversed(records[-5:])), athletes=athletes, athlete_id=athlete_id,
        lift_type=lift_type, statistics=statistics,
    )


@app.route("/results")
def results():
    if require_login():
        return redirect("/login")
    athlete_id = request.args.get("athlete_id", type=int)
    lift_type = request.args.get("lift_type", "")
    result = request.args.get("result", "")
    if lift_type not in {"Squat", "Bench", "Deadlift"}:
        lift_type = ""
    if result not in {"Bueno", "Nulo", "Sin evaluar"}:
        result = ""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    with SessionLocal() as db:
        athletes = db.query(Athlete).order_by(Athlete.last_name, Athlete.first_name).all()
        query = db.query(Attempt, Athlete).join(Athlete, Athlete.id == Attempt.athlete_id)
        if athlete_id is not None:
            query = query.filter(Attempt.athlete_id == athlete_id)
        if lift_type:
            query = query.filter(Attempt.lift_type == lift_type)
        if result:
            query = query.filter(Attempt.ai_result == result)
        total = query.count()
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        attempts = query.order_by(Attempt.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
        summary = query.with_entities(
            Athlete.id, Athlete.first_name, Athlete.last_name, Athlete.document_id,
            Attempt.lift_type,
            func.count(Attempt.id).label("total"),
            func.sum(case((Attempt.ai_result == "Bueno", 1), else_=0)).label("good"),
            func.sum(case((Attempt.ai_result == "Nulo", 1), else_=0)).label("null"),
            func.sum(case((Attempt.ai_result == "Sin evaluar", 1), else_=0)).label("unevaluated"),
        ).group_by(Athlete.id, Athlete.first_name, Athlete.last_name, Athlete.document_id, Attempt.lift_type
                   ).order_by(Athlete.last_name, Athlete.first_name, Athlete.id, Attempt.lift_type).all()
    return render_template(
        "results.html", username=session.get("user"), active_page="results", page_title="Resultados",
        athletes=athletes, attempts=attempts, summary=summary, total=total, page=page, pages=pages,
        athlete_id=athlete_id, lift_type=lift_type, selected_result=result,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if require_login():
        return redirect("/login")
    current_user = get_user(session["user"])
    is_admin = current_user["role"] == "admin"
    session.setdefault("users_csrf", secrets.token_hex(32))
    error = None
    status_code = 200
    if request.method == "POST":
        if not is_admin:
            abort(403)
        if not secrets.compare_digest(session["users_csrf"], request.form.get("csrf_token", "")):
            abort(400)
        action = request.form.get("action")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")
        user_id = request.form.get("user_id", type=int)
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if action not in {"create", "update", "toggle"}:
                error = "Acción inválida."
            elif action != "create" and not target:
                error = "El usuario no existe."
            elif action in {"create", "update"} and (not username or len(username) > 80 or role not in {"admin", "user"}):
                error = "Indica un usuario de hasta 80 caracteres y un permiso válido."
            elif action == "create" and not password:
                error = "La contraseña es obligatoria para crear un usuario."
            elif password and len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres."
            elif target and target["id"] == current_user["id"] and (action == "toggle" or role != "admin"):
                error = "No puedes desactivar tu propia cuenta ni quitarte el permiso de administrador."
            if not error:
                try:
                    if action == "create":
                        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                                     (username, generate_password_hash(password), role))
                    elif action == "update":
                        conn.execute("UPDATE users SET username = ?, role = ? WHERE id = ?", (username, role, user_id))
                        if password:
                            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
                    else:
                        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(not target["is_active"]), user_id))
                    conn.commit()
                    if action == "update" and user_id == current_user["id"]:
                        session["user"] = username
                    flash("Usuario creado." if action == "create" else "Usuario actualizado." if action == "update" else "Estado de acceso actualizado.")
                    return redirect("/settings")
                except sqlite3.IntegrityError:
                    conn.rollback()
                    error = "El nombre de usuario ya existe."
        status_code = 400
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        users = conn.execute("SELECT id, username, role, is_active FROM users ORDER BY id").fetchall() if is_admin else []
    return render_template(
        "settings.html", username=session.get("user"), active_page="settings",
        page_title="Configuración", users=users, is_admin=is_admin,
        current_user_id=current_user["id"], error=error,
    ), status_code


@app.route("/athletes/new", methods=["GET", "POST"])
def athlete_create():
    if require_login():
        return redirect("/login")
    status = request.args.get("status")
    if request.method == "POST":
        form_data = {
            "document_id": request.form.get("document_id", "").strip(),
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "age": request.form.get("age", "").strip(),
            "sex": request.form.get("sex", "").strip(),
            "body_weight": request.form.get("body_weight", "").strip(),
            "category": request.form.get("category", "").strip(),
            "level": request.form.get("level", "").strip(),
            "years_training": request.form.get("years_training", "").strip(),
            "notes": request.form.get("notes", "").strip(),
        }
        errors = []
        for field, label in (
            ("document_id", "ID"),
            ("first_name", "Nombre"),
            ("last_name", "Apellido"),
            ("age", "Edad"),
            ("sex", "Sexo"),
            ("body_weight", "Peso corporal"),
            ("category", "Categoria"),
            ("level", "Nivel"),
            ("years_training", "Anos entrenando"),
        ):
            if not form_data[field]:
                errors.append(f"{label} es obligatorio.")

        age = None
        if form_data["age"]:
            try:
                age = int(form_data["age"])
                if age <= 0:
                    errors.append("Edad debe ser un numero positivo.")
            except ValueError:
                errors.append("Edad debe ser un numero valido.")

        body_weight = None
        if form_data["body_weight"]:
            try:
                body_weight = float(form_data["body_weight"])
                if body_weight <= 0:
                    errors.append("Peso corporal debe ser un numero positivo.")
            except ValueError:
                errors.append("Peso corporal debe ser un numero valido.")

        years_training = None
        if form_data["years_training"]:
            try:
                years_training = int(form_data["years_training"])
                if years_training < 0:
                    errors.append("Anos entrenando no puede ser negativo.")
            except ValueError:
                errors.append("Anos entrenando debe ser un numero valido.")

        if not errors:
            with SessionLocal() as db:
                db.add(
                    Athlete(
                        document_id=form_data["document_id"],
                        first_name=form_data["first_name"],
                        last_name=form_data["last_name"],
                        age=age,
                        sex=form_data["sex"],
                        body_weight=body_weight,
                        category=form_data["category"],
                        level=form_data["level"],
                        years_training=years_training,
                        notes=form_data["notes"] or None,
                    )
                )
                db.commit()
            return redirect("/athletes/new?status=Atleta+registrado+correctamente.")

        return render_template(
            "athlete_create.html",
            username=session.get("user"),
            active_page="athlete_create",
            page_title="Agregar atleta",
            status=None,
            form_data=form_data,
            errors=errors,
        )

    return render_template(
        "athlete_create.html",
        username=session.get("user"),
        active_page="athlete_create",
        page_title="Agregar atleta",
        status=status,
        form_data={},
        errors=[],
    )


@app.route("/attempts/auto", methods=["POST"])
def attempt_auto():
    if require_login():
        return jsonify(error="unauthorized"), 401
    athlete_id = request.form.get("athlete_id", "").strip()
    lift_type = request.form.get("lift_type", "").strip()
    video_file = request.files.get("video")

    errors = []
    if not athlete_id:
        errors.append("Atleta es obligatorio.")
    if not lift_type:
        errors.append("Tipo es obligatorio.")
    if not video_file or not video_file.filename:
        errors.append("Video es obligatorio.")

    try:
        athlete_id_int = int(athlete_id)
    except ValueError:
        errors.append("Atleta invalido.")
        athlete_id_int = None

    valid_types = {"Squat", "Bench", "Deadlift"}
    if lift_type and lift_type not in valid_types:
        errors.append("Tipo no es valido.")

    if errors:
        return jsonify(error="; ".join(errors)), 400

    with SessionLocal() as db:
        if not db.get(Athlete, athlete_id_int):
            return jsonify(error="Atleta no existe."), 400

    video_path = save_video_blob(video_file)
    if not video_path:
        return jsonify(error="No se pudo guardar el video."), 400

    analysis = analyze_attempt(video_path, lift_type)
    analysis["fail_reason"] = public_reason(analysis["fail_reason"])
    image_path = analysis["image_path"]
    if not image_path:
        image_path = save_frame_image(np.zeros((480, 640, 3), dtype=np.uint8), "fallback")

    with SessionLocal() as db:
        attempt = Attempt(
            athlete_id=athlete_id_int,
            lift_type=lift_type,
            weight=None,
            video_path=video_path,
            image_path=image_path,
            ai_result=analysis["ai_result"],
            fail_reason=analysis["fail_reason"],
            body_angles=json.dumps(analysis["body_angles"]),
        )
        db.add(attempt)
        db.commit()
        attempt_id = attempt.id

    return jsonify(
        attempt_id=attempt_id,
        ai_result=analysis["ai_result"],
        fail_reason=analysis["fail_reason"],
        body_angles=analysis["body_angles"],
        image_path=image_path,
    )


def realtime_pose_stream(camera_index=None, lift_type=None, athlete_id=None, view=None, recording=None):
    try:
        cap = open_camera(camera_index)
    except RuntimeError as exc:
        if recording:
            recording.finish(reason="No se pudo abrir la cámara")
        with LATEST_POSE_RESULT_LOCK:
            LATEST_POSE_RESULT.update(
                updated_at=time.time(), lift_type=lift_type or "", athlete_id=athlete_id,
                has_pose=False, body_angles={}, landmarks=[], phase=None, status=None,
                reason=None, result=None, error="Cámara no disponible",
            )
        return

    frame_idx = 0
    base_ts = int(time.monotonic() * 1000)
    metrics_min = {"knee_min": None, "hip_min": None, "elbow_min": None}
    deadlift_state = DeadliftState()
    bench_state = BenchStateMachine(view=view or "frontal")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            timestamp_ms = base_ts + frame_idx * 33
            frame_idx += 1
            has_pose = False
            metrics = {"knee_angle": None, "hip_angle": None, "elbow_angle": None}
            landmark_data = []
            result_label = None
            overlay_info = None
            pose_error = None
            reason = None
            try:
                pose_result = detect_pose_from_frame(frame, timestamp_ms)
                landmarks = get_primary_landmarks(pose_result)
                if landmarks:
                    has_pose = True
                    if lift_type == "Deadlift":
                        deadlift_result = evaluate_deadlift(landmarks, timestamp_ms, deadlift_state)
                        metrics = deadlift_result.metrics
                        reason = deadlift_result.reason
                        overlay_info = {
                            "exercise": "Deadlift",
                            "phase": deadlift_result.phase.value,
                            "status": deadlift_result.status,
                        }
                        result_label = (
                            "Bueno" if deadlift_result.status == "VALIDO"
                            else "Nulo" if deadlift_result.status == "NO_VALIDO" else None
                        )
                    elif lift_type == "Bench":
                        bench_result = bench_state.update(landmarks, timestamp_ms)
                        metrics = bench_result.metrics
                        reason = bench_result.reason
                        overlay_info = {
                            "exercise": "Bench",
                            "phase": bench_result.phase.value,
                            "status": bench_result.status,
                            "lines": [
                                f"Wrist norm: {metrics.get('wrist_height_norm', None):.3f}"
                                if metrics.get("wrist_height_norm") is not None
                                else "Wrist norm: -",
                                f"Scale len: {metrics.get('scale_len', None):.3f}"
                                if metrics.get("scale_len") is not None
                                else "Scale len: -",
                                f"Elbow L: {metrics.get('elbow_angle_l', None):.1f}"
                                if metrics.get("elbow_angle_l") is not None
                                else "Elbow L: -",
                                f"Elbow R: {metrics.get('elbow_angle_r', None):.1f}"
                                if metrics.get("elbow_angle_r") is not None
                                else "Elbow R: -",
                                f"Motivo: {bench_result.reason}"
                                if bench_result.reason
                                else "",
                            ],
                        }
                        result_label = (
                            "Bueno" if bench_result.status == "VALIDO"
                            else "Nulo" if bench_result.status == "NO_VALIDO" else None
                        )
                    else:
                        metrics = build_pose_metrics(landmarks)
                        landmark_data = serialize_landmarks(landmarks)
                        update_attempt_metrics(metrics_min, metrics)
                        if lift_type in {"Squat", "Bench", "Deadlift"}:
                            result_label = evaluate_lift_metrics(lift_type, metrics_min)
                    frame = draw_pose_overlay(
                        frame, landmarks, metrics, result_label=result_label, overlay_info=overlay_info,
                        show_text=False,
                    )
            except RuntimeError as exc:
                print(f"PoseLandmarker error en realtime_pose_stream: {exc}")
                pose_error = "Análisis automático no disponible en este momento."

            if recording:
                recording.process(
                    frame, has_pose, metrics,
                    phase=(overlay_info or {}).get("phase"),
                    status=(overlay_info or {}).get("status"), reason=reason,
                )
                saved_result = recording.snapshot().get("result")
                result_label = saved_result["ai_result"] if saved_result and recording.mode == "automatic" else None

            with LATEST_POSE_RESULT_LOCK:
                # Estado compartido para consultas JSON en /api/pose/latest.
                LATEST_POSE_RESULT["timestamp_ms"] = timestamp_ms
                LATEST_POSE_RESULT["has_pose"] = has_pose
                LATEST_POSE_RESULT["body_angles"] = metrics
                LATEST_POSE_RESULT["landmarks"] = landmark_data
                LATEST_POSE_RESULT.update(
                    updated_at=time.time(), lift_type=lift_type or "", athlete_id=athlete_id,
                    phase=(overlay_info or {}).get("phase"),
                    status=(overlay_info or {}).get("status"), reason=reason,
                    result=result_label, error=pose_error,
                )

            ret, jpg = cv2.imencode(".jpg", frame)
            if not ret:
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            )
    finally:
        cap.release()
        if recording:
            recording.finish(reason="La cámara se detuvo antes de completar la grabación")


@app.route("/pose/stream")
def pose_stream():
    if require_login():
        return redirect("/login")
    camera_index = request.args.get("camera", default=None, type=str)
    lift_type = request.args.get("lift_type", default="", type=str)
    athlete_id = request.args.get("athlete_id", default=None, type=str)
    view = request.args.get("view", default="frontal", type=str)
    recording_id = request.args.get("recording_id", "")
    recording = None
    if recording_id:
        with RECORDINGS_LOCK:
            recording = RECORDINGS.get(recording_id)
        if not recording or recording.owner != session.get("user"):
            return jsonify(error="Grabación no encontrada"), 404
        athlete_id = str(recording.athlete_id)
        lift_type = recording.lift_type
    return Response(
        realtime_pose_stream(
            camera_index=camera_index,
            lift_type=lift_type,
            athlete_id=athlete_id,
            view=view,
            recording=recording,
        ),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/video_feed")
def video_feed():
    if require_login():
        return redirect("/login")
    camera_index = request.args.get("camera", default=None, type=str)
    lift_type = request.args.get("lift_type", default="", type=str)
    athlete_id = request.args.get("athlete_id", default=None, type=str)
    view = request.args.get("view", default="frontal", type=str)
    return Response(
        realtime_pose_stream(
            camera_index=camera_index,
            lift_type=lift_type,
            athlete_id=athlete_id,
            view=view,
        ),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


def persist_recording(recording, frame, result, reason, metrics):
    reason = public_reason(reason)
    image_path = save_frame_image(frame, "automatic" if recording.mode == "automatic" else "recording")
    video_path = f"/uploads/attempts/videos/{recording.video_filename}"
    with SessionLocal() as db:
        attempt = Attempt(
            athlete_id=recording.athlete_id, lift_type=recording.lift_type,
            weight=None, video_path=video_path, image_path=image_path,
            ai_result=result, fail_reason=reason, body_angles=json.dumps(metrics),
        )
        db.add(attempt)
        db.commit()
        attempt_id = attempt.id
    return {
        "attempt_id": attempt_id, "ai_result": result, "fail_reason": reason,
        "body_angles": metrics, "image_path": image_path, "video_path": video_path,
    }


@app.route("/api/recordings", methods=["POST"])
def create_recording():
    if require_login():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    if data.get("mode") not in {"automatic", "normal"}:
        return jsonify(error="Selecciona un modo válido."), 400
    if data.get("lift_type") not in {"Squat", "Bench", "Deadlift"}:
        return jsonify(error="Selecciona un tipo de levantamiento."), 400
    try:
        athlete_id = int(data.get("athlete_id", ""))
    except (ValueError, TypeError):
        return jsonify(error="Selecciona un atleta."), 400
    with SessionLocal() as db:
        if not db.get(Athlete, athlete_id):
            return jsonify(error="El atleta no existe."), 400
    with RECORDINGS_LOCK:
        # Keep recent completed results without allowing an unbounded registry.
        expired = [key for key, value in RECORDINGS.items()
                   if value.state in {"done", "cancelled", "error"}
                   and time.monotonic() - value.created_at > 3600]
        for key in expired:
            del RECORDINGS[key]
        if any(value.owner == session.get("user") and value.state in {"waiting", "starting", "recording"}
               for value in RECORDINGS.values()):
            return jsonify(error="Detén la grabación actual antes de iniciar otra."), 409
        recording = LiftRecording(
            session.get("user"), athlete_id, data["lift_type"], data["mode"],
            os.path.join(ATTEMPT_UPLOAD_DIR, "videos"), persist_recording,
        )
        RECORDINGS[recording.id] = recording
    return jsonify(recording.snapshot()), 201


@app.route("/api/recordings/<recording_id>", methods=["GET", "POST"])
def recording_status(recording_id):
    if require_login():
        return jsonify(error="unauthorized"), 401
    with RECORDINGS_LOCK:
        recording = RECORDINGS.get(recording_id)
    if not recording or recording.owner != session.get("user"):
        return jsonify(error="Grabación no encontrada."), 404
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if data.get("action") != "stop":
            return jsonify(error="Acción no válida."), 400
        recording.finish(reason="Grabación detenida por el usuario")
    return jsonify(recording.snapshot())


@app.route("/uploads/attempts/<kind>/<filename>")
def recording_file(kind, filename):
    if require_login():
        return redirect("/login")
    if kind not in {"images", "videos"}:
        return jsonify(error="Archivo no encontrado"), 404
    return send_from_directory(os.path.join(ATTEMPT_UPLOAD_DIR, kind), filename)


@app.route("/api/pose/latest")
def api_pose_latest():
    if require_login():
        return jsonify(error="unauthorized"), 401
    with LATEST_POSE_RESULT_LOCK:
        payload = {
            "timestamp_ms": LATEST_POSE_RESULT["timestamp_ms"],
            "has_pose": LATEST_POSE_RESULT["has_pose"],
            "body_angles": dict(LATEST_POSE_RESULT["body_angles"]),
            "landmarks": list(LATEST_POSE_RESULT["landmarks"]),
            **{key: LATEST_POSE_RESULT[key] for key in (
                "updated_at", "lift_type", "athlete_id", "phase", "status", "reason", "result", "error"
            )},
        }
    return jsonify(payload)


@app.route("/api/pose/evaluate")
def api_pose_evaluate():
    if require_login():
        return jsonify(error="unauthorized"), 401
    lift_type = request.args.get("lift_type", "").strip()
    if lift_type not in {"Squat", "Bench", "Deadlift"}:
        return jsonify(error="lift_type invalido"), 400
    with LATEST_POSE_RESULT_LOCK:
        angles = dict(LATEST_POSE_RESULT["body_angles"])
        has_pose = bool(LATEST_POSE_RESULT["has_pose"])
    metrics = {
        "knee_min": angles.get("knee_angle"),
        "hip_min": angles.get("hip_angle"),
        "elbow_min": angles.get("elbow_angle"),
    }
    label = evaluate_lift_metrics(lift_type, metrics) if has_pose else "Nulo"
    return jsonify(ai_result=label, body_angles=angles, has_pose=has_pose)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
