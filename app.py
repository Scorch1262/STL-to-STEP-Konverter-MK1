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

import multiprocessing
import os
import sys
import traceback

APP_NAME = "STL-STEP-Konverter-MK1"
HEARTBEAT_SECONDS = 8


def _fatal_startup_error(context: str) -> None:
    """Zeigt einen aufgetretenen Fehler vollstaendig an und haelt das
    Fenster anschliessend offen.

    Hintergrund: Unter Windows schliesst sich das von PyInstaller
    geoeffnete Konsolenfenster automatisch und SOFORT, sobald sich das
    Programm beendet - auch bei einem Absturz durch eine unbehandelte
    Ausnahme. Ohne diese Absicherung sieht man als Nutzer also nur ein
    kurz aufblitzendes und wieder verschwindendes schwarzes Fenster,
    aber nie die eigentliche Fehlermeldung. Diese Funktion faengt
    genau solche Startfehler ab (z. B. eine fehlende DLL fuer die
    OpenCASCADE-Anbindung 'OCP', typischerweise weil auf dem Windows-
    Rechner das "Microsoft Visual C++ Redistributable" fehlt), zeigt
    den vollstaendigen Fehler an und wartet auf eine Eingabe, bevor
    sich das Fenster schliesst."""
    print("\n" + "=" * 70)
    print(f"FEHLER: {context}")
    print("=" * 70)
    traceback.print_exc()
    print("=" * 70)
    print(
        "\nHinweis: Falls hier ein Fehler rund um 'OCP', 'DLL load failed'\n"
        "oder eine aehnliche Bibliothek steht, fehlt unter Windows meist\n"
        "das kostenlose 'Microsoft Visual C++ Redistributable (x64)'.\n"
        "Dieses laesst sich direkt bei Microsoft herunterladen und\n"
        "installieren, danach das Programm erneut starten."
    )
    try:
        input("\nDruecken Sie die Eingabetaste, um dieses Fenster zu schliessen ...")
    except Exception:
        pass


try:
    import json
    import queue as queue_module
    import tempfile
    import threading
    import time
    import uuid
    import webbrowser

    from flask import Flask, Response, jsonify, render_template, request, send_file

    from converter import ConversionError, ConversionSettings, convert_stl_to_step, simplify_stl_to_stl
    from version import __version__
except BaseException:
    # Passiert dieser Fehler, ist noch gar kein Programmteil (Server,
    # multiprocessing, ...) aktiv - daher hier sofort abfangen und
    # anzeigen, statt das Fenster kommentarlos verschwinden zu lassen.
    _fatal_startup_error(
        "Eine benoetigte Programmbibliothek konnte nicht geladen werden. "
        "Das Programm konnte deshalb nicht gestartet werden."
    )
    sys.exit(1)


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
        "mode": job.get("mode", "step"),
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


def _conversion_worker_process(
    input_path: str,
    output_path: str,
    preview_path: str,
    settings: ConversionSettings,
    progress_queue: "multiprocessing.Queue",
) -> None:
    """Laeuft in einem EIGENEN Prozess (nicht nur einem Thread).

    Grund: Der Flaechen-/Volumenkoerper-Aufbau kann bei grossen Netzen
    mehrere GB Arbeitsspeicher belegen. In einem gewoehnlichen Thread
    wuerde dieser Speicher Teil des langlebigen Server-Prozesses bleiben
    (und bei wiederholten Versuchen mit grossen Dateien immer weiter
    anwachsen, ohne zuverlaessig wieder freigegeben zu werden) - UND ein
    tatsaechliches Out-of-Memory wuerde vom Betriebssystem den GESAMTEN
    Serverprozess beenden, nicht nur die eine Umwandlung. Als eigener
    Prozess wird der komplette Speicher beim Beenden garantiert an das
    Betriebssystem zurueckgegeben, und ein harter Absturz (z. B. durch
    OOM) betrifft nur diesen einen Auftrag - der Webserver selbst laeuft
    unbeeintraechtigt weiter.
    """

    def progress_cb(pct: int, message: str) -> None:
        try:
            progress_queue.put(("progress", pct, message))
        except Exception:
            pass

    try:
        result = convert_stl_to_step(
            input_path, output_path, preview_path=preview_path, settings=settings, progress_cb=progress_cb
        )
        progress_queue.put((
            "done",
            {
                "is_solid": result.is_solid,
                "face_count_before": result.face_count_before,
                "face_count_after": result.face_count_after,
                "volume": result.volume,
                "preview_path": result.preview_path,
                "detected_shapes": [
                    {
                        "kind": s.kind,
                        "radius": round(s.radius, 3),
                        "inlier_ratio": round(s.inlier_ratio, 2),
                        "replaced": s.replaced,
                    }
                    for s in result.detected_shapes
                ],
            },
        ))
    except ConversionError as exc:
        progress_queue.put(("error", str(exc)))
    except Exception as exc:  # Sicherheitsnetz, damit der Prozess nie "stumm" stirbt
        progress_queue.put(("error", f"Unerwarteter Fehler: {exc}"))


