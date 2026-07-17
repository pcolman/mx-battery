"""Read-only HID++ 2.0 client for Logitech MX Master mice over Bluetooth.

Security posture: this module can only *query* the device. The single code
path that writes to the HID interface (`_request`) builds HID++ function-call
frames for the two read-only functions used here (ROOT.getFeature and the
battery get-status functions). No configuration, remapping, DFU/firmware, or
any other mutating HID++ feature is implemented, imported, or reachable.

Protocol per Logitech HID++ 2.0, cross-checked against the Solaar project's
documentation of the wire format (pwr-Solaar/Solaar). Reimplemented from
scratch; no Solaar code is used.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

import hid

VENDOR_LOGITECH = 0x046D
# Bluetooth product IDs. Add new MX Master variants here.
SUPPORTED_PIDS = {
    0xB023: "MX Master 3",
    0xB034: "MX Master 3S",
    0xB037: "MX Master 4",
}

LONG_REPORT_ID = 0x11          # HID++ long report, 20 bytes total
LONG_REPORT_LEN = 20
DEVICE_INDEX = 0xFF            # direct-connected (non-Unifying) device
SWID = 0x0A                    # 4-bit software id echoed back in responses

ROOT_FEATURE_INDEX = 0x00
FEATURE_UNIFIED_BATTERY = 0x1004   # newer devices (MX Master 4, 3S)
FEATURE_BATTERY_STATUS = 0x1000    # older devices (MX Master 3)

READ_TIMEOUT_MS = 1500

# Charging-state byte -> (label, is_charging). UNIFIED_BATTERY (0x1004).
_STATUS_1004 = {
    0: ("discharging", False),
    1: ("charging", True),
    2: ("charging (slow)", True),
    3: ("full", False),
    4: ("battery error", False),
}
# BATTERY_STATUS (0x1000).
_STATUS_1000 = {
    0: ("discharging", False),
    1: ("charging", True),
    2: ("charging (almost full)", True),
    3: ("full", False),
    4: ("charging (slow)", True),
    5: ("invalid battery", False),
    6: ("thermal error", False),
    7: ("charging error", False),
}

# macOS: hidapi seizes devices exclusively by default and the OS refuses to
# let anyone seize the system pointer. cython-hidapi does not expose the
# shared-mode switch, but the statically linked symbol is reachable.
ctypes.CDLL(hid.__file__).hid_darwin_set_open_exclusive(0)


class DeviceNotFound(Exception):
    """No supported mouse is connected (powered off, asleep, out of range)."""


class DeviceNotResponding(Exception):
    """Mouse is enumerated but did not answer the HID++ query in time."""


@dataclass
class BatteryReading:
    percentage: int | None   # None: level unavailable (0x1000 while charging)
    status_label: str        # "discharging", "charging", "full", ...
    is_charging: bool
    device_name: str


def _find_interface():
    """Locate the HID++ vendor-specific interface of a supported mouse.

    A Bluetooth mouse exposes several HID collections; HID++ lives on the
    Logitech vendor usage page 0xFF43 (older firmware: 0xFF00), long
    messages on usage 0x0202.
    """
    candidates = []
    for info in hid.enumerate(VENDOR_LOGITECH):
        if info["product_id"] not in SUPPORTED_PIDS:
            continue
        if info["usage_page"] in (0xFF43, 0xFF00):
            candidates.append(info)
    candidates.sort(key=lambda i: 0 if i["usage"] == 0x0202 else 1)
    if not candidates:
        raise DeviceNotFound()
    c = candidates[0]
    return c["path"], SUPPORTED_PIDS[c["product_id"]]


def _request(dev, feature_index: int, function: int, params: bytes = b"") -> bytes:
    """Send one read-only HID++ 2.0 function call, return 16 response bytes."""
    fnid_swid = ((function & 0x0F) << 4) | SWID
    frame = bytes([LONG_REPORT_ID, DEVICE_INDEX, feature_index, fnid_swid]) + params
    frame += bytes(LONG_REPORT_LEN - len(frame))
    dev.write(frame)

    deadline = time.monotonic() + READ_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        data = dev.read(LONG_REPORT_LEN, timeout_ms=remaining_ms)
        if not data:
            continue
        if data[0] != LONG_REPORT_ID or data[1] != DEVICE_INDEX:
            continue
        # HID++ 2.0 error frame: 0xFF where the feature index would be.
        if data[2] == 0xFF and data[3] == feature_index and data[4] == fnid_swid:
            raise DeviceNotResponding(f"HID++ error {data[5]:#04x}")
        if data[2] == feature_index and data[3] == fnid_swid:
            return bytes(data[4:20])
        # Unsolicited events / other software's traffic: ignore.
    raise DeviceNotResponding("query timed out")


def read_battery() -> BatteryReading:
    """Open the mouse, resolve its battery feature, read state of charge.

    Raises DeviceNotFound or DeviceNotResponding.
    """
    path, name = _find_interface()
    dev = hid.device()
    try:
        dev.open_path(path)
    except OSError as exc:
        raise DeviceNotResponding(f"cannot open HID interface: {exc}") from exc
    try:
        feat_index = None
        feat_id = None
        # Feature indices vary by firmware: resolve via ROOT.getFeature.
        # Prefer UNIFIED_BATTERY, fall back to legacy BATTERY_STATUS.
        for candidate in (FEATURE_UNIFIED_BATTERY, FEATURE_BATTERY_STATUS):
            resp = _request(dev, ROOT_FEATURE_INDEX, 0x0, candidate.to_bytes(2, "big"))
            if resp[0] != 0:
                feat_index, feat_id = resp[0], candidate
                break
        if feat_index is None:
            raise DeviceNotResponding("no supported battery feature on device")

        if feat_id == FEATURE_UNIFIED_BATTERY:
            # get_status (function 1): [state_of_charge, level_flags, status, ext_power]
            resp = _request(dev, feat_index, 0x1)
            pct, status_byte = resp[0], resp[2]
            label, charging = _STATUS_1004.get(status_byte, (f"unknown ({status_byte})", False))
        else:
            # get_battery_level_status (function 0): [level%, next_level, status]
            resp = _request(dev, feat_index, 0x0)
            pct, status_byte = resp[0], resp[2]
            label, charging = _STATUS_1000.get(status_byte, (f"unknown ({status_byte})", False))
            # 0x1000 reports level 0 (invalid) while on external power.
            if charging and pct == 0:
                pct = None

        if pct is not None:
            pct = max(0, min(100, pct))
        return BatteryReading(pct, label, charging, name)
    finally:
        dev.close()
