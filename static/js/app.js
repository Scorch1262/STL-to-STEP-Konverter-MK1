import * as THREE from "./vendor/three/three.module.min.js";
import { STLLoader } from "./vendor/three/STLLoader.js";
import { OrbitControls } from "./vendor/three/OrbitControls.js";

const STORAGE_KEY = "stl-step-konverter:last-job";

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const fileNameEl = document.getElementById("fileName");
const convertBtn = document.getElementById("convertBtn");

const progressSection = document.getElementById("progressSection");
const progressFill = document.getElementById("progressFill");
const progressMessage = document.getElementById("progressMessage");
const progressPercent = document.getElementById("progressPercent");

const resultBox = document.getElementById("resultBox");
const resultKind = document.getElementById("resultKind");
const resultFaces = document.getElementById("resultFaces");
const resultShapesRow = document.getElementById("resultShapesRow");
const resultShapes = document.getElementById("resultShapes");
const downloadLink = document.getElementById("downloadLink");

const errorBox = document.getElementById("errorBox");

const smoothing = document.getElementById("smoothing");
const smoothingVal = document.getElementById("smoothingVal");
const decimate = document.getElementById("decimate");
const decimateVal = document.getElementById("decimateVal");
const mergePlanar = document.getElementById("mergePlanar");
const detectShapes = document.getElementById("detectShapes");
const smoothScan = document.getElementById("smoothScan");

const previewPanel = document.getElementById("previewPanel");
const tabBefore = document.getElementById("tabBefore");
const tabAfter = document.getElementById("tabAfter");
const viewerEl = document.getElementById("viewer");

let selectedFile = null;
let selectedFileBuffer = null;
let currentJobId = null;
let eventSource = null;
let pollTimer = null;
let sseSilentSince = null;

// ---------------------------------------------------------------------
// Einstellungen
// ---------------------------------------------------------------------

smoothing.addEventListener("input", () => (smoothingVal.textContent = smoothing.value));
decimate.addEventListener("input", () => (decimateVal.textContent = `${decimate.value}%`));

function currentSettings() {
  return {
    smoothing_iterations: smoothing.value,
    decimate_percent: decimate.value,
    merge_planar: mergePlanar.checked ? "1" : "0",
    detect_curved_shapes: detectShapes.checked ? "1" : "0",
    smooth_scan_surfaces: smoothScan.checked ? "1" : "0",
  };
}

// ---------------------------------------------------------------------
// 3D-Vorschau (Three.js, lokal eingebunden)
// ---------------------------------------------------------------------

let renderer, scene, camera, controls, currentMesh;

function ensureViewer() {
  if (renderer) return;

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  viewerEl.appendChild(renderer.domElement);

  const keyLight = new THREE.DirectionalLight(0xffffff, 1.1);
  keyLight.position.set(1, 1.4, 1.2);
  scene.add(keyLight);
  scene.add(new THREE.AmbientLight(0x8899aa, 0.7));

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  window.addEventListener("resize", resizeViewer);
  resizeViewer();
  animate();
}

function resizeViewer() {
  if (!renderer) return;
  const w = viewerEl.clientWidth || 400;
  const h = viewerEl.clientHeight || 340;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function animate() {
  requestAnimationFrame(animate);
  if (controls) controls.update();
  if (renderer && scene && camera) renderer.render(scene, camera);
}

function loadPreview(url, { fromArrayBuffer, smooth } = {}) {
  ensureViewer();
  const loader = new STLLoader();

  const onGeometry = (geometry) => {
    if (currentMesh) {
      scene.remove(currentMesh);
    }

    geometry.computeVertexNormals();
    geometry.computeBoundingBox();

    const material = new THREE.MeshStandardMaterial({
      color: 0x00c389,
      metalness: 0.15,
      roughness: 0.55,
      // Die "Vorher"-Ansicht (rohes STL) zeigt bewusst flach schattierte
      // Facetten, damit die tatsaechliche Dreiecksstruktur sichtbar
      // bleibt. Die "Nachher"-Ansicht (STEP-Ergebnis) wird dagegen glatt
      // schattiert (interpolierte Normalen) - sonst wuerde selbst eine
      // echte, analytisch glatte Flaeche in der (immer neu triangulierten)
      // 3D-Vorschau faelschlich facettiert aussehen, obwohl die
      // STEP-Datei tatsaechlich eine glatte Flaeche enthaelt.
      flatShading: !smooth,
    });
    const solidMesh = new THREE.Mesh(geometry, material);

    // Kanten der einzelnen Facetten/Flaechen als Overlay einzeichnen,
    // damit man Dreiecksstruktur (STL) bzw. Flaechengrenzen (STEP-
    // Vorschau) erkennen kann. Bei der glatt schattierten Nachher-
    // Ansicht nur noch echte Flaechen-/Kantengrenzen zeigen (grosser
    // Schwellwinkel), nicht das Facettenraster der Neu-Triangulierung.
    const edgeThreshold = smooth ? 25 : 1;
    const edges = new THREE.EdgesGeometry(geometry, edgeThreshold);
    const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x0a2318, transparent: true, opacity: 0.35 });
    const edgeLines = new THREE.LineSegments(edges, edgeMaterial);
    solidMesh.add(edgeLines);

    currentMesh = solidMesh;
    scene.add(currentMesh);

    const box = geometry.boundingBox;
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);
    currentMesh.position.sub(center);

    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    const dist = maxDim * 2.2;
    camera.position.set(dist, dist * 0.7, dist);
    camera.near = maxDim / 100;
    camera.far = maxDim * 50;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0);
    controls.update();
  };

  if (fromArrayBuffer) {
    onGeometry(loader.parse(fromArrayBuffer));
  } else {
    loader.load(url, onGeometry);
  }
}

