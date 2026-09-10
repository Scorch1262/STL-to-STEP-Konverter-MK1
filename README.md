# STL-STEP-Konverter-MK1

Lokale Webanwendung, die eine `.stl`-Datei per Flächenrückführung in
eine `.stp`-Datei (STEP) umwandelt. Das komplette Dreiecksnetz wird
dabei zu einem einzigen Volumenkörper zusammengefasst.

## Funktionsweise (kurz)

1. STL-Netz einlesen (optional vorher glätten/vereinfachen, siehe Einstellungen).
2. Alle Dreiecksflächen zu einer Hülle vernähen (Sewing).
3. Ist die Hülle geschlossen: einen Volumenkörper daraus bauen
   (ein Körper, kein loses Flächenhaufen).
4. **Volle Zylinder (Bohrungen, Wellen), Kugeln und Wölbungen werden
   automatisch erkannt und durch echte, analytisch exakte STEP-Flächen
   ersetzt** - nicht mehr nur triangulierte Facetten. Eine Platte mit
   3 Bohrungen wird so z. B. von 600 Facetten auf 9 echte Flächen
   reduziert, bei exakt erhaltenem Volumen.
5. Verbleibende benachbarte, in derselben Ebene liegende Dreiecke
   werden zu jeweils einer großen, echten Fläche zusammengefasst.
6. Ergebnis als STEP (AP214) schreiben, zusätzlich eine 3D-Vorschau
   (Vorher/Nachher inkl. Kanten-Overlay) direkt auf der Weboberfläche.

**Wichtige Einschränkung:** Ersetzt werden nur **vollständige**
Zylinder-/Kugelflächen (volle 360° um Achse bzw. Pol) - das deckt die
häufigsten Fälle ab (Bohrungen, Wellen/Bolzen, Kuppeln, volle Kugeln).
Teilausschnitte, Verrundungen mit wechselndem Radius (variable
Fillets) und echte Freiformflächen bleiben als Facetten erhalten -
eine vollständige automatische Flächenrückführung wie in Geomagic
Wrap/Design X (inkl. Segmentierung beliebiger Freiformflächen in
NURBS-Patches) ist damit nicht erreicht. Schlägt der Ersetzungsversuch
am Ende der Verarbeitung dennoch fehl (z. B. weil der resultierende
Volumenkörper ungültig wäre), wird automatisch und vollständig auf die
reine Facetten-Lösung zurückgefallen - es wird nie eine kaputte
STEP-Datei ausgeliefert.

## Einstellungen auf der Weboberfläche

- **Glättung** (0–10): Taubin-Glättung vor der Umwandlung, entfernt
  Netzrauschen ohne das Modell sichtbar zu schrumpfen.
- **Vereinfachung**: Ziel-Dreieckszahl in % der ursprünglichen Anzahl.
- **Ebene Flächen zusammenführen**: an/aus.
- **Zylinder/Kugeln automatisch ersetzen**: an/aus.

## Performance bei sehr großen Netzen - ehrlicher Stand

Die Erkennung passender Zylinder/Kugeln läuft vektorisiert
(numpy/scipy) und die eigentliche Kandidatensuche parallel über
mehrere Prozessorkerne (ein Kandidat pro Kern). Das bringt bei
mittelgroßen Netzen (zehntausende Dreiecke) einen spürbaren
Geschwindigkeitsgewinn.

Der eigentliche Flaschenhals bei **sehr** großen Netzen (Hunderttausende
bis Millionen Dreiecke, erst recht im zweistelligen Millionenbereich)
liegt aber in OpenCASCADEs eigenen Kernroutinen zum Vernähen und zur
Volumenkörper-Gültigkeitsprüfung - diese sind über die verfügbaren
Python-Bindings nicht parallelisierbar und skalieren spürbar
schlechter als linear (in eigenen Tests: 4x mehr Dreiecke → ca. 7x
mehr Zeit). Ein Netz mit z. B. 100 Millionen Dreiecken (mehrere GB
allein als Datei) ist mit dieser Architektur nicht in praktikabler
Zeit verarbeitbar - das ist eine Grenze von OpenCASCADE selbst, keine
reine Python-Performance-Frage.

**Praktische Empfehlung für sehr große Dateien:** die Einstellung
„Vereinfachung“ (Dezimierung) *zuerst* nutzen, um die Dreieckszahl auf
ein handhabbares Maß zu reduzieren - dieser Schritt läuft in einer
schnellen, für große Netze ausgelegten Bibliothek (`fast-simplification`)
und passiert *vor* den teuren OpenCASCADE-Schritten. Ab automatisch
300.000 Dreiecken wird die Zylinder-/Kugel-Erkennung übersprungen
(reine Flächenrückführung läuft trotzdem weiter), um die Verarbeitung
nicht unnötig auszubremsen.

## Robustheit bei Hintergrund-Tabs / Verbindungsaussetzern

Die Umwandlung läuft in einem eigenen Server-Thread und damit
unabhängig davon, ob der Browser-Tab gerade sichtbar ist. Der
Live-Fortschritt (SSE) wird per Heartbeat abgesichert; bricht die
Verbindung trotzdem ab, übernimmt automatisch eine Status-Abfrage im
Hintergrund. Die Job-ID wird im Browser gespeichert, sodass ein
Neuladen der Seite den laufenden bzw. fertigen Auftrag wiederfindet.

## 3D-Vorschau

Die STL-Datei wird sofort nach Auswahl direkt im Browser angezeigt -
noch bevor irgendetwas hochgeladen wurde. Nach der Umwandlung zeigt
der zweite Tab das neu triangulierte STEP-Ergebnis. In beiden
Ansichten werden die Kanten der einzelnen Facetten (STL) bzw.
Flächengrenzen (STEP-Ergebnis) als Overlay eingezeichnet.


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
