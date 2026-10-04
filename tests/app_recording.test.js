const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function setup(mode = "automatic") {
  const elements = {};
  const timers = new Map();
  const windowEvents = {};
  let timerId = 0;
  let record = {id: "test-recording", mode, state: mode === "automatic" ? "waiting" : "starting", message: "Preparando"};
  const requests = [];
  const selectedMode = {value: mode};
  const context = vm.createContext({
    document: {
      getElementById(id) {
        return elements[id] ||= {
          value: "", checked: false, hidden: false, dataset: {}, attributes: {}, events: {},
          addEventListener(name, callback) { this.events[name] = callback; },
          getAttribute(name) { return this.attributes[name] || null; },
          removeAttribute(name) { delete this.attributes[name]; },
          get src() { return this.attributes.src || ""; },
          set src(value) { this.attributes.src = value; },
        };
      },
      querySelector() { return selectedMode; },
      querySelectorAll() { return []; },
    },
    window: {addEventListener(name, callback) { windowEvents[name] = callback; }},
    navigator: {sendBeacon() {}},
    Blob, AbortController, console,
    setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url, options = {}) {
      requests.push({url, options});
      if (url === "/api/pose/latest") return {ok: true, json: async () => ({updated_at: null})};
      if (url === "/api/recordings" && options.method === "POST") return {ok: true, json: async () => record};
      if (options.method === "POST") {
        record = {...record, state: "done", message: "Guardado", result: {
          ai_result: mode === "automatic" ? "Nulo" : "Sin evaluar", fail_reason: "Detenido",
          image_path: "/uploads/attempts/images/result.jpg", video_path: "/uploads/attempts/videos/result.webm",
        }};
      }
      return {ok: true, json: async () => record};
    },
  });
  vm.runInContext(fs.readFileSync("static/js/app.js", "utf8"), context);
  elements.athleteSelect.value = "1";
  elements.liftType.value = "Squat";
  windowEvents.DOMContentLoaded();
  return {elements, timers, requests, selectedMode};
}

test("automatic activation locks options and routes stream to its recording", async () => {
  const {elements, timers, requests} = setup();
  await elements.recordBtn.events.click();
  assert.equal(elements.recordBtn.textContent, "Cancelar automático");
  assert.equal(elements.athleteSelect.disabled, true);
  assert.equal(elements.recordingMode.disabled, true);
  const creation = requests.find(item => item.url === "/api/recordings");
  assert.equal(JSON.parse(creation.options.body).mode, "automatic");
  const cameraTimer = [...timers.values()].find(timer => timer.delay === 250);
  cameraTimer.fn();
  assert.match(elements.poseStream.src, /recording_id=test-recording/);
  assert.equal(elements.captureFrame.hidden, true);
});

test("stopping automatic recording shows its result and evidence", async () => {
  const {elements} = setup();
  await elements.recordBtn.events.click();
  await elements.recordBtn.events.click();
  assert.equal(elements.resultLabel.textContent, "Nulo");
  assert.equal(elements.captureFrame.src, "/uploads/attempts/images/result.jpg");
  assert.equal(elements.captureFrame.hidden, false);
  assert.equal(elements.capturePlaceholder.hidden, true);
  assert.equal(elements.automaticCaptureResult.hidden, false);
  assert.equal(elements.recordingVideo.hidden, false);
  assert.equal(elements.athleteSelect.disabled, false);
});

test("normal recording saves video without replacing automatic evidence", async () => {
  const {elements} = setup("normal");
  elements.captureFrame.src = "/previous-automatic.jpg";
  elements.captureFrame.hidden = false;
  assert.equal(elements.recordBtn.textContent, "Iniciar grabación");
  await elements.recordBtn.events.click();
  assert.equal(elements.recordBtn.textContent, "Detener grabación");
  await elements.recordBtn.events.click();
  assert.equal(elements.resultLabel.textContent, "Sin evaluar");
  assert.equal(elements.captureFrame.src, "/previous-automatic.jpg");
  assert.equal(elements.recordingVideo.hidden, false);
});

test("disabling camera stops an active recording first", async () => {
  const {elements, requests} = setup("normal");
  await elements.recordBtn.events.click();
  elements.disableCamera.checked = true;
  await elements.disableCamera.events.change();
  assert(requests.some(item => item.url === "/api/recordings/test-recording" && item.options.method === "POST"));
  assert.equal(elements.cameraPlaceholderTitle.textContent, "Cámara deshabilitada");
  assert.equal(elements.recordBtn.disabled, true);
  assert.equal(elements.poseStream.getAttribute("src"), null);
});
