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

## Performance bei großen Netzen

**Der größte Engpass wurde beseitigt:** Bis Version 1.2.0 lief jede
Umwandlung über OpenCASCADEs `BRepBuilderAPI_Sewing` - ein
toleranzbasiertes Verfahren, das bei tausenden Einzelfacetten deutlich
schlechter als linear skaliert (eigene Messung: 4x mehr Dreiecke →
ca. 7x mehr Zeit). Eine gezielte Recherche zur OCCT-Performance ergab:
Die OCCT-Entwickler selbst bestätigen im offiziellen Forum, dass Sewing
für genau diesen Anwendungsfall (ein Dreieck = eine unabhängige Fläche)
"nicht ausgelegt" ist und empfehlen, geteilte Topologie direkt
aufzubauen.

Seit Version 1.3.0 tut dieses Programm genau das: Eckpunkte und Kanten
werden aus der ohnehin bekannten Netz-Nachbarschaft **jeweils nur ein
einziges Mal** angelegt und von den angrenzenden Dreiecken gemeinsam
genutzt - der Volumenkörper ist dadurch beim Aufbau bereits
"vernäht", ein Sewing-Aufruf entfällt komplett. Zusätzlich läuft die
Zylinder-/Kugel-Erkennung selbst jetzt auf einer Stichprobe statt auf
allen Punkten einer Region, und die Volumenkörper-Prüfung nach dem
schnellen Aufbau nutzt eine günstigere reine Topologieprüfung.

**Ergebnis in eigenen Benchmarks** (identische Hardware, gleiche
Testdatei, jeweils vollständig geprüfter, gültiger Volumenkörper):

| Dreiecke | Vorher (v1.2.0) | Jetzt (v1.3.0) | Faktor |
|---|---|---|---|
| 20.480 | ca. 18,5 s | ca. 7,5 s | ~2,5x |
| 81.920 | ca. 133 s | ca. 29 s | ~4,5x |

**Wichtig - Korrektheit geht vor Tempo:** Der schnelle Pfad wird nur
versucht, wenn das Netz bereits als wasserdicht und wicklungskonsistent
erkannt wird. Schlägt er dennoch fehl (z. B. bei ungewöhnlich
unsauberen Netzen), fällt die Umwandlung automatisch auf den
bisherigen, langsameren aber toleranteren Sewing-Pfad zurück. Am Ende
steht dadurch **immer** entweder ein echter, geprüft gültiger
Volumenkörper oder eine ehrliche „Netz nicht wasserdicht"-Meldung -
nie werden unbearbeitete Rohdreiecke als Ergebnis ausgeliefert.

**Verbleibende, ehrliche Grenze - Arbeitsspeicher, nicht nur Zeit:**
Der Flächen-Aufbau läuft als Python-Schleife über jede Facette (mehrere
OpenCASCADE-Aufrufe pro Dreieck) - das ist inzwischen linear statt
überlinear in der Zeit, aber der eigentlich limitierende Faktor ist
Speicher: Ein einzelnes OpenCASCADE-B-Rep-Face ist ein vergleichsweise
schweres Objekt (parametrisierte Fläche + Kanten + Kurven + Toleranzen),
kein schlankes Dreieck. Eigene Messung: linear ca. 17-18 KB Speicher
**pro Dreieck** während des Aufbaus. Bei einem Netz mit mehreren
hunderttausend bis Millionen Dreiecken reicht das, um selbst auf
Rechnern mit mehreren GB RAM ein hartes Out-of-Memory auszulösen - ein
Absturz, der sich von aussen nicht von einem Hänger unterscheiden lässt
und (anders als ein Zeitlimit) nicht softwareseitig abgefangen werden
kann, sobald er eintritt.

**Deshalb schätzt das Programm beim Start automatisch**, wie viele
Dreiecke der tatsächlich verfügbare Arbeitsspeicher sicher zulässt
(über `psutil`). Reicht die vom Nutzer gewählte (oder keine)
Vereinfachung nicht aus, um darunter zu bleiben, wird **automatisch
zusätzlich nachdezimiert** - der Nutzer muss also nicht selbst den
passenden Prozentwert erraten.

**Wie viel Detail dabei erhalten bleibt, hängt stark von der Geometrie
ab:** Bei technischen/mechanischen Teilen (Platten, Bohrungen, Wellen)
werden grosse ebene und runde Bereiche ohnehin zu wenigen echten
Flächen zusammengefasst (siehe oben) - dort wirkt sich die
Speichergrenze kaum auf die sichtbare Qualität aus. Bei organischen
oder gescannten Formen (Freiform, kaum ebene/runde Bereiche) bleibt
dagegen fast jedes Dreieck eine eigene Facette, und eine notwendige
Vereinfachung ist dort direkt als gröbere, kantigere Oberfläche
sichtbar. Für diesen Fall geht das Programm bewusst so nah wie möglich
an das tatsächliche Speicherlimit heran (nicht nur pauschal
vorsichtig): Eine **Live-Überwachung** während des Aufbaus bricht bei
tatsächlich knapp werdendem Speicher sauber ab (kein Absturz) und
startet automatisch mit etwas stärkerer Vereinfachung neu - bis zu
dreimal. Nur wenn das nicht ausreicht, erfolgt ein klarer
Fehlerabbruch. Die Vereinfachung selbst läuft in einer schnellen, für
große Netze ausgelegten Bibliothek (`fast-simplification`) und
passiert *vor* dem OpenCASCADE-Aufbau, ist also von dieser
Speichergrenze nicht betroffen. Ab automatisch 300.000 Dreiecken wird
zusätzlich die Zylinder-/Kugel-Erkennung übersprungen (reine
Flächenrückführung läuft trotzdem weiter).

Während des Aufbaus selbst wird jetzt außerdem laufend der Fortschritt
gemeldet ("Facette X von Y"), damit auch eine mehrminütige Umwandlung
sichtbar voranschreitet statt wie eingefroren zu wirken.

## Robustheit bei Hintergrund-Tabs / Verbindungsaussetzern / Speicherproblemen

Die Umwandlung läuft in einem **eigenen Prozess** (nicht nur einem
Thread) und damit unabhängig davon, ob der Browser-Tab gerade sichtbar
ist. Der Live-Fortschritt (SSE) wird per Heartbeat abgesichert; bricht
die Verbindung trotzdem ab, übernimmt automatisch eine Status-Abfrage
im Hintergrund. Die Job-ID wird im Browser gespeichert, sodass ein
Neuladen der Seite den laufenden bzw. fertigen Auftrag wiederfindet.

Die Prozess-Isolation ist bewusst gewählt: Bei sehr großen Dateien kann
die Umwandlung mehrere GB Arbeitsspeicher benötigen. Liefe das im
selben Prozess wie der Webserver, könnte (a) wiederholt angefragter
Speicher bei mehreren Versuchen hintereinander nicht zuverlässig wieder
freigegeben werden, und (b) ein tatsächliches Out-of-Memory den
**kompletten Webserver** abstürzen lassen. Als eigener Prozess wird der
Speicher beim Beenden garantiert zurückgegeben, und ein Absturz betrifft
immer nur den einen Auftrag - der Webserver selbst bleibt erreichbar
und meldet dem Nutzer einen klaren Fehler.

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
