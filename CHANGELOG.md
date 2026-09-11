# Changelog

## 1.3.1 - Fix: Programm haengt bei sehr grossen Dateien
- **Ursache 1 (der eigentliche Haenger) behoben:** Die Facetten-
  Normalen-Reparatur wurde bisher IMMER ausgefuehrt, auch wenn die
  Wicklung des Netzes schon konsistent war. Bei sehr grossen Netzen
  (getestet: 5,2 Mio. Dreiecke / 250 MB) konnte allein das ueber 5
  Minuten dauern, obwohl die reine PRUEFUNG (ob eine Reparatur ueber-
  haupt noetig ist) nur Millisekunden braucht. Jetzt: erst pruefen,
  nur bei tatsaechlichem Bedarf reparieren.
- **Ursache 2 (wichtiger, unabhaengiger Fund): Speicher statt Zeit.**
  Eigene Messung ergab einen linearen Speicherbedarf von ca. 17-18 KB
  PRO DREIECK beim Volumenkoerper-Aufbau (ein OpenCASCADE-B-Rep-Face
  ist ein vergleichsweise schweres Objekt). Bei grossen Dateien fuehrte
  das zu einem harten Out-of-Memory-Absturz, der sich von aussen nicht
  von einem Haenger unterscheiden liess.
- **Neu: speicheradaptive Obergrenze.** Das Programm schaetzt beim
  Start ueber den tatsaechlich verfuegbaren Arbeitsspeicher (`psutil`),
  wie viele Dreiecke sicher verarbeitbar sind, und bricht bei zu
  grossen Dateien SOFORT (statt nach langem Warten oder einem Absturz)
  mit einer klaren, umsetzbaren Fehlermeldung ab, die auf die
  "Vereinfachung"-Einstellung verweist.
- **Neu: Live-Fortschritt waehrend des Volumenkoerper-Aufbaus.** Vorher
  gab es zwischen 15% und 38% keine einzige Zwischenmeldung - bei
  mehreren Minuten Laufzeit wirkte das wie ein Haenger, obwohl im
  Hintergrund gearbeitet wurde. Jetzt werden regelmaessig ("Facette X
  von Y") Zwischenstaende gemeldet.

## 1.3.0 - Grosse Performance-Ueberarbeitung
- **Sewing eliminiert:** Der bisher groesste Engpass (BRepBuilderAPI_Sewing)
  entfaellt fuer die meisten Netze komplett. Ein neuer schneller Pfad baut
  den Volumenkoerper direkt aus geteilter Topologie auf (jeder Eckpunkt/
  jede Kante wird nur einmal angelegt, statt die Nachbarschaft ueber
  eine teure toleranzbasierte Naeherungssuche neu zu entdecken). Grundlage:
  eine gezielte Recherche zu OpenCASCADE-Performance, die bestaetigte,
  dass Sewing laut den OCCT-Entwicklern selbst "nicht fuer diese Art von
  Eingabe ausgelegt" ist, wenn - wie zuvor - jedes Dreieck als
  unabhaengige Einzelflaeche eingespeist wird.
- **Ergebnis in eigenen Benchmarks:** 81.920 Dreiecke (Kugel) vorher
  ca. 133s, jetzt ca. 29s (~4,5x schneller) - bei identischem, weiterhin
  vollstaendig geprueftem Volumenkoerper.
- **Sicherheitsnetz bleibt bestehen:** Der schnelle Pfad wird nur
  versucht, wenn das Netz bereits wasserdicht und wicklungskonsistent
  ist, und das Ergebnis wird danach trotzdem validiert. Schlaegt
  irgendein Schritt fehl, faellt die Umwandlung automatisch auf den
  bisherigen, langsameren aber toleranteren Sewing-Pfad zurueck. Am
  Ende steht dadurch immer entweder ein echter, geprueft gueltiger
  Volumenkoerper oder eine ehrliche "Netz nicht wasserdicht"-Meldung -
  nie werden unbearbeitete Rohdreiecke als Ergebnis ausgeliefert.
- RANSAC-Zylinder-/Kugel-Fit laeuft jetzt auf einer Stichprobe statt auf
  allen Punkten einer Region (Trefferquote wird danach auf allen
  Punkten nachgerechnet) - bei sehr grossen einzelnen Rundungen
  (z. B. einer kompletten Kugel aus zehntausenden Facetten) allein
  dadurch bis zu 36x schneller.
- Volumenkoerper-Pruefung nach dem schnellen Aufbau nutzt eine reine
  Topologiepruefung statt der vollen geometrischen Kontrolle (bei
  exakt geteilter Topologie durch Konstruktion ausreichend und ca. 40%
  schneller). Der eingebaute Parallel-Modus von OCCTs Pruefroutine
  wurde in eigenen Tests dagegen NICHT schneller (teils sogar
  langsamer) und wird deshalb bewusst nicht verwendet.

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
