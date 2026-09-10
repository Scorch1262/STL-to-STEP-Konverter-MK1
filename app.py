"""
STL -> STEP Konverter MK1
=========================

Startet einen lokalen Webserver mit einer Oberflaeche zum Hochladen
einer .stl Datei. Die Datei wird per Flaechenrueckfuehrung (siehe
converter.py) in eine .stp Datei umgewandelt, die anschliessend ueber
die Webseite wieder heruntergeladen werden kann. Fortschritt und eine
3D-Vorschau (vorher/nachher) werden live angezeigt.

Die eigentliche Umwandlung laeuft in einem Hintergrund-Thread und ist
komplett unabhaengig vom Browser-Tab: Auch wenn die Weboberflaeche
gerade nicht im Vordergrund ist (oder die Verbindung kurz aussetzt),
laeuft die Konvertierung serverseitig weiter. Das Frontend erkennt
das automatisch wieder (Wiederverbindung + Status-Abfrage), sobald es
wieder aktiv ist.

Wird als exe (Windows) bzw. .app (macOS) ueber PyInstaller gebaut und
startet dabei bewusst sichtbar in einem Terminal-/Konsolenfenster,
damit man das Programm einfach durch Schliessen des Fensters (oder
STRG+C) wieder beenden kann.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import uuid
import webbrowser

from flask import Flask, Response, jsonify, render_template, request, send_file

from converter import ConversionError, ConversionSettings, convert_stl_to_step
from version import __version__

APP_NAME = "STL-STEP-Konverter-MK1"
HEARTBEAT_SECONDS = 8


def resource_path(relative_path: str) -> str:
    """Pfad zu Ressourcen (templates/static), funktioniert im Skript wie
    auch im per PyInstaller gebauten Programm (sys._MEIPASS)."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)

WORK_DIR = os.path.join(tempfile.gettempdir(), "stl-step-konverter")
os.makedirs(WORK_DIR, exist_ok=True)

# job_id -> Status-Dict. Reicht fuer den lokalen Ein-Nutzer-Betrieb aus.
# Der Hintergrund-Thread laeuft unabhaengig vom Browser weiter und
# schreibt seinen Fortschritt hier hinein - egal ob gerade jemand
# zusieht oder nicht.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **kwargs) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def _job_public_state(job: dict) -> dict:
    payload = {
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
    }
    if job["status"] == "done":
        payload.update(
            {
                "is_solid": job.get("is_solid"),
                "face_count_before": job.get("face_count_before"),
                "face_count_after": job.get("face_count_after"),
                "volume": job.get("volume"),
                "detected_shapes": job.get("detected_shapes", []),
                "has_preview": bool(job.get("preview_path") and os.path.exists(job.get("preview_path", ""))),
            }
        )
    return payload


def _run_conversion(job_id: str, input_path: str, output_path: str, preview_path: str, settings: ConversionSettings) -> None:
    def progress_cb(pct: int, message: str) -> None:
        _set_job(job_id, progress=pct, message=message)

    try:
        result = convert_stl_to_step(
            input_path, output_path, preview_path=preview_path, settings=settings, progress_cb=progress_cb
        )
        _set_job(
            job_id,
            status="done",
            progress=100,
            message="Umwandlung abgeschlossen.",
            is_solid=result.is_solid,
            face_count_before=result.face_count_before,
            face_count_after=result.face_count_after,
            volume=result.volume,
            preview_path=result.preview_path,
            detected_shapes=[
                {
                    "kind": s.kind,
                    "radius": round(s.radius, 3),
                    "inlier_ratio": round(s.inlier_ratio, 2),
                    "replaced": s.replaced,
                }
                for s in result.detected_shapes
            ],
        )
    except ConversionError as exc:
        _set_job(job_id, status="error", message=str(exc))
    except Exception as exc:  # Sicherheitsnetz, damit der Thread nie "stumm" stirbt
        _set_job(job_id, status="error", message=f"Unerwarteter Fehler: {exc}")
    # Eingabedatei bewusst NICHT sofort loeschen: die "Vorher"-Vorschau
    # auf der Webseite liest sie ggf. noch, solange der Job im Speicher ist.


