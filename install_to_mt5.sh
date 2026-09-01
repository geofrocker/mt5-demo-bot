#!/usr/bin/env bash
# Copy and compile the bridge EA inside the Wine-wrapped MetaTrader 5 folder.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT/MQL5/Experts/PythonBridgeEA.mq5"
PREFIX="${HOME}/Library/Application Support/net.metaquotes.wine.metatrader5"
MT5="${PREFIX}/drive_c/Program Files/MetaTrader 5"
DEST_DIR="${MT5}/MQL5/Experts"
WINE="/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine64"

if [[ ! -f "$SRC" ]]; then
  echo "Missing $SRC" >&2
  exit 1
fi
if [[ ! -d "$DEST_DIR" ]]; then
  echo "MT5 Experts folder not found at: $DEST_DIR" >&2
  echo "Is MetaTrader 5 installed?" >&2
  exit 1
fi

cp "$SRC" "$DEST_DIR/PythonBridgeEA.mq5"
echo "Copied to: $DEST_DIR/PythonBridgeEA.mq5"

if [[ -x "$WINE" ]]; then
  export WINEPREFIX="$PREFIX"
  (cd "$MT5" && "$WINE" metaeditor64.exe /compile:MQL5\\Experts\\PythonBridgeEA.mq5 /log) || true
  if [[ -f "$DEST_DIR/PythonBridgeEA.ex5" ]]; then
    echo "Compiled: $DEST_DIR/PythonBridgeEA.ex5"
  else
    echo "Compile did not produce .ex5 — open the file in MetaEditor and press F7."
  fi
fi

echo
echo "In MT5: Navigator → Expert Advisors → Refresh,"
echo "reattach PythonBridgeEA on each enabled-symbol H4 chart, enable Algo Trading."
echo "Leave EnableTcp=false when more than one chart is attached."
