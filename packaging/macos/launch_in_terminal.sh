#!/bin/bash
# Wird von GitHub Actions als "sichtbarer" Einstiegspunkt in das .app-Bundle
# eingesetzt. Er ersetzt das eigentliche PyInstaller-Binary an der Stelle,
# an der macOS das Programm startet, und oeffnet stattdessen ein
# Terminal-Fenster, in dem das echte Binary laeuft. So kann man das
# Programm einfach per Fenster-schliessen oder STRG+C beenden - genau wie
# unter Windows, wo automatisch ein Konsolenfenster erscheint.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_BIN="$DIR/STL-STEP-Konverter-MK1-bin"

osascript -e "tell application \"Terminal\" to do script \"'$REAL_BIN'; exit\""
osascript -e 'tell application "Terminal" to activate'
