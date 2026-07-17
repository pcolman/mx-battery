---
name: mx-battery-audit
description: Run the MX Battery security and dependency maintenance audit — checks pinned deps for CVEs, embedded Python currency, no-network and read-only-HID guarantees, pin discipline, and source/bundle drift. Use when asked to audit, update, or check the health of the MX Battery app.
---

# MX Battery maintenance audit

Run `./audit.sh` from the repository root and interpret the results.
The script is mechanical; your job is judgment on top of it.

## How to interpret each section

1. **pip-audit findings**: for each CVE, state whether it is reachable in
   MX Battery's usage (the app has no network access, parses no untrusted
   input except 20-byte HID frames from the mouse, and runs unprivileged).
   Recommend the pinned-version bump regardless, but rank urgency by
   reachability.
2. **Embedded Python**: the py2app bundle freezes Python at build time and
   does NOT get Homebrew updates. If Homebrew's 3.13.x is ahead on a
   security release, recommend: rebuild venv against it, full py2app
   rebuild, codesign (see README "Updating"), reinstall, re-grant Input
   Monitoring unless a stable signing certificate is in use.
3. **No-network check**: any hit is a hard failure — this project requires
   zero network capability in the app. Find who introduced it.
4. **Single-write-site check**: the only HID write must be the query frame
   in `_request()` in `mxbattery/hidpp.py`. Anything else breaks the
   read-only guarantee; treat as a hard failure.
5. **Pinning**: every non-comment line in both requirements files must use
   `==`. Fix immediately if not.
6. **Drift**: if source differs from the installed bundle, ask the user
   which is authoritative before syncing (the source may have been edited
   without deploying, or the change may be unwanted).
7. **Outdated list**: informational. Do not chase latest versions for
   their own sake: this project prefers stable pins, updated for security
   advisories or needed fixes only.

## Update protocol (when bumping a dependency)

- Change the pin in requirements.txt / requirements-build.txt.
- `hidapi` must be reinstalled with `--no-binary hidapi` (compile from
  source — hard requirement).
- Any dependency change requires a FULL py2app rebuild, not
  update_app.sh. Follow README "Updating the installed app".
- After rebuild, re-run this audit, then verify the app end to end:
  launch, battery reads, Refresh Now works.

## Known accepted risks (do not re-raise as new findings)

- setuptools 80.9.0 / PYSEC-2026-3447 (fix: 83.0.0): pinned back because
  py2app 0.28.x's boot script requires pkg_resources, removed in newer
  setuptools. Build-time only; not shipped code paths reachable from the
  running app. Accepted 2026-07-17. Re-check each audit whether a newer
  py2app has dropped the pkg_resources requirement; if so, propose
  upgrading both together (full rebuild + re-sign).

## Cadence

Monthly, or immediately after any dependency change or macOS major
upgrade. If the audit hasn't run in over a month, suggest it when
working in this project.
