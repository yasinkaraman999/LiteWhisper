from datetime import datetime

import numpy as np
import sounddevice as sd

import audio_cleanup
import config
import voice_activity
import wav_io

SAMPLE_RATE = 16000
CHANNELS = 1


def _write_wav(path, audio):
    with open(path, "wb") as f:
        f.write(wav_io.encode_wav(audio, SAMPLE_RATE, CHANNELS))


def _save_debug_pair(raw, cleaned):
    config.DEBUG_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _write_wav(config.DEBUG_AUDIO_DIR / f"{stamp}_raw.wav", raw)
    _write_wav(config.DEBUG_AUDIO_DIR / f"{stamp}_cleaned.wav", cleaned)


SILENCE_FLOOR_DB = -60.0  # quieter than this reads as no signal on the meter


class AudioRecorder:
    def __init__(self):
        self._frames = []
        self._stream = None
        self._recording = False
        self._level = 0.0
        # Whether the most recently stopped recording contained actual
        # speech, per voice_activity.has_speech(). Callers check this before
        # sending audio off to a transcriber, so a recording that captured
        # nothing but silence never costs an API call.
        self.last_had_speech = True

    @property
    def is_recording(self):
        return self._recording

    @property
    def level(self):
        """Current input loudness, 0.0-1.0, for the recording overlay.

        A plain float read/write, which CPython makes atomic — the audio
        callback must never block on a lock.
        """
        return self._level

    def start(self):
        self._frames = []
        self._level = 0.0
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            device=config.load()["input_device"],
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        self._frames.append(indata.copy())

        # Loudness on a dB scale rather than raw RMS: speech sits around
        # -30 dB, so a linear meter would barely twitch while a dB one
        # swings across its whole range.
        block = indata.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
        db = 20.0 * np.log10(rms + 1e-9)
        self._level = float(np.clip((db - SILENCE_FLOOR_DB) / -SILENCE_FLOOR_DB, 0.0, 1.0))

    def cancel(self):
        """Stop recording and throw the audio away."""
        self._recording = False
        self._level = 0.0
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._frames = []

    def stop(self):
        self._recording = False
        self._level = 0.0
        self._stream.stop()
        self._stream.close()
        self._stream = None

        if not self._frames:
            self.last_had_speech = False
            return None

        cfg = config.load()
        raw = np.concatenate(self._frames, axis=0).flatten()

        self.last_had_speech = (
            voice_activity.has_speech(raw, SAMPLE_RATE) if cfg["vad_enabled"] else True
        )

        cleaned = audio_cleanup.clean(
            raw, SAMPLE_RATE, noise_reduction_strength=cfg["noise_reduction_strength"]
        )

        if cfg["debug_save_audio"]:
            _save_debug_pair(raw, cleaned)

        return wav_io.encode_wav(cleaned, SAMPLE_RATE, CHANNELS)
