from abc import ABC, abstractmethod


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribes WAV audio bytes and returns the recognized text."""


def get_transcriber(config) -> Transcriber:
    if config["engine"] == "local":
        from . import local_whisper
        import config as config_module

        return local_whisper.LocalWhisperTranscriber(
            size=config["local_model_size"],
            download_root=config_module.LOCAL_MODELS_DIR,
        )

    from .openrouter import OpenRouterTranscriber

    return OpenRouterTranscriber(
        api_key=config["openrouter_api_key"],
        model=config["model"],
    )
