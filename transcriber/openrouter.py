import base64
import unicodedata

import requests

from . import Transcriber

API_URL = "https://openrouter.ai/api/v1/audio/transcriptions"


class OpenRouterTranscriber(Transcriber):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.last_usage = None

    def transcribe(self, wav_bytes: bytes) -> str:
        if not self.api_key:
            raise ValueError("OpenRouter API key not set")

        audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "input_audio": {"data": audio_b64, "format": "wav"},
            },
            timeout=60,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            detail = response.text[:200]
            raise RuntimeError(f"OpenRouter returned an error ({response.status_code}): {detail}") from e

        body = response.json()
        if "text" not in body:
            raise RuntimeError(f"Unexpected OpenRouter response: {body}")
        self.last_usage = body.get("usage")
        # Normalized to a single canonical form (precomposed) so repeated
        # transcriptions of the same audio — as live dictation does — are
        # never one decomposed-vs-composed accent apart, which would
        # otherwise show up as bogus diffs around Turkish and other
        # diacritic letters.
        return unicodedata.normalize("NFC", body["text"])
