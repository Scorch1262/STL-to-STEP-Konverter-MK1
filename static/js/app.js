(() => {
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
  const downloadLink = document.getElementById("downloadLink");

  const errorBox = document.getElementById("errorBox");

  let selectedFile = null;

  function resetPanels() {
    progressSection.classList.add("hidden");
    resultBox.classList.add("hidden");
    errorBox.classList.add("hidden");
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
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    pickFile(file);
  });

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  }

  function setProgress(pct, message) {
    progressFill.style.width = `${pct}%`;
    progressPercent.textContent = `${pct}%`;
    progressMessage.textContent = message;
  }

  async function startConversion() {
    if (!selectedFile) return;
    resetPanels();
    progressSection.classList.remove("hidden");
    convertBtn.disabled = true;

    const formData = new FormData();
    formData.append("file", selectedFile);

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

    const source = new EventSource(`/api/progress/${jobId}`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      setProgress(payload.progress ?? 0, payload.message ?? "");

      if (payload.status === "done") {
        source.close();
        resultKind.textContent = payload.is_solid
          ? "Geschlossener Volumenkörper"
          : "Offene Fläche (Netz war nicht wasserdicht)";
        resultFaces.textContent = `${payload.face_count_before} → ${payload.face_count_after}`;
        downloadLink.href = `/api/download/${jobId}`;
        resultBox.classList.remove("hidden");
        convertBtn.disabled = false;
      } else if (payload.status === "error") {
        source.close();
        showError(payload.message || "Unbekannter Fehler bei der Umwandlung.");
        convertBtn.disabled = false;
      }
    };
    source.onerror = () => {
      source.close();
      showError("Verbindung zum Fortschritts-Stream unterbrochen.");
      convertBtn.disabled = false;
    };
  }

  convertBtn.addEventListener("click", startConversion);
})();
