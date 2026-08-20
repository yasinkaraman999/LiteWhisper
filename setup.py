from setuptools import setup

APP = ["main.py"]
ICON_FILE = "logos/AppIcon.icns"
DATA_FILES = [
    (
        "logos",
        [
            "logos/lite-whisper-no-bg-colored.png",
            "logos/lite-whisper-no-bg-white.png",
            "logos/lite-whisper-transparent-colored-logo.png",
            "logos/lite-whisper-white-bg-logo.png",
        ],
    )
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": ICON_FILE,
    "plist": {
        "CFBundleName": "LiteWhisper",
        "CFBundleDisplayName": "LiteWhisper",
        "CFBundleIdentifier": "com.yasinkaraman.litewhisper",
        "CFBundleShortVersionString": "0.8.0",
        "CFBundleVersion": "0.8.0",
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
