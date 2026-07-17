#!/bin/zsh
# In-place updater for /Applications/MXBattery.app.
#
# TCC (Input Monitoring) identifies the app by its main executable's code
# signature. The app's Python source lives in Resources/ and is not part of
# that signature, so pure-Python changes can be synced into the installed
# bundle without invalidating the permission grant.
#
# Use this for changes to mxbattery/*.py or launcher.py ONLY. If you changed
# dependencies, Python version, or setup.py, do a full rebuild instead
# (python setup.py py2app) and re-grant Input Monitoring — see README.

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/MXBattery.app"
DEST="$APP/Contents/Resources/lib/python3.13"

[[ -d "$DEST/mxbattery" ]] || { echo "error: $DEST/mxbattery not found — full rebuild needed?"; exit 1; }

echo "Stopping MX Battery…"
pkill -f "$APP" 2>/dev/null || true
sleep 1

echo "Syncing Python source…"
cp "$SRC"/mxbattery/*.py "$DEST/mxbattery/"
cp "$SRC/launcher.py" "$APP/Contents/Resources/launcher.py"
# Drop stale bytecode so the new source is the only truth.
find "$DEST/mxbattery" -name __pycache__ -type d -exec rm -rf {} +

echo "Relaunching…"
open "$APP"
echo "Done. Input Monitoring grant untouched."
