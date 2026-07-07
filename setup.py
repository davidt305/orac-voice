"""py2app packaging for Orac Voice (alias mode).

Build the Dock-native .app that runs THIS repo in place:
    .venv/bin/python setup.py py2app -A

Alias mode (-A) references the source files where they live instead of
copying them, so the heavy local deps (onnxruntime, sounddevice, pyobjc,
WebKit) keep resolving from .venv. make-app.sh wraps this + ad-hoc signs.
"""
from setuptools import setup

APP = ["flow.py"]
OPTIONS = {
    "iconfile": "assets/AppIcon.icns",
    "plist": {
        "CFBundleName": "Orac Voice",
        "CFBundleDisplayName": "Orac Voice",
        "CFBundleIdentifier": "com.davidt.oracvoice",
        "CFBundleShortVersionString": "1.4",
        "CFBundleVersion": "1.4",
        "LSUIElement": False,
        "LSMinimumSystemVersion": "11.0",
        "NSMicrophoneUsageDescription":
            "Orac Voice needs the microphone to transcribe your dictation locally.",
    },
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