tabBefore.addEventListener("click", () => {
  if (tabBefore.disabled) return;
  setActiveTab(tabBefore);
  if (selectedFileBuffer) {
    loadPreview(null, { fromArrayBuffer: selectedFileBuffer });
  } else if (currentJobId) {
    loadPreview(`/api/preview/input/${currentJobId}`);
  }
});
tabAfter.addEventListener("click", () => {
  if (tabAfter.disabled) return;
  setActiveTab(tabAfter);
  loadPreview(`/api/preview/output/${currentJobId}`, { smooth: true });
});

function setActiveTab(tab) {
  [tabBefore, tabAfter].forEach((t) => t.classList.toggle("active", t === tab));
}

// ---------------------------------------------------------------------
// Datei-Auswahl
// ---------------------------------------------------------------------

function resetPanels() {
  progressSection.classList.add("hidden");
  resultBox.classList.add("hidden");
  errorBox.classList.add("hidden");
  resultShapesRow.classList.add("hidden");
  progressFill.style.width = "0%";
  progressPercent.textContent = "0%";
  progressMessage.textContent = "Bereit.";
}

function pickFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".stl")) {
    showError("Bitte eine .stl Datei auswählen.");
    return;
  }
  selectedFile = file;
  fileNameEl.textContent = file.name;
  convertBtn.disabled = false;
  resetPanels();

  // Sofort-Vorschau, noch bevor irgendetwas hochgeladen oder
  // umgewandelt wurde: die Datei wird direkt im Browser gelesen und
  // gerendert, ganz ohne Serverkontakt.
  const reader = new FileReader();
  reader.onload = () => {
    selectedFileBuffer = reader.result;
    previewPanel.classList.remove("hidden");
    tabAfter.disabled = true;
    setActiveTab(tabBefore);
    loadPreview(null, { fromArrayBuffer: selectedFileBuffer });
  };
  reader.readAsArrayBuffer(file);
}

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => pickFile(e.target.files[0]));

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function setProgress(pct, message) {
  progressFill.style.width = `${pct}%`;
  progressPercent.textContent = `${pct}%`;
  progressMessage.textContent = message;
}

// ---------------------------------------------------------------------
// Start & robuste Fortschrittsverfolgung
//
// Die Umwandlung laeuft serverseitig in einem eigenen Thread weiter,
// auch wenn dieser Tab im Hintergrund ist oder die Live-Verbindung
// (SSE) kurz abbricht (z. B. durch Energiesparen des Browsers). Statt
// bei einem SSE-Fehler sofort aufzugeben, wird automatisch per
// Status-Abfrage weiterverfolgt.
// ---------------------------------------------------------------------

