# Changelog

## 1.2.0
- **Automatischer Flächenersatz:** Volle Zylinder (Bohrungen, Wellen),
  Kugeln und Wölbungen werden jetzt automatisch durch echte,
  analytisch exakte STEP-Flächen ersetzt (nicht mehr nur erkannt und
  angezeigt wie in 1.1.0). Beispiel: eine Platte mit 3 Bohrungen
  unterschiedlichen Radius' wird von 600 Facetten auf 9 echte Flächen
  reduziert, bei exakt erhaltenem Volumen. Teilausschnitte,
  Verrundungen mit wechselndem Radius und Freiformflächen bleiben wie
  gehabt als Facetten erhalten.
- Absicherung: Gelingt der Ersetzungsversuch am Ende nicht (Volumen-
  körper wäre ungültig), wird automatisch und vollständig auf die
  reine Facetten-Lösung zurückgefallen - es wird nie eine kaputte
  STEP-Datei ausgeliefert.
- Mehrkern-Nutzung: Die Suche nach passenden Zylindern/Kugeln läuft
  jetzt über mehrere Prozesse parallel (ein Kandidat pro Kern).
- Grundlegende Performance-Überarbeitung der Erkennung: Regionen-
  wachstum und Punktauswertung laufen jetzt vektorisiert über
  numpy/scipy statt einzeln pro Facette über OpenCASCADE-Aufrufe -
  bei 20.000 Dreiecken ca. 2x schneller, vor allem aber grundsätzlich
  besser skalierbar. Siehe README für die ehrlichen Grenzen bei sehr
  großen Netzen (mehrere Millionen+ Dreiecke).
- Zwei Fehlerquellen behoben, die zuvor Bohrungen/Rundungen verpassen
  konnten: fehlerhafte (wicklungsabhängige) Normalenberechnung und
  eine zu früh greifende Vollständigkeitsprüfung.
- 3D-Vorschau: Kanten der Facetten (STL) bzw. Flächengrenzen
  (STEP-Ergebnis) werden jetzt als Overlay eingezeichnet.
- Die STL-Vorschau erscheint jetzt sofort nach Dateiauswahl, direkt im
  Browser gerendert - noch bevor irgendetwas hochgeladen wurde.

## 1.1.0
- Fortschritts-Stream mit Heartbeat abgesichert: Bricht nicht mehr ab,
  wenn der Browser-Tab länger im Hintergrund ist oder die Verbindung
  kurz aussetzt. Neuer Status-Endpunkt (`/api/status/<id>`) dient als
  Rückfall-Abfrage und zur Wiederaufnahme nach Neuladen der Seite
  (Job-ID wird im Browser gespeichert).
- Die Umwandlung läuft weiterhin serverseitig in einem eigenen
  Hintergrund-Thread und damit unabhängig davon, ob die Weboberfläche
  gerade sichtbar ist.
- Neues Einstellungen-Panel auf der Weboberfläche: Glättung
  (0–10 Iterationen, Taubin-Filter), Vereinfachung/Dezimierung
  (Ziel-Dreiecke in %), Ebene Flächen zusammenführen (an/aus),
  Rundungserkennung (an/aus).
- Neu: Erkennung von Zylindern/Kugeln im Netz per Regionenwachstum +
  RANSAC-Fit, Anzeige von Radius und Trefferquote auf der
  Weboberfläche. Rein informativ - ändert die Geometrie nicht.
- Neu: 3D-Vorschau direkt auf der Weboberfläche (Three.js, lokal
  eingebunden, funktioniert offline) mit Tabs "Vorher" (Original-STL)
  und "Nachher" (neu trianguliertes STEP-Ergebnis).

### Bekannte Einschränkung
- Automatischer Ersatz erkannter Zylinder/Kugeln durch echte
  gekrümmte STEP-Flächen wurde versucht und verworfen: Der
  Naht-/Solid-Aufbau an den Übergangskanten erzeugte in Tests
  ungültige Geometrie. Um keine kaputten STEP-Dateien auszuliefern,
  bleibt es bei Erkennung + Anzeige (siehe "Ideen für später").
- Bei aktivierter Glättung können bislang auch ursprünglich exakt
  ebene Bereiche (z. B. Deckflächen) minimal wölben und dadurch
  fälschlich als Kugel/Zylinder gemeldet werden. Rein kosmetisch bei
  der Info-Anzeige, die STEP-Datei selbst ist davon nicht betroffen.

## 1.0.0
- Erste Version.
- Weboberfläche im dunklen Dashboard-Stil (Upload, Fortschrittsbalken, Download).
- STL → STEP Konvertierung: Netz vernähen, Volumenkörper bauen, ebene
  Flächen zusammenführen (Flächenrückführung für ebene Bereiche).
- Versionsnummer wird auf der Weboberfläche angezeigt.
- GitHub-Actions-Workflow: Windows-exe und macOS-App per PyInstaller,
  beide starten sichtbar in einem Terminal-/Konsolenfenster.
- Release wird automatisch erstellt, sobald ein Tag `vX.Y.Z` gepusht wird.

## Ideen für später
- Echte Flächenrückführung für gekrümmte/freiformige Bereiche: die in
  1.1.0 verworfene automatische Ersetzung durch analytische STEP-
  Flächen müsste die Randkanten der erkannten Region erst sauber auf
  die ideale Fläche projizieren (inkl. Toleranz-Handling), bevor der
  Flächen-/Naht-Aufbau zuverlässig gelingt - vermutlich nur mit
  deutlich mehr Aufwand und Testfällen robust lösbar.
- Sagitta-Schwelle der Rundungserkennung weiter verfeinern, damit sie
  auch nach starker Glättung nicht auf minimal gewölbte, eigentlich
  ebene Bereiche anspringt.
- Mehrere Dateien gleichzeitig hochladen/umwandeln (Warteschlange).
