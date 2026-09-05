import os

from setuptools import setup

APP = ["main.py"]
ICON_FILE = "logos/AppIcon.icns"


def _chat_transcript_data_files():
    """(dest_dir, [files]) entries for every directory under chat_transcript/
    (index.html/app.js/style.css plus the vendored markdown-it/highlight.js/
    KaTeX bundles and KaTeX's font files) — walked instead of listed by hand
    so a new vendored file doesn't silently go missing from the packaged
    app the way a hand-maintained list could."""
    entries = []
    for root, _dirs, files in os.walk("chat_transcript"):
        if not files:
            continue
        entries.append((root, [os.path.join(root, f) for f in files]))
    return entries


DATA_FILES = [
    (
        "logos",
        [
            "logos/lite-whisper-no-bg-colored.png",
            "logos/lite-whisper-no-bg-white.png",
            "logos/lite-whisper-transparent-colored-logo.png",
            "logos/lite-whisper-white-bg-logo.png",
        ],
    ),
    *_chat_transcript_data_files(),
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": ICON_FILE,
    "plist": {
        "CFBundleName": "LiteWhisper",
        "CFBundleDisplayName": "LiteWhisper",
        "CFBundleIdentifier": "com.yasinkaraman.litewhisper",
        "CFBundleShortVersionString": "0.10.0",
        "CFBundleVersion": "0.10.0",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "LiteWhisper needs microphone access to turn what you say into text.",
        "NSAppleEventsUsageDescription": "LiteWhisper needs to control other apps so it can paste the transcribed text.",
    },
    "packages": [
        "rumps",
        "pynput",
        "sounddevice",
        "numpy",
        "requests",
        "_sounddevice_data",
        "AVFoundation",
        "WebKit",
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "huggingface_hub",
        "av",
        "onnxruntime",
        "tqdm",
        "noisereduce",
        "scipy",
        "webrtcvad",
    ],
    # PIL is pulled in transitively (unused by us) and ships a liblzma.dylib
    # that fails codesign's strict validation — excluding it avoids that
    # entirely and trims the bundle since we never import PIL at runtime.
    # matplotlib is noisereduce's optional plotting dependency, never
    # imported by our code path — excluding it keeps the bundle smaller.
    "excludes": ["PIL", "matplotlib"],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
