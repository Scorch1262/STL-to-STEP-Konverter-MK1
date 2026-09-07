# STL-STEP-Konverter-MK1

Lokale Webanwendung, die eine `.stl`-Datei per Flächenrückführung in
eine `.stp`-Datei (STEP) umwandelt. Das komplette Dreiecksnetz wird
dabei zu einem einzigen Volumenkörper zusammengefasst.

## Funktionsweise (kurz)

1. STL-Netz einlesen.
2. Alle Dreiecksflächen zu einer Hülle vernähen (Sewing).
3. Ist die Hülle geschlossen: einen Volumenkörper daraus bauen
   (ein Körper, kein loses Flächenhaufen).
4. Benachbarte, in derselben Ebene liegende Dreiecke zu jeweils
   einer großen, echten Fläche zusammenfassen.
5. Ergebnis als STEP (AP214) schreiben.

**Wichtige Einschränkung:** Anders als eine vollständige
Flächenrückführung à la Geomagic Wrap/Design X (automatische
Segmentierung + Anpassung von NURBS-Flächen auch an gekrümmte /
freiformige Bereiche) werden hier nur **ebene** Bereiche zu echten
Flächen zusammengeführt. Gekrümmte Bereiche (Rundungen, organische
Formen) bleiben als Dreiecksfacetten erhalten – sind aber weiterhin
Teil des einen Volumenkörpers und liegen als gültige STEP-Flächen vor.
Eine echte automatische NURBS-Anpassung für Freiformflächen wäre ein
eigenständiges, deutlich aufwändigeres Ausbaustadium (siehe
`CHANGELOG.md`, Abschnitt „Ideen für später“).

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
