# STL-STEP-Konverter-MK1

Lokale Webanwendung, die eine `.stl`-Datei per Flächenrückführung in
eine `.stp`-Datei (STEP) umwandelt. Das komplette Dreiecksnetz wird
dabei zu einem einzigen Volumenkörper zusammengefasst.

## Funktionsweise (kurz)

1. STL-Netz einlesen (optional vorher glätten/vereinfachen, siehe Einstellungen).
2. Alle Dreiecksflächen zu einer Hülle vernähen (Sewing).
3. Ist die Hülle geschlossen: einen Volumenkörper daraus bauen
   (ein Körper, kein loses Flächenhaufen).
4. Benachbarte, in derselben Ebene liegende Dreiecke zu jeweils
   einer großen, echten Fläche zusammenfassen.
5. Gekrümmte Bereiche (Zylinder/Kugeln) werden erkannt und mit
   Radius + Trefferquote angezeigt (rein informativ).
6. Ergebnis als STEP (AP214) schreiben, zusätzlich eine 3D-Vorschau
   (Vorher/Nachher) direkt auf der Weboberfläche.

**Wichtige Einschränkung:** Anders als eine vollständige
Flächenrückführung à la Geomagic Wrap/Design X (automatische
Segmentierung + Anpassung von NURBS-Flächen auch an gekrümmte /
freiformige Bereiche) werden hier nur **ebene** Bereiche zu echten
Flächen zusammengeführt. Der automatische Ersatz erkannter
Zylinder/Kugeln durch echte gekrümmte STEP-Flächen wurde versucht und
wieder verworfen, weil der Naht-/Solid-Aufbau an den Übergangskanten
in Tests ungültige Geometrie erzeugte - siehe `CHANGELOG.md`. Gekrümmte
Bereiche bleiben deshalb als Dreiecksfacetten erhalten, sind aber
weiterhin Teil des einen Volumenkörpers und liegen als gültige
STEP-Flächen vor.

## Einstellungen auf der Weboberfläche

- **Glättung** (0–10): Taubin-Glättung vor der Umwandlung, entfernt
  Netzrauschen ohne das Modell sichtbar zu schrumpfen.
- **Vereinfachung**: Ziel-Dreieckszahl in % der ursprünglichen Anzahl.
- **Ebene Flächen zusammenführen**: an/aus.
- **Zylinder/Kugeln erkennen**: an/aus (nur Anzeige, ändert die
  Geometrie nicht).

## Robustheit bei Hintergrund-Tabs / Verbindungsaussetzern

Die Umwandlung läuft in einem eigenen Server-Thread und damit
unabhängig davon, ob der Browser-Tab gerade sichtbar ist. Der
Live-Fortschritt (SSE) wird per Heartbeat abgesichert; bricht die
Verbindung trotzdem ab, übernimmt automatisch eine Status-Abfrage im
Hintergrund. Die Job-ID wird im Browser gespeichert, sodass ein
Neuladen der Seite den laufenden bzw. fertigen Auftrag wiederfindet.


## Starten (aus dem Quellcode)

```bash
pip install -r requirements.txt
python app.py
```

Die Weboberfläche öffnet sich automatisch unter `http://127.0.0.1:5158`.

## Als exe / App bauen

Passiert automatisch per GitHub Actions bei jedem Push nach `main`
(Artefakte) bzw. bei jedem Tag `vX.Y.Z` (zusätzlich als GitHub
Release). Manuell auslösbar über den Tab „Actions“ → „Build“ →
„Run workflow“.

## Anwendung beenden

- **Windows:** Beim Start öffnet sich automatisch ein Konsolenfenster.
  Einfach schließen oder STRG+C drücken.
- **macOS:** Beim Start öffnet sich automatisch ein **Terminal-Fenster**,
  in dem das Programm läuft (ein kleiner Wrapper startet die eigentliche
  Anwendung sichtbar in Terminal.app). Zum Beenden im Terminal-Fenster
  STRG+C drücken oder das Fenster schließen.

## Versionsnummer

Steht in `version.py` (`__version__`) und wird oben rechts auf der
Weboberfläche angezeigt. Bei jeder funktionalen Änderung hochzählen
und in `CHANGELOG.md` eintragen.

## Repo-Struktur

Der Inhalt dieses Ordners kann 1:1 in ein leeres GitHub-Repository
kopiert werden (alle Dateien liegen bereits auf Root-Ebene).
