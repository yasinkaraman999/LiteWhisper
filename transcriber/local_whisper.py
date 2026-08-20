import os
import shutil
import tempfile
import threading
import unicodedata
from pathlib import Path

from . import Transcriber

_loaded_models = {}  # size -> WhisperModel

# Same file list faster_whisper.utils.download_model() uses internally —
# kept in sync here so download_with_progress() populates the exact same
# cache layout _get_model() will later find and load without re-downloading.
_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

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


def download_with_progress(size, download_root, on_progress):
    """Downloads a model's files, calling on_progress(fraction) as bytes
    arrive.

    faster_whisper.utils.download_model() hardcodes tqdm_class=disabled_tqdm
    internally, so there's no way to observe progress by going through it —
    huggingface_hub.snapshot_download() is called directly instead, with the
    same repo_id/cache_dir/allow_patterns download_model() uses, so the
    cache ends up exactly where _get_model() expects it and never re-fetches.

    Also forces the classic HTTP download path: when the optional hf_xet
    package is installed, huggingface_hub silently switches to its own
    "xet" transfer protocol, which reports progress through a completely
    different mechanism and ignores tqdm_class entirely — the progress
    callback below would just never fire.
    """
    os.environ["HF_HUB_DISABLE_XET"] = "1"

    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm as tqdm_base

    lock = threading.Lock()
    totals = {}  # id(bar) -> (n, total), byte-unit bars only
    best = [0.0]

    def report():
        with lock:
            done = sum(n for n, _ in totals.values())
            total = sum(t for _, t in totals.values())
        if not total:
            return
        # Resuming a previously-interrupted download can make
        # huggingface_hub reconcile/"reconstruct" already-fetched chunks
        # through a second, separate progress pass that doesn't line up
        # with the first — clamped here so the reported percentage never
        # visibly jumps backwards, whatever is happening underneath.
        fraction = max(best[0], done / total)
        best[0] = fraction
        on_progress(fraction)

    class _ProgressTqdm(tqdm_base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.unit == "B":
                with lock:
                    totals[id(self)] = (self.n, self.total or 0)
                report()

        def update(self, n=1):
            result = super().update(n)
            if self.unit == "B":
                with lock:
                    totals[id(self)] = (self.n, self.total or 0)
                report()
            return result

        def close(self):
            with lock:
                totals.pop(id(self), None)
            report()
            super().close()

    snapshot_download(
        repo_id=_repo_id(size),
        cache_dir=str(download_root),
        allow_patterns=_ALLOW_PATTERNS,
        tqdm_class=_ProgressTqdm,
    )


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
            text = "".join(segment.text for segment in segments).strip()
            # See openrouter.py's transcribe() for why this is normalized
            # to a single canonical form here.
            return unicodedata.normalize("NFC", text)