async function startConversion() {
  if (!selectedFile) return;
  resetPanels();
  progressSection.classList.remove("hidden");
  convertBtn.disabled = true;

  const formData = new FormData();
  formData.append("file", selectedFile);
  Object.entries(currentSettings()).forEach(([k, v]) => formData.append(k, v));

  let jobId;
  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      showError(data.error || "Upload fehlgeschlagen.");
      convertBtn.disabled = false;
      return;
    }
    jobId = data.job_id;
  } catch (err) {
    showError("Verbindung zum lokalen Server fehlgeschlagen.");
    convertBtn.disabled = false;
    return;
  }

  currentJobId = jobId;
  localStorage.setItem(STORAGE_KEY, jobId);

  previewPanel.classList.remove("hidden");
  tabAfter.disabled = true;
  setActiveTab(tabBefore);
  // Die "Vorher"-Ansicht steht durch die Sofort-Vorschau schon; nur
  // falls sie aus irgendeinem Grund fehlt, vom Server nachladen.
  if (!selectedFileBuffer) {
    loadPreview(`/api/preview/input/${jobId}`);
  }

  trackJob(jobId);
}

function trackJob(jobId) {
  stopTracking();
  sseSilentSince = Date.now();

  eventSource = new EventSource(`/api/progress/${jobId}`);
  eventSource.onmessage = (event) => {
    sseSilentSince = Date.now();
    handleJobPayload(jobId, JSON.parse(event.data));
  };
  eventSource.onerror = () => {
    // Nicht sofort aufgeben: Der Browser versucht bei EventSource oft
    // von selbst erneut zu verbinden. Zusaetzlich als Netz eine
    // Status-Abfrage im Hintergrund starten, die auch dann noch den
    // Stand findet, wenn der Stream endgueltig weg ist (z. B. Tab
    // war laenger im Hintergrund/Standby).
    if (!pollTimer) {
      pollTimer = setInterval(() => pollStatus(jobId), 2000);
    }
  };
}

async function pollStatus(jobId) {
  try {
    const res = await fetch(`/api/status/${jobId}`);
    if (res.status === 404) {
      stopTracking();
      showError("Auftrag nicht mehr bekannt (Server evtl. neu gestartet).");
      convertBtn.disabled = false;
      return;
    }
    const data = await res.json();
    handleJobPayload(jobId, data);
  } catch (err) {
    // Server kurz nicht erreichbar (z. B. Rechner aufgewacht) - weiter versuchen.
  }
}

function handleJobPayload(jobId, payload) {
  setProgress(payload.progress ?? 0, payload.message ?? "");

  if (payload.status === "done") {
    stopTracking();
    localStorage.removeItem(STORAGE_KEY);

    resultKind.textContent = payload.is_solid
      ? "Geschlossener Volumenkörper"
      : "Offene Fläche (Netz war nicht wasserdicht)";
    resultFaces.textContent = `${payload.face_count_before} → ${payload.face_count_after}`;

    if (payload.detected_shapes && payload.detected_shapes.length > 0) {
      resultShapesRow.classList.remove("hidden");
      resultShapes.textContent = payload.detected_shapes
        .map((s) => `${s.kind} r≈${s.radius}${s.replaced ? "" : " (nicht ersetzt)"}`)
        .join(", ");
    }

    downloadLink.href = `/api/download/${jobId}`;
    resultBox.classList.remove("hidden");
    convertBtn.disabled = false;

    if (payload.has_preview) {
      tabAfter.disabled = false;
      setActiveTab(tabAfter);
      loadPreview(`/api/preview/output/${jobId}`, { smooth: true });
    }
  } else if (payload.status === "error") {
    stopTracking();
    localStorage.removeItem(STORAGE_KEY);
    showError(payload.message || "Unbekannter Fehler bei der Umwandlung.");
    convertBtn.disabled = false;
  }
}

function stopTracking() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

convertBtn.addEventListener("click", startConversion);

// ---------------------------------------------------------------------
// Wiederaufnahme nach Neuladen der Seite / Rückkehr aus dem Hintergrund
// ---------------------------------------------------------------------

window.addEventListener("DOMContentLoaded", async () => {
  const savedJobId = localStorage.getItem(STORAGE_KEY);
  if (!savedJobId) return;

  try {
    const res = await fetch(`/api/status/${savedJobId}`);
    if (!res.ok) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    const data = await res.json();
    if (data.status === "running") {
      currentJobId = savedJobId;
      convertBtn.disabled = true;
      progressSection.classList.remove("hidden");
      previewPanel.classList.remove("hidden");
      tabAfter.disabled = true;
      loadPreview(`/api/preview/input/${savedJobId}`);
      trackJob(savedJobId);
    } else if (data.status === "done") {
      currentJobId = savedJobId;
      previewPanel.classList.remove("hidden");
      handleJobPayload(savedJobId, data);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (err) {
    // Server (noch) nicht erreichbar - beim naechsten Laden erneut versuchen.
  }
});
