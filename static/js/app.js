const recordBtn = document.getElementById("recordBtn");
const statusLabel = document.getElementById("statusLabel");
const resultLabel = document.getElementById("resultLabel");
const athleteSelect = document.getElementById("athleteSelect");
const liftType = document.getElementById("liftType");
const disableCamera = document.getElementById("disableCamera");
const poseStream = document.getElementById("poseStream");
const captureFrame = document.getElementById("captureFrame");
const capturePlaceholder = document.getElementById("capturePlaceholder");
const cameraPlaceholder = document.getElementById("cameraPlaceholder");
const cameraPlaceholderTitle = document.getElementById("cameraPlaceholderTitle");
const cameraPlaceholderDetail = document.getElementById("cameraPlaceholderDetail");
let cameraTimer;
let analysisTimer;
let analysisController;
let analysisGeneration = 0;
let currentRecording = null;
let recordingTimer;
let recordingBusy = false;
const recordingMode = document.getElementById("recordingMode");
const recordingHelp = document.getElementById("recordingHelp");
const recordingFeedback = document.getElementById("recordingFeedback");
const recordingVideo = document.getElementById("recordingVideo");
const automaticCaptureResult = document.getElementById("automaticCaptureResult");

function selectedRecordingMode() {
  return document.querySelector('input[name="recordingMode"]:checked').value;
}

function isRecordingActive() {
  return currentRecording && ["waiting", "starting", "recording"].includes(currentRecording.state);
}

function updateRecordingControls() {
  const active = isRecordingActive();
  athleteSelect.disabled = Boolean(active || recordingBusy);
  liftType.disabled = Boolean(active || recordingBusy);
  recordingMode.disabled = Boolean(active || recordingBusy);
  recordBtn.disabled = recordingBusy || (isCameraDisabled() && !active);
  recordBtn.textContent = active
    ? (currentRecording.state === "waiting" ? "Cancelar automático" : "Detener grabación")
    : (selectedRecordingMode() === "automatic" ? "Activar automático" : "Iniciar grabación");
  recordingHelp.textContent = selectedRecordingMode() === "automatic"
    ? "Activa el modo automático y colócate en la postura inicial. Cada activación registra un intento."
    : "Pulsa Iniciar grabación y luego Detener grabación para guardar el video.";
}

function showRecordingFeedback(text) {
  recordingFeedback.textContent = text;
  recordingFeedback.hidden = !text;
}

function renderRecording(data) {
  currentRecording = data;
  setStatus(data.message);
  showRecordingFeedback(data.error || data.message);
  updateRecordingControls();
  if (data.state === "done" && data.result) {
    const result = data.result;
    resultLabel.textContent = result.ai_result;
    recordingVideo.href = result.video_path;
    recordingVideo.hidden = false;
    if (data.mode === "automatic") {
      captureFrame.src = result.image_path;
      captureFrame.hidden = false;
      capturePlaceholder.hidden = true;
      automaticCaptureResult.textContent = `Resultado: ${result.ai_result}${result.fail_reason ? ` · ${result.fail_reason}` : ""}`;
      automaticCaptureResult.hidden = false;
    }
  }
}

async function recordingRequest(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo actualizar la grabación.");
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

function pollRecording(id) {
  clearTimeout(recordingTimer);
  recordingTimer = setTimeout(async () => {
    try {
      const data = await recordingRequest(`/api/recordings/${id}`);
      if (!currentRecording || currentRecording.id !== id) return;
      renderRecording(data);
      if (isRecordingActive()) pollRecording(id);
    } catch (error) {
      showRecordingFeedback("No se pudo consultar la grabación. Intentando reconectar…");
      if (currentRecording && currentRecording.id === id && isRecordingActive()) pollRecording(id);
    }
  }, 500);
}

async function stopRecording() {
  if (!isRecordingActive()) return;
  const data = await recordingRequest(`/api/recordings/${currentRecording.id}`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action: "stop"}),
  });
  clearTimeout(recordingTimer);
  renderRecording(data);
}