def _simplify_worker_process(
    input_path: str,
    output_path: str,
    preview_path: str,
    settings: ConversionSettings,
    progress_queue: "multiprocessing.Queue",
) -> None:
    """Wie _conversion_worker_process, aber fuer den leichtgewichtigen
    Modus "nur Dreiecke reduzieren": ruft simplify_stl_to_stl statt
    convert_stl_to_step auf, kommt also komplett ohne OpenCASCADE aus.
    Laeuft trotzdem in einem eigenen Prozess (statt nur einem Thread),
    damit auch sehr grosse Netze den Hauptprozess (Webserver) niemals
    belasten oder mit ihm um Arbeitsspeicher konkurrieren.

    'preview_path' wird hier nicht separat erzeugt: die reduzierte STL-
    Datei IST bereits die Vorschau fuer die "Nachher"-Ansicht, output_path
    und preview_path zeigen deshalb bewusst auf dieselbe Datei (siehe
    upload()).
    """

    def progress_cb(pct: int, message: str) -> None:
        try:
            progress_queue.put(("progress", pct, message))
        except Exception:
            pass

    try:
        result = simplify_stl_to_stl(input_path, output_path, settings=settings, progress_cb=progress_cb)
        progress_queue.put((
            "done",
            {
                "is_solid": None,
                "face_count_before": result.face_count_before,
                "face_count_after": result.face_count_after,
                "volume": None,
                "preview_path": output_path,
                "detected_shapes": [],
            },
        ))
    except ConversionError as exc:
        progress_queue.put(("error", str(exc)))
    except Exception as exc:  # Sicherheitsnetz, damit der Prozess nie "stumm" stirbt
        progress_queue.put(("error", f"Unerwarteter Fehler: {exc}"))


def _run_conversion(
    job_id: str,
    input_path: str,
    output_path: str,
    preview_path: str,
    settings: ConversionSettings,
    mode: str = "step",
) -> None:
    """Leichter Aufseher-Thread im Hauptprozess: startet die eigentliche
    Umwandlung als eigenen Prozess und reicht dessen Fortschritts-
    meldungen an JOBS weiter. Haelt selbst keine grossen Datenmengen im
    Speicher. Je nach 'mode' wird entweder die volle STEP-Umwandlung
    oder die leichtgewichtige reine Netz-Vereinfachung gestartet."""
    progress_queue: "multiprocessing.Queue" = multiprocessing.Queue()
    worker = _simplify_worker_process if mode == "simplify" else _conversion_worker_process
    process = multiprocessing.Process(
        target=worker,
        args=(input_path, output_path, preview_path, settings, progress_queue),
    )
    process.start()

    finished = False
    try:
        while True:
            try:
                item = progress_queue.get(timeout=1.0)
            except queue_module.Empty:
                if not process.is_alive():
                    break
                continue

            kind = item[0]
            if kind == "progress":
                _, pct, message = item
                _set_job(job_id, progress=pct, message=message)
            elif kind == "done":
                _, data = item
                _set_job(
                    job_id,
                    status="done",
                    progress=100,
                    message="Umwandlung abgeschlossen.",
                    **data,
                )
                finished = True
                break
            elif kind == "error":
                _, message = item
                _set_job(job_id, status="error", message=message)
                finished = True
                break
    finally:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    if not finished:
        # Der Prozess wurde beendet, ohne eine abschliessende Meldung zu
        # schicken - typischerweise ein harter Abbruch durch das
        # Betriebssystem (z. B. nicht genug Arbeitsspeicher trotz der
        # vorherigen Schaetzung). Der Webserver selbst laeuft weiter;
        # nur dieser eine Auftrag wird als fehlgeschlagen markiert.
        _set_job(
            job_id,
            status="error",
            message=(
                "Die Umwandlung wurde unerwartet beendet (vermutlich nicht genug "
                "Arbeitsspeicher). Bitte die Einstellung \"Vereinfachung\" "
                "(Dezimierung) staerker nutzen und erneut versuchen."
            ),
        )
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

    mode = request.form.get("mode", "step")
    if mode not in ("step", "simplify"):
        mode = "step"

    job_id = uuid.uuid4().hex
    input_path = os.path.join(WORK_DIR, f"{job_id}_input.stl")
    if mode == "simplify":
        # Reiner Vereinfachungs-Modus: das Ergebnis ist selbst wieder
        # eine STL-Datei, die zugleich als "Nachher"-Vorschau dient -
        # eine separate Vorschau-Datei wird hier nicht benoetigt.
        output_path = os.path.join(WORK_DIR, f"{job_id}_output.stl")
        preview_path = output_path
    else:
        output_path = os.path.join(WORK_DIR, f"{job_id}_output.stp")
        preview_path = os.path.join(WORK_DIR, f"{job_id}_preview.stl")
    file.save(input_path)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "message": "Warteschlange ...",
            "mode": mode,
            "output_path": output_path,
            "input_path": input_path,
            "preview_path": preview_path,
            "original_name": os.path.splitext(file.filename)[0],
        }

    thread = threading.Thread(
        target=_run_conversion,
        args=(job_id, input_path, output_path, preview_path, settings, mode),
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

    original_name = job.get("original_name", "modell")
    if job.get("mode") == "simplify":
        download_name = f"{original_name}_reduziert.stl"
    else:
        download_name = f"{original_name}.stp"
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
    # Noetig, damit multiprocessing.Process in einer per PyInstaller
    # gebauten .exe unter Windows nicht in eine Endlosschleife laeuft
    # (jeder neue Prozess wuerde sonst das ganze Programm erneut von
    # vorne starten). Muss die allererste Anweisung im Einstiegspunkt
    # sein.
    multiprocessing.freeze_support()
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        # Sicherheitsnetz fuer alle Startfehler NACH dem Bibliotheks-
        # Import (z. B. Port bereits belegt) - siehe _fatal_startup_error
        # oben fuer die Begruendung, warum dieses Abfangen unter Windows
        # notwendig ist.
        _fatal_startup_error("Das Programm wurde unerwartet beendet.")
        sys.exit(1)
