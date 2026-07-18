"""py2app build script for MX Battery.

Build:  .venv/bin/python setup.py py2app
Output: dist/MXBattery.app
"""

from setuptools import setup

APP = ["launcher.py"]

OPTIONS = {
    "packages": ["mxbattery", "rumps"],
    "plist": {
        "CFBundleName": "MXBattery",
        "CFBundleDisplayName": "MX Battery",
        "CFBundleIdentifier": "com.pcolman.mxbattery",
        "CFBundleShortVersionString": "1.2.1",
        "CFBundleVersion": "1.2.1",
        # Menu-bar-only app: no Dock icon, no app switcher entry.
        "LSUIElement": True,
        "NSHumanReadableCopyright": "GPL-3.0. Local tool: no network access, read-only HID.",
    },
}

setup(
    app=APP,
    name="MXBattery",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
