"""
py2app build configuration for Claude Usage Monitor.
Run:  python setup.py py2app
"""
from setuptools import setup

APP = ["claude_usage_monitor.py"]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": None,          # swap in an .icns path when you have one
    "plist": {
        "CFBundleName": "ClaudeCosts",
        "CFBundleDisplayName": "ClaudeCosts",
        "CFBundleIdentifier": "com.rs1990.claudecosts",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,   # hides from Dock — menu bar only
        "NSHighResolutionCapable": True,
    },
    "packages": [
        "rumps",
        "AppKit",
        "Foundation",
        "objc",
        "matplotlib",
        "PIL",
    ],
    "excludes": ["tkinter"],
}

setup(
    name="ClaudeCosts",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
