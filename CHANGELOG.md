# Changelog

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
- Echte Flächenrückführung für gekrümmte/freiformige Bereiche
  (Segmentierung + NURBS-Flächenanpassung, z. B. Ebenen, Zylinder,
  Kugeln automatisch erkennen).
- Mesh-Vorverarbeitung (Glättung/Reduktion) vor der Umwandlung, mit
  einstellbarer Toleranz auf der Weboberfläche.
- Mehrere Dateien gleichzeitig hochladen/umwandeln (Warteschlange).
