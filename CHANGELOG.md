# Changelog

## 1.5.2 - Versionsnummer wieder im GitHub-Actions-Build sichtbar
- Der Build-Workflow liest jetzt zu Beginn die Versionsnummer aus
  `version.py` aus und verwendet sie im PyInstaller-Programmnamen
  sowie in den Namen der Artefakt-/Release-Zip-Dateien (z. B.
  `STL-STEP-Konverter-MK1-v1.5.2-windows.zip` statt nur
  `STL-STEP-Konverter-MK1-windows.zip`).

## 1.5.1 - Fix: Build-Fehler durch falsche pymeshfix-Versionsangabe
- **Build-Fix:** `requirements.txt` verlangte faelschlich
  `pymeshfix>=1.0` - diese Bibliothek hat aber nie eine 1.x-Version
  veroeffentlicht (aktuell: 0.18.x), wodurch die GitHub-Actions-Builds
  fehlschlugen ("No matching distribution found"). Korrigiert auf
  `pymeshfix>=0.16`.

## 1.5.0 - Automatische Lochreparatur fuer echte Scan-Luecken
- **Wichtige Klarstellung zu einem oft gemeldeten "Loch"-Problem:**
  Nicht jedes gemeldete Loch war ein Fehler in der Umwandlung selbst -
  viele echte 3D-Scans (besonders komplexe Baugruppen mit duennen
  oder ueberlappenden Teilen, z. B. Propeller/Drohnen-Rahmen) sind
  bereits im Original NICHT wasserdicht. Das Programm hat das bisher
  ehrlich als "Offene Flaeche (Netz war nicht wasserdicht)" gemeldet -
  aber ohne eine Moeglichkeit, das zu beheben.
- **Neu: automatische Lochreparatur.** Ist das eingelesene Netz nicht
  wasserdicht, wird jetzt automatisch versucht, die Luecken zu
  schliessen (ueber die auf Wasserdichtigkeit spezialisierte
  Bibliothek `pymeshfix`) - deutlich robuster als einfache
  Loch-Fuellung, die nur bei simplen, kleinen Luecken zuverlaessig
  funktioniert. In eigenen Tests: ein Netz mit vier unterschiedlich
  komplexen Luechern wurde erfolgreich vollstaendig repariert und
  anschliessend zu einem gueltigen Volumenkoerper mit exaktem Volumen
  umgewandelt (Abweichung < 0,001% vom Original). Auch bei groesseren
  Netzen (80.000+ Dreiecke, 200 verstreute Luecken) dauert die
  Reparatur unter einer Sekunde.
- Schlaegt die Reparatur trotzdem fehl oder bleiben Luecken uebrig,
  faellt die Umwandlung wie bisher ehrlich auf die offene-Flaeche-
  Meldung zurueck - es wird nichts vorgetaeuscht.

## 1.4.3 - Fix: Lueckenpruefung war selbst fehlerhaft
- **Ursache des in 1.4.2 weiterhin gemeldeten Lochs gefunden:** Die
  in 1.4.2 eingefuehrte, selbstgeschriebene Lueckenpruefung (Kanten
  zaehlen + Sonderfaelle fuer Pole und Nahtkanten periodischer
  Flaechen von Hand behandeln) hatte selbst einen blinden Fleck und
  hat ein echtes Loch nicht zuverlaessig erkannt.
- **Fix:** Die eigene Kantenzaehlung wurde durch OpenCASCADEs
  eingebaute Pruefung `BRepCheck_Shell.Closed()` ersetzt - gezielt
  gegen beide kritischen Faelle getestet: erkennt ein absichtlich
  kaputtes Testmodell (fehlende Flaeche) korrekt als nicht
  geschlossen, UND stuft eine volle Kugel (periodische Naht) weiterhin
  korrekt als geschlossen ein (kein erneutes Fehlalarm-Risiko wie bei
  der vorherigen Handimplementierung).
- **Zusaetzlich unabhaengig verifiziert:** Alle erzeugten STEP-Dateien
  wurden ueber einen komplett separaten Lese-Pfad (STEPControl_Reader)
  erneut eingelesen und mit der vollen, strengen BRepCheck_Analyzer-
  Pruefung bestaetigt - inklusive der Datei, die ueber die Web-API
  heruntergeladen wird.

## 1.4.2 - Fix: Loch im Modell + ganze Regionen statt Facetten-Rand
- **Kritischer Fix:** Die in 1.4.1 komplett entfernte Gueltigkeits-
  pruefung hat tatsaechlich kaputte Geometrie durchgelassen (sichtbares
  Loch im Modell, Datei liess sich in anderer Software nicht oeffnen).
  Das war kein Fehlalarm der Pruefung, sondern ein echter Fehler.
  Ersetzt durch eine GEZIELTE Lueckenpruefung: Es wird weiterhin
  geprueft, ob jede Kante zu genau zwei Flaechen gehoert (das ist die
  konkrete Ursache eines Lochs) - aber nicht mehr die volle, oft zu
  strenge OCCT-Rundum-Pruefung, die auch wegen rein kosmetischer
  Toleranzfragen angeschlagen hat. Dabei wurde ein weiterer Bug
  gefunden und behoben: die neue Pruefung schlug faelschlich bei einer
  kompletten Kugel an (periodische Naht-Kante wurde falsch gezaehlt).