if (recordBtn) {
  recordBtn.addEventListener("click", async () => {
    recordingBusy = true;
    updateRecordingControls();
    try {
      if (isRecordingActive()) {
        await stopRecording();
      } else {
        if (isCameraDisabled()) throw new Error("Habilita la cámara antes de registrar un intento.");
        if (!athleteSelect.value || !liftType.value) throw new Error("Selecciona atleta y tipo de levantamiento.");
        const data = await recordingRequest("/api/recordings", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({athlete_id: athleteSelect.value, lift_type: liftType.value, mode: selectedRecordingMode()}),
        });
        currentRecording = data;
        resultLabel.textContent = "—";
        recordingVideo.hidden = true;
        if (data.mode === "automatic") {
          captureFrame.hidden = true;
          captureFrame.removeAttribute("src");
          capturePlaceholder.hidden = false;
          automaticCaptureResult.hidden = true;
        }
        setCameraState(false);
        renderRecording(data);
        pollRecording(data.id);
      }
    } catch (error) {
      showRecordingFeedback(error.name === "AbortError" ? "La solicitud tardó demasiado. Comprueba la conexión." : error.message);
    } finally {
      recordingBusy = false;
      updateRecordingControls();
    }
  });
}

if (recordingMode) recordingMode.addEventListener("change", updateRecordingControls);

if (disableCamera) {
  disableCamera.addEventListener("change", async () => {
    recordingBusy = true;
    updateRecordingControls();
    try {
      if (disableCamera.checked) await stopRecording();
      setCameraState(disableCamera.checked);
    } catch (error) {
      disableCamera.checked = false;
      showRecordingFeedback(error.message);
    } finally {
      recordingBusy = false;
      updateRecordingControls();
    }
  });
}

window.addEventListener("pagehide", () => {
  if (isRecordingActive()) {
    navigator.sendBeacon(`/api/recordings/${currentRecording.id}`, new Blob([JSON.stringify({action: "stop"})], {type: "application/json"}));
  }
});
const poseDetection = document.getElementById("poseDetection");
const poseAngleElements = document.querySelectorAll("[data-pose-angle]");
const posePhase = document.getElementById("posePhase");
const poseLiftStatus = document.getElementById("poseLiftStatus");
const poseLiveResult = document.getElementById("poseLiveResult");
const poseReason = document.getElementById("poseReason");
const poseLabels = {
  SETUP: "Preparación", PULL: "Ascenso", LOCKOUT: "Bloqueo", DOWN: "Descenso",
  IDLE: "En espera", UNRACK: "Salida del soporte", DESCENT: "Descenso",
  CHEST: "Pausa en el pecho", ASCENT: "Ascenso", RACK: "Regreso al soporte",
  END: "Finalizado", VALIDO: "Válido", NO_VALIDO: "No válido", PENDIENTE: "Pendiente",
};

