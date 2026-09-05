from abc import ABC, abstractmethod


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribes WAV audio bytes and returns the recognized text."""


def get_transcriber(engine, model=None, local_model_size=None, api_key=None) -> Transcriber:
    """Builds a transcriber for the given engine/model, independent of which
    config keys they came from — batch dictation and live dictation each
    have their own engine/model settings and both call this."""
    if engine == "local":
        from . import local_whisper
        import config as config_module

        return local_whisper.LocalWhisperTranscriber(
            size=local_model_size,
            download_root=config_module.LOCAL_MODELS_DIR,
        )

    from .openrouter import OpenRouterTranscriber

    return OpenRouterTranscriber(api_key=api_key, model=model)