- **Grundlegende Verbesserung der Scan-Glaettung:** Statt nur einen
  Innenbereich mit einem Facetten-Rand drumherum zu ersetzen, wird
  jetzt die GESAMTE zusammenhaengende Freiform-Region durch eine
  einzige Flaeche ersetzt - kein Facetten-Rest mehr am Rand. Das
  behebt gleich zwei gemeldete Probleme: kleinere Ausgabedatei (im
  Test 2151 statt 3738 STEP-Eintraege) und tatsaechlich "eine Flaeche
  aus mehreren Dreiecken" statt weiterhin vieler kleiner Facetten.
  Im Test: 768 Facetten wurden zu nur noch 6 Flaechen (vorher 48).

## 1.4.1 - Sicherheitspruefung entfernt + Vorschau-Schattierung korrigiert
- **Auf Nutzerwunsch entfernt:** Die abschliessende, sehr strikte
  BRepCheck_Analyzer-Gueltigkeitspruefung nach einer Zylinder-/Kugel-
  oder Glaettungs-Ersetzung wurde entfernt. Sie hat bisher
  Ersetzungsversuche komplett verworfen, ohne dass sichtbar wurde, was
  dabei entstanden waere - dadurch liess sich nicht beurteilen, ob das
  Ergebnis trotz formaler OCCT-Beanstandung praktisch besser gewesen
  waere als die reinen Facetten. Das Ergebnis wird jetzt immer
  geliefert, sobald es sich zu einem einzigen Volumenkoerper
  zusammenbauen liess (weiterhin verworfen wird nur, wenn gar kein
  einzelner, wasserdichter Koerper entstehen konnte).
- **Vorschau-Bug behoben:** Die 3D-Vorschau im Browser rendert die
  STEP-Datei zwangslaeufig als neu trianguliertes Dreiecksnetz (Three.js
  kann keine echten Flaechen zeichnen) - bisher wurde dafuer immer
  FLACH schattiert (jede Facette einzeln sichtbar), selbst wenn die
  STEP-Datei tatsaechlich eine echte glatte Flaeche enthielt. Das liess
  ein erfolgreich geglaettetes Ergebnis in der Vorschau faelschlich
  weiterhin facettiert aussehen. Die "Nachher"-Ansicht wird jetzt glatt
  schattiert (interpolierte Normalen), die "Vorher"-Ansicht (rohes STL)
  bleibt bewusst flach schattiert, um die tatsaechliche Facettenstruktur
  ehrlich zu zeigen.

## 1.4.0 - Scan-Glaettung funktioniert jetzt wirklich
- **Durchbruch:** Die "Scan-Oberflaeche glaetten"-Einstellung (seit
  1.4.0 nicht mehr experimentell) erzeugt jetzt zuverlaessig eine
  ECHTE glatte Flaeche ohne Facetten - kein Kompromiss mehr. Nach
  fuenf gescheiterten Ansaetzen (grosse Naht-Toleranz, automatische
  OCCT-Projektion, manuelle 2D-Parametrisierung, u. a.) wurde eine
  gezielte Recherche zu Alternativen durchgefuehrt: Die Loesung ist
  `BRepOffsetAPI_MakeFilling` - ein OpenCASCADE-Werkzeug, das eine
  Flaeche DIREKT aus Randkurven aufbaut, statt eine Flaeche zu fitten
  und nachtraeglich zu beschneiden. Dadurch entfaellt das gesamte
  Trimm-/Parametrisierungsproblem, an dem alle vorherigen Versuche
  gescheitert sind.
- Die Randkanten der neuen glatten Flaeche sind exakt dieselben
  (geteilten) Kanten, die die umgebenden Original-Facetten ohnehin
  schon verwenden - dadurch ist keine grosse, riskante Naht-Toleranz
  mehr noetig (die kleine Standard-Toleranz reicht).
- Wenige innere Stuetzpunkte (automatisch 12, 10, 8 ... als Kandidaten
  durchprobiert) ziehen die Flaeche zusaetzlich zu den entrauschten
  Hoehenwerten der Scan-Daten, statt nur die Randkurven zu erfuellen.
- Eigene Messung am Testobjekt (Box mit verrauschter Woelbung, 768
  Dreiecke): Ergebnis ist ein gueltiger Volumenkoerper mit 48 statt
  768 Flaechen, korrektem Volumen (Abweichung < 5% vom Original -
  gewollt, da das Rauschen ja herausgerechnet werden soll).
- Funktioniert weiterhin nur, wenn der schnelle (geteilte-Topologie-)
  Aufbau erfolgreich war - beim selteneren Sewing-Fallback-Pfad wird
  die Glaettung sauber uebersprungen (kein Absturz, siehe README).

