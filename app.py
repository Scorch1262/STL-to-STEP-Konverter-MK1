"""
STL -> STEP Konverter MK1
=========================

Startet einen lokalen Webserver mit einer Oberflaeche zum Hochladen
einer .stl Datei. Die Datei wird per Flaechenrueckfuehrung (siehe
converter.py) in eine .stp Datei umgewandelt, die anschliessend ueber
die Webseite wieder heruntergeladen werden kann. Der Fortschritt wird
live als Balken angezeigt.

Wird als exe (Windows) bzw. .app (macOS) ueber PyInstaller gebaut und
startet dabei bewusst sichtbar in einem Terminal-/Konsolenfenster,
damit man das Programm einfach durch Schliessen des Fensters (oder
STRG+C) wieder beenden kann.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
import uuid
import webbrowser

from flask import Flask, Response, jsonify, render_template, request, send_file

from converter import ConversionError, convert_stl_to_step
from version import __version__

APP_NAME = "STL-STEP-Konverter-MK1"


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
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **kwargs) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(kwargs)


def _run_conversion(job_id: str, input_path: str, output_path: str) -> None:
    def progress_cb(pct: int, message: str) -> None:
        _set_job(job_id, progress=pct, message=message)

    try:
        result = convert_stl_to_step(input_path, output_path, progress_cb)
        _set_job(
            job_id,
            status="done",
            progress=100,
            message="Umwandlung abgeschlossen.",
            is_solid=result.is_solid,
            face_count_before=result.face_count_before,
            face_count_after=result.face_count_after,
            volume=result.volume,
        )
    except ConversionError as exc:
        _set_job(job_id, status="error", message=str(exc))
    except Exception as exc:  # Sicherheitsnetz, damit der Thread nie "stumm" stirbt
        _set_job(job_id, status="error", message=f"Unerwarteter Fehler: {exc}")
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass


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

    job_id = uuid.uuid4().hex
    input_path = os.path.join(WORK_DIR, f"{job_id}_input.stl")
    output_path = os.path.join(WORK_DIR, f"{job_id}_output.stp")
    file.save(input_path)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "message": "Warteschlange ...",
            "output_path": output_path,
            "original_name": os.path.splitext(file.filename)[0],
        }

    thread = threading.Thread(
        target=_run_conversion, args=(job_id, input_path, output_path), daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def progress(job_id: str):
    def stream():
        last_sent = None
        while True:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Unbekannter Auftrag.'})}\n\n"
                return

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
                    }
                )

            if payload != last_sent:
                yield f"data: {json.dumps(payload)}\n\n"
                last_sent = payload

            if job["status"] in ("done", "error"):
                return
            time.sleep(0.3)

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/download/<job_id>")
def download(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)

    if job is None or job["status"] != "done":
        return jsonify({"error": "Datei ist noch nicht bereit."}), 404

    download_name = f"{job.get('original_name', 'modell')}.stp"
    return send_file(job["output_path"], as_attachment=True, download_name=download_name)


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
