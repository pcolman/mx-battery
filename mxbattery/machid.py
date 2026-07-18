"""Minimal macOS HID access via ctypes + IOKit. No third-party code.

Replaces the hidapi dependency with the ~180 lines this app actually
needs: enumerate HID devices by vendor, open one in SHARED mode (never
exclusive — the OS pointer stays untouched), send an output report, and
receive input reports with a timeout.

Threading model: open(), write(), read() and close() must all be called
from the same thread. read() pumps that thread's CFRunLoop, where the
input-report callback is scheduled. In this app that is the main thread
(rumps timer callbacks), briefly and re-entrantly — the same pattern
AppKit uses for modal loops.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import POINTER, byref, c_char_p, c_int32, c_long, c_ubyte, c_uint32, c_void_p

_iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

_KCFSTRING_ENCODING_UTF8 = 0x08000100
_KCFNUMBER_LONG_TYPE = 10
_KIOHID_REPORT_TYPE_OUTPUT = 1
_KIORETURN_SUCCESS = 0

# void (*IOHIDReportCallback)(void *context, IOReturn result, void *sender,
#                             IOHIDReportType type, uint32_t reportID,
#                             uint8_t *report, CFIndex reportLength)
_REPORT_CALLBACK = ctypes.CFUNCTYPE(
    None, c_void_p, c_int32, c_void_p, c_int32, c_uint32, POINTER(c_ubyte), c_long
)

for fn, res, args in [
    ("CFStringCreateWithCString", c_void_p, [c_void_p, c_char_p, c_uint32]),
    ("CFNumberGetValue", ctypes.c_bool, [c_void_p, c_int32, c_void_p]),
    ("CFSetGetCount", c_long, [c_void_p]),
    ("CFSetGetValues", None, [c_void_p, POINTER(c_void_p)]),
    ("CFRetain", c_void_p, [c_void_p]),
    ("CFRelease", None, [c_void_p]),
    ("CFRunLoopGetCurrent", c_void_p, []),
    ("CFRunLoopRunInMode", c_int32, [c_void_p, ctypes.c_double, ctypes.c_bool]),
    ("CFStringGetCString", ctypes.c_bool, [c_void_p, c_char_p, c_long, c_uint32]),
]:
    f = getattr(_cf, fn)
    f.restype, f.argtypes = res, args

for fn, res, args in [
    ("IOHIDManagerCreate", c_void_p, [c_void_p, c_uint32]),
    ("IOHIDManagerSetDeviceMatching", None, [c_void_p, c_void_p]),
    ("IOHIDManagerOpen", c_int32, [c_void_p, c_uint32]),
    ("IOHIDManagerCopyDevices", c_void_p, [c_void_p]),
    ("IOHIDDeviceGetProperty", c_void_p, [c_void_p, c_void_p]),
    ("IOHIDDeviceOpen", c_int32, [c_void_p, c_uint32]),
    ("IOHIDDeviceClose", c_int32, [c_void_p, c_uint32]),
    ("IOHIDDeviceSetReport", c_int32, [c_void_p, c_int32, c_long, POINTER(c_ubyte), c_long]),
    ("IOHIDDeviceRegisterInputReportCallback",
     None, [c_void_p, POINTER(c_ubyte), c_long, c_void_p, c_void_p]),
    ("IOHIDDeviceScheduleWithRunLoop", None, [c_void_p, c_void_p, c_void_p]),
    ("IOHIDDeviceUnscheduleFromRunLoop", None, [c_void_p, c_void_p, c_void_p]),
]:
    f = getattr(_iokit, fn)
    f.restype, f.argtypes = res, args

_RUNLOOP_DEFAULT_MODE = c_void_p.in_dll(_cf, "kCFRunLoopDefaultMode")


def _cfstr(s: str) -> c_void_p:
    return _cf.CFStringCreateWithCString(None, s.encode(), _KCFSTRING_ENCODING_UTF8)


_PROP_VENDOR = _cfstr("VendorID")
_PROP_PRODUCT_ID = _cfstr("ProductID")
_PROP_PRODUCT = _cfstr("Product")


def _prop_int(dev: c_void_p, key: c_void_p) -> int | None:
    ref = _iokit.IOHIDDeviceGetProperty(dev, key)
    if not ref:
        return None
    out = c_long()
    _cf.CFNumberGetValue(ref, _KCFNUMBER_LONG_TYPE, byref(out))
    return out.value


def _prop_str(dev: c_void_p, key: c_void_p) -> str:
    ref = _iokit.IOHIDDeviceGetProperty(dev, key)
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(256)
    if _cf.CFStringGetCString(ref, buf, 256, _KCFSTRING_ENCODING_UTF8):
        return buf.value.decode(errors="replace")
    return ""


_manager = None


def _get_manager() -> c_void_p:
    global _manager
    if _manager is None:
        _manager = _iokit.IOHIDManagerCreate(None, 0)
        _iokit.IOHIDManagerSetDeviceMatching(_manager, None)
        _iokit.IOHIDManagerOpen(_manager, 0)
    return _manager


def reset_manager() -> None:
    """Drop the cached IOHIDManager so the next call rebuilds it.

    The manager and the device refs it hands out go stale across a system
    sleep/wake, after which IOHIDDeviceOpen fails on every attempt. A
    long-running menu bar app must rebuild rather than trust the handle
    forever; callers invoke this and retry on failure.
    """
    global _manager
    if _manager is not None:
        _cf.CFRelease(_manager)
        _manager = None


def enumerate_devices(vendor_id: int) -> list[tuple[c_void_p, int, str]]:
    """Return [(retained device ref, product_id, product name)] for a vendor.

    Callers own the returned refs via HIDDevice (which releases on close).
    """
    dev_set = _iokit.IOHIDManagerCopyDevices(_get_manager())
    if not dev_set:
        return []
    try:
        n = _cf.CFSetGetCount(dev_set)
        refs = (c_void_p * n)()
        _cf.CFSetGetValues(dev_set, refs)
        found = []
        for ref in refs:
            if ref and _prop_int(ref, _PROP_VENDOR) == vendor_id:
                _cf.CFRetain(ref)
                found.append((c_void_p(ref), _prop_int(ref, _PROP_PRODUCT_ID) or 0,
                              _prop_str(ref, _PROP_PRODUCT)))
        return found
    finally:
        _cf.CFRelease(dev_set)


def release_ref(ref: c_void_p) -> None:
    _cf.CFRelease(ref)


class HIDDevice:
    """One opened HID device. Shared (non-exclusive) access only."""

    def __init__(self, ref: c_void_p):
        self._ref = ref
        self._buf = (c_ubyte * 64)()
        self._queue: list[bytes] = []
        # Keep the callback object referenced for the device's lifetime.
        self._cb = _REPORT_CALLBACK(self._on_report)
        self._open = False

    def _on_report(self, _ctx, result, _sender, _rtype, _report_id, report, length):
        # For numbered reports, IOKit's buffer already starts with the ID.
        if result == _KIORETURN_SUCCESS and length > 0:
            self._queue.append(bytes(report[:length]))

    def open(self) -> None:
        r = _iokit.IOHIDDeviceOpen(self._ref, 0)  # 0 = never seize
        if r != _KIORETURN_SUCCESS:
            raise OSError(f"IOHIDDeviceOpen failed ({r & 0xFFFFFFFF:#010x})")
        self._open = True
        _iokit.IOHIDDeviceRegisterInputReportCallback(
            self._ref, self._buf, len(self._buf), self._cb, None)
        _iokit.IOHIDDeviceScheduleWithRunLoop(
            self._ref, _cf.CFRunLoopGetCurrent(), _RUNLOOP_DEFAULT_MODE)

    def write(self, frame: bytes) -> None:
        """Send one output report; frame[0] is the (nonzero) report ID.

        Matches hidapi's macOS convention: the buffer passed to
        IOHIDDeviceSetReport includes the report-ID byte for numbered
        reports, and the ID is also passed as the reportID argument.
        """
        data = (c_ubyte * len(frame))(*frame)
        r = _iokit.IOHIDDeviceSetReport(
            self._ref, _KIOHID_REPORT_TYPE_OUTPUT, frame[0], data, len(data))
        if r != _KIORETURN_SUCCESS:
            raise OSError(f"IOHIDDeviceSetReport failed ({r & 0xFFFFFFFF:#010x})")

    def read(self, timeout_ms: int) -> bytes | None:
        """Return the next input report (report ID prefixed), or None."""
        deadline = time.monotonic() + timeout_ms / 1000
        while not self._queue:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            _cf.CFRunLoopRunInMode(_RUNLOOP_DEFAULT_MODE, min(remaining, 0.05), True)
        return self._queue.pop(0)

    def close(self) -> None:
        if self._ref is None:
            return
        if self._open:
            _iokit.IOHIDDeviceUnscheduleFromRunLoop(
                self._ref, _cf.CFRunLoopGetCurrent(), _RUNLOOP_DEFAULT_MODE)
            _iokit.IOHIDDeviceRegisterInputReportCallback(
                self._ref, self._buf, len(self._buf), None, None)
            _iokit.IOHIDDeviceClose(self._ref, 0)
            self._open = False
        _cf.CFRelease(self._ref)
        self._ref = None
