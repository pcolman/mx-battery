# Security Policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: Security tab > Report a
vulnerability. Reports are reviewed within a week.

## Scope and design guarantees

MX Battery is designed so that the interesting attack surface stays
small; anything that violates these properties is a security bug worth
reporting:

- The app never sends anything to the mouse except two read-only
  HID++ query functions (single write site: `_request()` in
  `mxbattery/hidpp.py`).
- The app has no network capability of any kind.
- HID access is in-repo (`mxbattery/machid.py`, ctypes + IOKit) and
  opens devices shared, never exclusive.
- The only macOS permission used is Input Monitoring.
- All dependency versions are pinned.

## Supported versions

Only the latest release is supported. `audit.sh` documents the
dependency review process; known accepted risks are listed in the
README.
