#!/usr/bin/env python3
"""Validation probe: read battery from a Logitech MX Master over Bluetooth
using HID++ 2.0, before building the menu bar app around it.

Read-only by design: the only reports ever sent are HID++ *query* messages
(ROOT.getFeature and UNIFIED_BATTERY.get_status). No configuration,
remapping, or firmware functions are implemented anywhere in this project.

Protocol reference: Logitech HID++ 2.0 as documented by the Solaar project
(pwr-Solaar/Solaar, lib/logitech_receiver/hidpp20.py) — reimplemented from
scratch, no Solaar code used.
"""

import ctypes
import sys
import time

import hid

# macOS: hidapi seizes devices exclusively by default, and the OS refuses to
# let anyone seize the system pointer. cython-hidapi doesn't expose the
# shared-mode switch, but the statically linked symbol is reachable.
ctypes.CDLL(hid.__file__).hid_darwin_set_open_exclusive(0)

VENDOR_LOGITECH = 0x046D
# Bluetooth product IDs for supported mice.
SUPPORTED_PIDS = {
    0xB023: "MX Master 3",
    0xB034: "MX Master 3S",
    0xB037: "MX Master 4",  # provisional; probe will report actual PID
}

# HID++ long report: report ID 0x11, 20 bytes total.
LONG_REPORT_ID = 0x11
LONG_REPORT_LEN = 20
# Direct-connected (non-receiver) devices use device index 0xFF.
DEVICE_INDEX = 0xFF
# Software ID: arbitrary 4-bit tag echoed back in responses so we can match
# replies to our own queries. Any value 1-15.
SWID = 0x0A

ROOT_FEATURE_INDEX = 0x00
FEATURE_UNIFIED_BATTERY = 0x1004
FEATURE_BATTERY_STATUS = 0x1000

# 0x1000 status byte meanings (HID++ 2.0 BATTERY_LEVEL_STATUS).
BATTERY_STATUS_1000 = {
    0: "discharging",
    1: "charging",
    2: "charging (almost full)",
    3: "full",
    4: "charging (slow)",
    5: "invalid battery",
    6: "thermal error",
    7: "charging error",
}

READ_TIMEOUT_MS = 2000

CHARGING_STATUS = {
    0: "discharging",
    1: "charging",
    2: "charging (slow)",
    3: "full",
    4: "error",
}


def find_hidpp_interface():
    """Return (path, product_string, pid) for the HID++ vendor-specific
    interface of a supported Logitech mouse, or None.

    A single Bluetooth mouse exposes several HID interfaces (pointer, vendor
    channels). HID++ lives on the Logitech vendor usage page 0xFF43 (older
    firmware: 0xFF00). Long messages are usage 0x0202 on that page.
    """
    candidates = []
    for info in hid.enumerate(VENDOR_LOGITECH):
        pid = info["product_id"]
        if pid not in SUPPORTED_PIDS:
            continue
        if info["usage_page"] in (0xFF43, 0xFF00):
            candidates.append(info)
    # Prefer usage 0x0202 (long-message channel) when present.
    candidates.sort(key=lambda i: 0 if i["usage"] == 0x0202 else 1)
    if not candidates:
        return None
    c = candidates[0]
    return c["path"], c.get("product_string", ""), c["product_id"]


def hidpp_request(dev, feature_index, function, params=b""):
    """Send one HID++ 2.0 query and return the 16 response param bytes.

    Only used for read/query functions in this project.
    """
    fnid_swid = ((function & 0x0F) << 4) | SWID
    payload = bytes([LONG_REPORT_ID, DEVICE_INDEX, feature_index, fnid_swid])
    payload += bytes(params)
    payload += bytes(LONG_REPORT_LEN - len(payload))
    n = dev.write(payload)
    if n < 0:
        raise IOError("HID write failed")

    deadline = time.monotonic() + READ_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        data = dev.read(LONG_REPORT_LEN, timeout_ms=remaining_ms)
        if not data:
            continue
        if data[0] != LONG_REPORT_ID or data[1] != DEVICE_INDEX:
            continue
        # HID++ 2.0 error frame: feature-index slot holds 0xFF, then our
        # original feature index / fnid, then an error code.
        if data[2] == 0xFF and data[3] == feature_index and data[4] == fnid_swid:
            raise IOError(f"HID++ error code {data[5]:#04x}")
        if data[2] == feature_index and data[3] == fnid_swid:
            return bytes(data[4:20])
        # Anything else (battery event broadcasts, other SW's traffic): skip.
    raise TimeoutError("no HID++ response (device asleep or out of range?)")


def main():
    found = find_hidpp_interface()
    if not found:
        print("No supported Logitech mouse found on the HID++ vendor interface.")
        print("All Logitech HID interfaces visible:")
        for info in hid.enumerate(VENDOR_LOGITECH):
            print(
                f"  pid={info['product_id']:#06x} usage_page={info['usage_page']:#06x} "
                f"usage={info['usage']:#06x} product={info.get('product_string','')!r}"
            )
        sys.exit(1)

    path, name, pid = found
    print(f"Opening {SUPPORTED_PIDS.get(pid, 'Logitech')} (pid={pid:#06x}) at {path!r}")
    dev = hid.device()
    dev.open_path(path)
    try:
        # Step 1: ROOT.getFeature — look up the battery feature index on this
        # device/firmware (indices are not fixed). Newer devices expose
        # UNIFIED_BATTERY (0x1004); older MX Master 3 firmware exposes
        # BATTERY_STATUS (0x1000) instead, so probe both like Solaar does.
        feat_index = None
        feat_id = None
        for candidate in (FEATURE_UNIFIED_BATTERY, FEATURE_BATTERY_STATUS):
            params = candidate.to_bytes(2, "big")
            resp = hidpp_request(dev, ROOT_FEATURE_INDEX, 0x0, params)
            if resp[0] != 0:
                feat_index, feat_id = resp[0], candidate
                print(f"Battery feature {candidate:#06x} at index {resp[0]:#04x} "
                      f"(type={resp[1]:#04x}, version={resp[2]})")
                break
            print(f"Feature {candidate:#06x} not present on this device.")
        if feat_index is None:
            print("No supported battery feature found.")
            sys.exit(2)

        if feat_id == FEATURE_UNIFIED_BATTERY:
            # UNIFIED_BATTERY.get_status (function 1).
            resp = hidpp_request(dev, feat_index, 0x1)
            soc = resp[0]
            charging = CHARGING_STATUS.get(resp[2], f"unknown ({resp[2]})")
        else:
            # BATTERY_STATUS.get_battery_level_status (function 0):
            # params[0] = level %, params[1] = next level, params[2] = status.
            resp = hidpp_request(dev, feat_index, 0x0)
            soc = resp[0]
            charging = BATTERY_STATUS_1000.get(resp[2], f"unknown ({resp[2]})")
        print(f"State of charge: {soc}%")
        print(f"Charging status: {charging}")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
