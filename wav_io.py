"""Shared WAV encoding for in-memory int16 PCM audio."""

import io
import wave


def encode_wav(audio_int16, sample_rate, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()
