#!/bin/zsh
# MX Battery maintenance audit: mechanical checks for security posture,
# vulnerabilities, and dependency hygiene. Interpret results with the
# /mx-battery-audit skill (.claude/skills/mx-battery-audit/SKILL.md).
#
# Note: pip-audit queries the PyPI advisory database over the network.
# That is a dev-machine action; the app itself remains network-free.

set -uo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/MXBattery.app"
PY="$SRC/.venv/bin/python"
FAIL=0

section() { echo; echo "=== $1 ==="; }

section "1. Known vulnerabilities (pip-audit over the full venv: runtime + build + dev)"
"$SRC/.venv/bin/pip-audit" --skip-editable 2>&1 | grep -v "cachecontrol" || FAIL=1
[[ ${pipestatus[1]} -ne 0 ]] && FAIL=1
echo "(runtime deps shipped in the app: rumps, pyobjc-*; HID access is in-repo ctypes)"

section "2. Embedded Python in the installed bundle vs Homebrew"
BUNDLED=$(defaults read \
  "$APP/Contents/Frameworks/Python.framework/Versions/3.13/Resources/Info.plist" \
  CFBundleVersion 2>/dev/null)
echo "bundle:   Python ${BUNDLED:-NOT FOUND}"
echo "homebrew: $(/opt/homebrew/bin/python3.13 --version 2>/dev/null || echo 'python3.13 missing')"
echo "(if Homebrew is ahead on a security release, rebuild + re-sign the app)"

section "3. App guarantees: no network code in app modules"
if grep -rn -E "import (socket|urllib|http|requests|ssl)|urlopen|Session\(" "$SRC/mxbattery" "$SRC/launcher.py"; then
  echo "VIOLATION: network-capable import found in app code"; FAIL=1
else
  echo "OK: no network imports"
fi

section "4. App guarantees: HID writes only from the query builder"
WRITES=$(grep -rn --exclude-dir=__pycache__ "\.write(\|IOHIDDeviceSetReport" "$SRC/mxbattery" \
  | grep -v "def _request" | grep -v "dev.write(frame)" | grep -v "machid.py")
if [[ -n "$WRITES" ]]; then
  echo "REVIEW: HID/file write outside _request():"; echo "$WRITES"; FAIL=1
else
  echo "OK: single write site (_request query frame)"
fi

section "5. Dependency pinning discipline"
if grep -Ev "^\s*(#|$)" "$SRC/requirements.txt" "$SRC/requirements-build.txt" | grep -v "=="; then
  echo "VIOLATION: unpinned requirement above"; FAIL=1
else
  echo "OK: all requirements pinned with =="
fi

section "6. Source vs installed bundle drift"
DRIFT=0
for f in "$SRC"/mxbattery/*.py; do
  if ! diff -q "$f" "$APP/Contents/Resources/lib/python3.13/mxbattery/$(basename "$f")" >/dev/null 2>&1; then
    echo "DRIFT: $(basename "$f") differs from installed copy"; DRIFT=1
  fi
done
[[ $DRIFT -eq 0 ]] && echo "OK: installed app matches source" || echo "(run ./update_app.sh to sync)"

section "7. Outdated pinned packages (informational)"
"$SRC/.venv/bin/pip" list --outdated 2>/dev/null | head -15

echo
[[ $FAIL -eq 0 ]] && echo "AUDIT RESULT: PASS (review informational sections)" \
                  || echo "AUDIT RESULT: ATTENTION NEEDED (see sections above)"
exit $FAIL
