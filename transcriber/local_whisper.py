import shutil
import tempfile
from pathlib import Path

from . import Transcriber

_loaded_models = {}  # size -> WhisperModel

# Approximate figures for the settings UI (actual disk usage is measured
# directly once a model is downloaded via downloaded_size_bytes()).
MODEL_INFO = {
    "tiny": {"disk": "~75 MB", "ram": "~1 GB"},
    "base": {"disk": "~145 MB", "ram": "~1 GB"},
    "small": {"disk": "~485 MB", "ram": "~2 GB"},
    "medium": {"disk": "~1.5 GB", "ram": "~5 GB"},
    "large-v3-turbo": {"disk": "~1.6 GB", "ram": "~6 GB"},
}


def _repo_id(size):
    from faster_whisper.utils import _MODELS

    return _MODELS[size]


def _model_dir(size, download_root):
    return Path(download_root) / f"models--{_repo_id(size).replace('/', '--')}"


def _get_model(size, download_root):
    if size not in _loaded_models:
        from faster_whisper import WhisperModel

        _loaded_models[size] = WhisperModel(
            size, device="auto", compute_type="default", download_root=str(download_root)
        )
    return _loaded_models[size]


def is_downloaded(size, download_root):
    d = _model_dir(size, download_root)
    return d.exists() and any(d.rglob("*.bin"))


def downloaded_size_bytes(size, download_root):
    d = _model_dir(size, download_root)
    if not d.exists():
        return 0
    # HF's cache layout stores real file data under blobs/, with snapshots/
    # containing symlinks to those blobs — skipping symlinks avoids double
    # counting the same bytes twice.
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink())


def delete_model(size, download_root):
    d = _model_dir(size, download_root)
    if d.exists():
        shutil.rmtree(d)
    _loaded_models.pop(size, None)


def format_size(num_bytes):
    if num_bytes < 1024**2:
        return f"{num_bytes / 1024:.0f} KB"
    if num_bytes < 1024**3:
        return f"{num_bytes / 1024**2:.0f} MB"
    return f"{num_bytes / 1024**3:.2f} GB"


class LocalWhisperTranscriber(Transcriber):
    def __init__(self, size, download_root):
        self.size = size
        self.download_root = download_root

    def transcribe(self, wav_bytes: bytes) -> str:
        model = _get_model(self.size, self.download_root)

        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            segments, _info = model.transcribe(tmp.name)
            return "".join(segment.text for segment in segments).strip()