@app.route("/")
def index():
    return render_template("index.html", version=__version__, app_name=APP_NAME)


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei uebermittelt."}), 400

    file = request.files["file"]
    if not file.filename.lower().endswith(".stl"):
        return jsonify({"error": "Bitte eine .stl Datei auswaehlen."}), 400

    settings = ConversionSettings.from_form(request.form)

    job_id = uuid.uuid4().hex
    input_path = os.path.join(WORK_DIR, f"{job_id}_input.stl")
    output_path = os.path.join(WORK_DIR, f"{job_id}_output.stp")
    preview_path = os.path.join(WORK_DIR, f"{job_id}_preview.stl")
    file.save(input_path)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "message": "Warteschlange ...",
            "output_path": output_path,
            "input_path": input_path,
            "preview_path": preview_path,
            "original_name": os.path.splitext(file.filename)[0],
        }

    thread = threading.Thread(
        target=_run_conversion,
        args=(job_id, input_path, output_path, preview_path, settings),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id: str):
    """Einfache (nicht-streamende) Statusabfrage.

    Dient dem Frontend als Rueckfall, falls der Live-Fortschritts-
    Stream (SSE) z. B. durch Tab-Wechsel/Standby unterbrochen wurde,
    und zur Wiederaufnahme nach einem Neuladen der Seite.
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return jsonify({"status": "unknown"}), 404
    return jsonify(_job_public_state(job))


@app.route("/api/progress/<job_id>")
def progress(job_id: str):
    def stream():
        last_sent = None
        last_sent_at = 0.0
        while True:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Unbekannter Auftrag.'})}\n\n"
                return

            payload = _job_public_state(job)
            now = time.time()

            if payload != last_sent:
                yield f"data: {json.dumps(payload)}\n\n"
                last_sent = payload
                last_sent_at = now
            elif now - last_sent_at > HEARTBEAT_SECONDS:
                # Haelt die Verbindung am Leben, damit Browser/Betriebssystem
                # sie nicht wegen vermeintlicher Inaktivitaet schliessen
                # (z. B. wenn der Tab im Hintergrund laeuft).
                yield ": heartbeat\n\n"
                last_sent_at = now

            if job["status"] in ("done", "error"):
                return
            time.sleep(0.3)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/api/download/<job_id>")
def download(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)

    if job is None or job["status"] != "done":
        return jsonify({"error": "Datei ist noch nicht bereit."}), 404

    download_name = f"{job.get('original_name', 'modell')}.stp"
    return send_file(job["output_path"], as_attachment=True, download_name=download_name)


@app.route("/api/preview/input/<job_id>")
def preview_input(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None or not os.path.exists(job.get("input_path", "")):
        return jsonify({"error": "Eingabedatei nicht (mehr) verfuegbar."}), 404
    return send_file(job["input_path"], mimetype="model/stl")


@app.route("/api/preview/output/<job_id>")
def preview_output(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    preview_path = job.get("preview_path") if job else None
    if job is None or job["status"] != "done" or not preview_path or not os.path.exists(preview_path):
        return jsonify({"error": "Vorschau nicht verfuegbar."}), 404
    return send_file(preview_path, mimetype="model/stl")


def _open_browser(url: str) -> None:
    try:
        time.sleep(1.0)
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "5158"))
    open_browser = os.environ.get("DASHBOARD_OPEN_BROWSER", "1") != "0"

    print(f"{APP_NAME} v{__version__}")
    print(f"Weboberflaeche: http://{host}:{port}")
    print("Zum Beenden dieses Fenster schliessen oder STRG+C druecken.")

    if open_browser:
        threading.Thread(target=_open_browser, args=(f"http://{host}:{port}",), daemon=True).start()

    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
