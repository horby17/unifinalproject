const recordBtn = document.getElementById("recordBtn");
const statusLabel = document.getElementById("statusLabel");
const resultLabel = document.getElementById("resultLabel");
const athleteSelect = document.getElementById("athleteSelect");
const liftType = document.getElementById("liftType");
const disableCamera = document.getElementById("disableCamera");
const poseStream = document.getElementById("poseStream");
const captureFrame = document.getElementById("captureFrame");
const capturePlaceholder = document.getElementById("capturePlaceholder");

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
  if (disabled) {
    poseStream.dataset.prevSrc = poseStream.src || "/pose/stream";
    poseStream.src = "";
    poseStream.alt = "Camara deshabilitada";
    setStatus("Camara deshabilitada");
    return;
  }
  const baseSrc = poseStream.dataset.prevSrc || "/pose/stream";
  const liftParam = liftType && liftType.value ? `&lift_type=${encodeURIComponent(liftType.value)}` : "";
  const viewParam = `&view=frontal`;
  const athleteParam = athleteSelect && athleteSelect.value
    ? `&athlete_id=${encodeURIComponent(athleteSelect.value)}`
    : "";
  poseStream.src = "";
  setTimeout(() => {
    poseStream.src = `${baseSrc.split("?")[0]}?t=${Date.now()}${liftParam}${viewParam}${athleteParam}`;
  }, 250);
  poseStream.alt = "Stream de pose";
  setStatus("Listo");
}

async function evaluateCurrentPose() {
  const resp = await fetch(`/api/pose/evaluate?lift_type=${encodeURIComponent(liftType.value)}`);
  if (!resp.ok) {
    return { ai_result: "Nulo" };
  }
  const data = await resp.json();
  return { ai_result: data.ai_result || "Nulo" };
}

function captureCurrentFrame() {
  if (!poseStream || !poseStream.naturalWidth || !poseStream.naturalHeight) {
    return false;
  }
  const canvas = document.createElement("canvas");
  canvas.width = poseStream.naturalWidth;
  canvas.height = poseStream.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(poseStream, 0, 0, canvas.width, canvas.height);
  captureFrame.src = canvas.toDataURL("image/jpeg", 0.9);
  if (capturePlaceholder) {
    capturePlaceholder.classList.add("hidden");
  }
  if (captureFrame) {
    captureFrame.classList.remove("hidden");
  }
  return true;
}

async function captureAttempt() {
  if (isCameraDisabled()) {
    setStatus("Camara deshabilitada");
    resultLabel.textContent = "Nulo";
    return;
  }
  if (!athleteSelect.value || !liftType.value) {
    alert("Selecciona atleta y tipo antes de capturar.");
    return;
  }

  setStatus("Capturando");
  const frameOk = captureCurrentFrame();
  const evalResult = await evaluateCurrentPose();
  resultLabel.textContent = evalResult.ai_result === "Bueno" ? "Bueno" : "Nulo";
  setStatus(frameOk ? "Listo" : "Sin frame");
}

if (recordBtn) {
  recordBtn.addEventListener("click", () => {
    captureAttempt().catch((err) => {
      console.error(err);
      resultLabel.textContent = "Nulo";
      setStatus("Error");
    });
  });
}

if (disableCamera) {
  disableCamera.addEventListener("change", () => {
    setCameraState(disableCamera.checked);
  });
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
  if (captureFrame && !captureFrame.src) {
    captureFrame.classList.add("hidden");
  }
  if (capturePlaceholder && captureFrame && captureFrame.src) {
    capturePlaceholder.classList.add("hidden");
    captureFrame.classList.remove("hidden");
  }
});