## 1.3.4 - Mehr Detail bei organischen/gescannten Formen
- **Hintergrund:** Bei Netzen ohne grosse ebene oder runde Bereiche
  (organische Scan-Daten, Freiformflaechen) gibt es fast nichts, was
  sich zu wenigen grossen Flaechen zusammenfassen liesse - fast jedes
  Dreieck bleibt eine eigene Facette. Die bisherige, vorsichtig
  geschaetzte Speichergrenze fuehrte hier zu spuerbar grobem,
  kantigem Ergebnis, weil automatisch zu stark vereinfacht wurde.
- **Grosszuegigere Grenze:** Da ein Fehlschlag seit 1.3.2 nur noch den
  einen Umwandlungs-Prozess betrifft (nicht mehr den ganzen
  Webserver), darf die a-priori-Schaetzung jetzt naeher an das
  tatsaechliche Limit herangehen (Sicherheitsfaktor 0,5 -> 0,7,
  Speicherschaetzung pro Dreieck 22.000 -> 19.000 Bytes, naeher an der
  gemessenen Realitaet). In eigenen Tests ca. 60% mehr nutzbare
  Dreieckszahl bei gleichem verfuegbarem Speicher.
- **Neu: Live-Speicherueberwachung waehrend des Aufbaus.** Statt sich
  nur auf die Vorab-Schaetzung zu verlassen, wird der tatsaechlich
  verfuegbare Speicher waehrend des Aufbaus laufend geprueft. Wird er
  knapp, bricht der Aufbau sauber ab (kein Absturz) und startet
  automatisch mit staerkerer Vereinfachung neu (bis zu 3 Versuche).
  Das erlaubt es, so nah wie moeglich an das tatsaechliche Limit
  heranzugehen, ohne das Absturzrisiko zu erhoehen.
- Reicht der Speicher auch nach mehrfachem automatischem Nachschaerfen
  nicht aus, wird sauber mit einer klaren Fehlermeldung abgebrochen
  (der bisherige, noch speicherhungrigere Sewing-Fallback wird in
  diesem Fall bewusst NICHT mehr versucht, da er das Problem eher
  verschaerfen als loesen wuerde).

## 1.3.3 - Fix: Manuelle Vereinfachung reichte nicht aus
- **Problem:** Bei sehr grossen Dateien (mehrere Millionen Dreiecke)
  auf Rechnern mit wenig freiem Arbeitsspeicher reichte selbst die
  staerkste manuell einstellbare Vereinfachung (bisher min. 10%) nicht
  aus, um unter die Speichergrenze zu kommen - der Nutzer haette den
  exakt passenden Prozentwert erraten muessen, ohne dass der Regler
  fein genug war.
- **Fix: automatische Nachdezimierung.** Reicht die vom Nutzer gewaehlte
  (oder keine) Vereinfachung nicht aus, wird das Netz jetzt automatisch
  zusaetzlich so weit vereinfacht, dass es sicher in den verfuegbaren
  Arbeitsspeicher passt - mit klarer Meldung im Fortschritt, was
  passiert ist. Ein Fehlerabbruch erfolgt nur noch, wenn selbst ein
  stark vereinfachtes Netz (unter 200 Dreiecke) nicht mehr passen
  wuerde (praktisch nur bei extrem wenig freiem Speicher).
- Der "Vereinfachung"-Regler auf der Weboberflaeche erlaubt jetzt Werte
  von 1-100% (vorher 10-100% in 5%-Schritten) fuer feinere manuelle
  Kontrolle, falls gewuenscht.

## 1.3.2 - Fix: Speichergrenze schrumpfte bei wiederholten Versuchen
- **Architektur-Fix:** Die eigentliche Umwandlung laeuft jetzt in
  einem EIGENEN Prozess statt nur in einem Thread innerhalb des
  Webserver-Prozesses. Zwei konkrete Probleme werden dadurch behoben:
  1. Bei wiederholten Versuchen mit grossen Dateien konnte die
     angezeigte "verfuegbare" Dreiecksgrenze von Versuch zu Versuch
     kleiner werden - der Webserver-Prozess gab Speicher nicht
     zuverlaessig wieder her. Als eigener Prozess wird der komplette
     Speicher beim Beenden garantiert an das Betriebssystem
     zurueckgegeben; der Webserver selbst bleibt jetzt nachweislich
     stabil (eigene Messung: 334 MB vor drei grossen Testversuchen,
     336 MB danach).
  2. Schwerwiegender: Wuerde eine Umwandlung trotz der Schaetzung
     tatsaechlich zu einem Out-of-Memory fuehren, hat das
     Betriebssystem bisher den KOMPLETTEN Webserver beendet (alle
     Threads teilen sich einen Prozess) - die ganze Anwendung waere
     abgestuerzt. Jetzt betrifft ein solcher Absturz nur den einen
     Auftrag; der Webserver selbst laeuft unbeeintraechtigt weiter und
     meldet dem Nutzer einen klaren Fehler statt komplett unerreichbar
     zu werden.
- Erkennt zusaetzlich den Fall, dass der Umwandlungs-Prozess ohne
  Abschlussmeldung endet (z. B. durch ein hartes Betriebssystem-Limit),
  und meldet dann eine klare Fehlermeldung statt den Auftrag unendlich
  in "laeuft" haengen zu lassen.

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
