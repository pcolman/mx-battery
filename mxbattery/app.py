"""MX Battery menu bar app: shows MX Master battery percentage via HID++.

Fully local: no network access of any kind, no telemetry, no logging of
anything beyond stderr. Read-only toward the device (see hidpp.py).
"""

from __future__ import annotations

import datetime
import json
import os
import time

import rumps
from Foundation import NSBundle
from ServiceManagement import SMAppService

from . import hidpp

# SMAppService status constants (macOS 13+).
_SM_STATUS_ENABLED = 1

# Poll interval presets. Nothing below 1 minute on purpose: battery moves
# ~1% per 40+ min, so faster polling only adds BLE traffic to the mouse.
INTERVAL_CHOICES = [
    ("1 minute", 60),
    ("5 minutes", 300),
    ("15 minutes", 900),
    ("30 minutes", 1800),
]
DEFAULT_INTERVAL = 300

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/MX Battery")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

TITLE_DISCONNECTED = "\U0001F5B1 —"        # mouse glyph + em dash
TITLE_FMT = "\U0001F5B1 {pct}%"
TITLE_FMT_CHARGING = "\U0001F5B1⚡{pct}%"


def load_interval() -> int:
    try:
        with open(CONFIG_PATH) as f:
            value = json.load(f).get("poll_interval_seconds")
        if value in {secs for _, secs in INTERVAL_CHOICES}:
            return value
    except (OSError, ValueError):
        pass
    return DEFAULT_INTERVAL


def save_interval(seconds: int) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump({"poll_interval_seconds": seconds}, f)
    except OSError:
        pass  # settings still apply for this run


class MXBatteryApp(rumps.App):
    def __init__(self):
        super().__init__("MX Battery", title=TITLE_DISCONNECTED, quit_button=None)
        self.interval = load_interval()

        self.status_item = rumps.MenuItem("Status: starting…")
        self.status_item.set_callback(None)
        self.updated_item = rumps.MenuItem("Last checked: never")
        self.updated_item.set_callback(None)

        self.interval_menu = rumps.MenuItem("Polling Interval")
        self.interval_items = {}
        for label, secs in INTERVAL_CHOICES:
            item = rumps.MenuItem(label, callback=self.set_interval)
            item.state = 1 if secs == self.interval else 0
            self.interval_items[label] = (item, secs)
            self.interval_menu.add(item)

        # Login item toggle uses SMAppService, which only works for a real
        # .app bundle — hide it in `python -m mxbattery.app` dev runs.
        self.login_item = None
        if str(NSBundle.mainBundle().bundlePath()).endswith(".app"):
            self.login_item = rumps.MenuItem("Open at Login", callback=self.toggle_login)
            self.login_item.state = (
                1 if SMAppService.mainAppService().status() == _SM_STATUS_ENABLED else 0
            )

        self.menu = [
            self.status_item,
            self.updated_item,
            None,
            rumps.MenuItem("Refresh Now", callback=self.refresh_now),
            self.interval_menu,
            # NB: "is not None" matters — an empty rumps.MenuItem is falsy.
            *( [self.login_item] if self.login_item is not None else [] ),
            None,
            rumps.MenuItem("Quit MX Battery", callback=rumps.quit_application),
        ]
        self.timer = rumps.Timer(self.poll, self.interval)
        self.timer.start()
        # NSTimer's first fire is one full interval out; poll once at launch.
        self.poll(None)

    def set_interval(self, sender):
        _, secs = self.interval_items[sender.title]
        if secs == self.interval:
            return
        self.interval = secs
        for item, item_secs in self.interval_items.values():
            item.state = 1 if item_secs == secs else 0
        save_interval(secs)
        self.timer.stop()
        self.timer.interval = secs
        self.timer.start()
        self.poll(None)

    def toggle_login(self, sender):
        svc = SMAppService.mainAppService()
        if svc.status() == _SM_STATUS_ENABLED:
            ok, err = svc.unregisterAndReturnError_(None)
        else:
            ok, err = svc.registerAndReturnError_(None)
        if not ok:
            detail = str(err.localizedDescription()) if err else "unknown error"
            rumps.alert("MX Battery", f"Login item change failed: {detail}")
        sender.state = 1 if svc.status() == _SM_STATUS_ENABLED else 0

    def refresh_now(self, _sender):
        self.poll(None)

    def poll(self, _timer):
        now = datetime.datetime.now().strftime("%-I:%M %p")
        try:
            try:
                reading = hidpp.read_battery()
            except hidpp.DeviceNotResponding:
                # Transient: HID session not ready right at launch, or a
                # momentary collision with another HID client. One retry.
                time.sleep(1.0)
                reading = hidpp.read_battery()
        except hidpp.DeviceNotFound:
            self.title = TITLE_DISCONNECTED
            self.status_item.title = "Mouse not connected"
            self.updated_item.title = f"Last checked: {now}"
            return
        except hidpp.DeviceNotResponding as exc:
            self.title = TITLE_DISCONNECTED
            self.status_item.title = f"Mouse not responding ({exc})"
            self.updated_item.title = f"Last checked: {now}"
            return

        if reading.percentage is None:
            self.title = "\U0001F5B1⚡"
            self.status_item.title = f"{reading.device_name}: {reading.status_label}"
        else:
            fmt = TITLE_FMT_CHARGING if reading.is_charging else TITLE_FMT
            self.title = fmt.format(pct=reading.percentage)
            # 0x1000 devices (MX Master 3) only report 4 discrete levels
            # (100/50/20/5); say so instead of implying 1% precision.
            approx = " — approx., mouse reports 4 levels" if reading.coarse else ""
            self.status_item.title = (
                f"{reading.device_name}: {reading.percentage}% ({reading.status_label}){approx}"
            )
        self.updated_item.title = f"Last checked: {now}"


def main():
    MXBatteryApp().run()


if __name__ == "__main__":
    main()
