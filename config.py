import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "lite-whisper"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOCAL_MODELS_DIR = CONFIG_DIR / "models"
DEBUG_AUDIO_DIR = CONFIG_DIR / "debug_audio"

LOCAL_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3-turbo"]

RECORDING_WINDOW_STYLES = ["classic", "mini", "none"]
# The overlay docks into a frame of slots around the screen: five along each
# edge, addressed as "<edge>-<slot>" with slot 0 nearest the origin corner.
DOCK_EDGES = ["top", "bottom", "left", "right"]
DOCK_SLOTS = 5

DEFAULTS = {
    "openrouter_api_key": "",
    "model": "openai/gpt-transcribe",
    "engine": "cloud",  # "cloud" or "local"
    "local_model_size": "small",
    "debug_save_audio": False,
    "noise_reduction_strength": 0.7,  # 0.0-1.0
    "vad_enabled": True,  # skip recordings that contain no detected speech
    "input_device": None,  # sounddevice input device index, None = system default
    "recording_window_style": "classic",  # "classic", "mini" or "none"
    "recording_window_always_show": False,
    "recording_window_dock": "bottom-2",  # "<edge>-<slot>", see DOCK_EDGES
}


def load():
    if not CONFIG_PATH.exists():
        return dict(DEFAULTS)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {**DEFAULTS, **data}


def save(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