function renderPoseAnalysis(data, message) {
  if (!poseDetection) return;
  const hasPose = Boolean(data && data.has_pose);
  const angles = hasPose ? data.body_angles || {} : {};
  poseDetection.textContent = message || (data.error || (hasPose ? "Postura detectada" : "Sin postura detectada"));
  poseAngleElements.forEach(element => {
    const key = element.dataset.poseAngle;
    const value = angles[key];
    element.textContent = typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}°` : "—";
    if (key === "elbow_angle_l" || key === "elbow_angle_r") {
      element.parentElement.hidden = liftType.value !== "Bench";
    }
  });
  const label = value => poseLabels[value] || value || "—";
  posePhase.textContent = hasPose ? label(data.phase) : "—";
  poseLiftStatus.textContent = hasPose ? label(data.status) : "—";
  poseLiveResult.textContent = hasPose ? label(data.result) : "—";
  poseReason.textContent = hasPose ? data.reason || "" : "";
  poseReason.hidden = !poseReason.textContent;
}

function restartPoseAnalysis(disabled) {
  clearTimeout(analysisTimer);
  if (analysisController) analysisController.abort();
  const generation = ++analysisGeneration;
  const requestedLift = liftType.value;
  const requestedAthlete = athleteSelect.value;
  const startedAt = Date.now() / 1000;
  renderPoseAnalysis(null, disabled ? "Cámara deshabilitada" : "Esperando datos de la cámara");
  if (disabled || !poseDetection) return;
  async function poll() {
    const controller = new AbortController();
    analysisController = controller;
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch("/api/pose/latest", { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error("Datos no disponibles");
      const data = await response.json();
      if (generation !== analysisGeneration) return;
      const matches = (data.lift_type || "") === requestedLift && String(data.athlete_id || "") === requestedAthlete;
      const fresh = typeof data.updated_at === "number" && data.updated_at >= startedAt && Math.abs(Date.now() / 1000 - data.updated_at) < 3;
      if (matches && fresh) {
        renderPoseAnalysis(data);
        if (data.error === "Cámara no disponible") {
          showCameraPlaceholder("Cámara no disponible", "Activa o conecta la cámara para mostrar la vista en tiempo real.");
        }
      } else {
        renderPoseAnalysis(null, "Esperando datos de la cámara");
      }
    } catch (error) {
      if (generation === analysisGeneration) renderPoseAnalysis(null, "Análisis no disponible por el momento");
    } finally {
      clearTimeout(timeout);
      if (generation === analysisGeneration) analysisTimer = setTimeout(poll, 500);
    }
  }
  poll();
}

function showCameraPlaceholder(title, detail) {
  poseStream.hidden = true;
  cameraPlaceholder.hidden = false;
  cameraPlaceholderTitle.textContent = title;
  cameraPlaceholderDetail.textContent = detail;
}

if (poseStream) {
  poseStream.addEventListener("load", () => {
    if (isCameraDisabled() || !poseStream.getAttribute("src")) return;
    poseStream.hidden = false;
    cameraPlaceholder.hidden = true;
  });
  poseStream.addEventListener("error", () => {
    if (isCameraDisabled() || !poseStream.getAttribute("src")) return;
    showCameraPlaceholder("Cámara no disponible", "Activa o conecta la cámara para mostrar la vista en tiempo real.");
  });
}

function setStatus(text) {
  if (statusLabel) {
    statusLabel.textContent = text;
  }
}

function isCameraDisabled() {
  return Boolean(disableCamera && disableCamera.checked);
}

function setCameraState(disabled) {
  if (!poseStream) return;
  clearTimeout(cameraTimer);
  restartPoseAnalysis(disabled);
  if (disabled) {
    poseStream.dataset.prevSrc = poseStream.src || "/pose/stream";
    showCameraPlaceholder("Cámara deshabilitada", "Desmarca «Deshabilitar cámara» para activar la vista en tiempo real.");
    poseStream.removeAttribute("src");
    setStatus("Camara deshabilitada");
    return;
  }
  const baseSrc = poseStream.dataset.prevSrc || "/pose/stream";
  const liftParam = liftType && liftType.value ? `&lift_type=${encodeURIComponent(liftType.value)}` : "";
  const viewParam = `&view=frontal`;
  const athleteParam = athleteSelect && athleteSelect.value
    ? `&athlete_id=${encodeURIComponent(athleteSelect.value)}`
    : "";
  showCameraPlaceholder("Preparando cámara", "La vista en tiempo real aparecerá aquí.");
  poseStream.removeAttribute("src");
  cameraTimer = setTimeout(() => {
    const recordingParam = isRecordingActive() ? `&recording_id=${encodeURIComponent(currentRecording.id)}` : "";
    poseStream.src = `${baseSrc.split("?")[0]}?t=${Date.now()}${liftParam}${viewParam}${athleteParam}${recordingParam}`;
  }, 250);
  poseStream.alt = "Stream de pose";
  setStatus("Listo");
}

if (liftType) {
  liftType.addEventListener("change", () => {
    if (!isCameraDisabled()) {
      setCameraState(false);
    }
  });
}

if (athleteSelect) {
  athleteSelect.addEventListener("change", () => {
    if (!isCameraDisabled()) {
      setCameraState(false);
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  setCameraState(isCameraDisabled());
  updateRecordingControls();
  if (capturePlaceholder && captureFrame) {
    const hasCapture = Boolean(captureFrame.getAttribute("src"));
    capturePlaceholder.hidden = hasCapture;
    captureFrame.hidden = !hasCapture;
  }
});
