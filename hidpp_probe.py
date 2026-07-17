#!/usr/bin/env python3
"""Standalone diagnostic: read the mouse battery once and print details.

Uses the same modules as the app (mxbattery.hidpp over mxbattery.machid),
so a passing probe means the app's core read path works.
"""

import sys

from mxbattery import hidpp, machid


def main():
    print("Logitech HID devices visible:")
    found_any = False
    for ref, pid, product in machid.enumerate_devices(hidpp.VENDOR_LOGITECH):
        supported = "supported" if pid in hidpp.SUPPORTED_PIDS else "unsupported"
        print(f"  pid={pid:#06x} {product!r} ({supported})")
        machid.release_ref(ref)
        found_any = True
    if not found_any:
        print("  none")

    try:
        r = hidpp.read_battery()
    except hidpp.DeviceNotFound:
        print("No supported mouse connected (off, asleep, or out of range).")
        sys.exit(1)
    except hidpp.DeviceNotResponding as exc:
        print(f"Mouse not responding: {exc}")
        print("If this says 'cannot open', grant Input Monitoring to the")
        print("process running this script and try again.")
        sys.exit(2)

    pct = "n/a (level unavailable while charging)" if r.percentage is None else f"{r.percentage}%"
    print(f"{r.device_name}: {pct}, {r.status_label}")


if __name__ == "__main__":
    main()
