"""Live dictation: types what you say, word by word, into whatever field
currently has keyboard focus, as you speak.

Neither faster-whisper nor OpenRouter's transcription endpoint streams
tokens — both take a full audio blob and hand back a full transcript. Live
typing is simulated the way tools like Superwhisper do it: repeatedly
re-transcribe the growing buffer for the utterance currently being spoken,
and keep the on-screen text in sync with each new result by backspacing the
part that changed and typing the new tail (`_apply_diff`). Once a pause is
long enough, the utterance is "committed" — one last correction pass, then
it is never touched again — and a fresh utterance starts. That bounds any
mis-correction to the last few words rather than the whole dictation.
"""

import threading
import time

import numpy as np
import sounddevice as sd
from AppKit import NSWorkspace
from pynput.keyboard import Controller, Key

import config
import mic_lock
import voice_activity
import wav_io
from transcriber import get_transcriber

SAMPLE_RATE = 16000
CHANNELS = 1
OWNER = "live"

POLL_INTERVAL_S = 0.2
REFRESH_INTERVAL_S = 0.9
COMMIT_SILENCE_MS = 700
MIN_UTTERANCE_SAMPLES = int(SAMPLE_RATE * 0.3)


def _frontmost_app_id():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.bundleIdentifier() if app is not None else None


def _common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class LiveDictation:
    def __init__(self):
        self._stream = None
        self._frames = []
        self._frames_lock = threading.Lock()
        self._active = False
        self._stop_event = threading.Event()
        self._engine_thread = None
        self._kb = Controller()
        self._transcriber = None

        self._utterance_start_sample = 0
        self._speech_started = False
        self._last_typed = ""
        self._last_refresh_at = 0.0
        self._frontmost_app = None

    @property
    def is_active(self):
        return self._active

    def start(self):
        """Starts a live dictation session. Returns False if the microphone
        is already in use by something else."""
        if self._active:
            return True
        if not mic_lock.acquire(OWNER):
            return False

        cfg = config.load()
        self._transcriber = get_transcriber(
            engine=cfg["live_engine"],
            model=cfg["live_model"],
            local_model_size=cfg["live_local_model_size"],
            api_key=cfg["openrouter_api_key"],
        )

        self._frames = []
        self._utterance_start_sample = 0
        self._speech_started = False
        self._last_typed = ""
        self._last_refresh_at = 0.0
        self._frontmost_app = _frontmost_app_id()
        self._stop_event.clear()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            device=cfg["input_device"],
            callback=self._audio_callback,
        )
        self._stream.start()
        self._active = True

        self._engine_thread = threading.Thread(target=self._engine_loop, daemon=True)
        self._engine_thread.start()
        return True

    def stop(self):
        """Commits whatever is left of the current utterance, then ends the
        session."""
        if not self._active:
            return
        self._teardown_stream()
        self._flush_pending_utterance()
        mic_lock.release(OWNER)

    def cancel(self):
        """Esc: drops the not-yet-committed tail of the current utterance —
        already-committed text is left exactly as typed."""
        if not self._active:
            return
        self._teardown_stream()
        if self._last_typed:
            self._backspace(len(self._last_typed))
            self._last_typed = ""
        mic_lock.release(OWNER)

    # ------------------------------------------------------------- capture

    def _audio_callback(self, indata, frames, time_info, status):
        with self._frames_lock:
            self._frames.append(indata.copy())

    def _teardown_stream(self):
        self._active = False
        self._stop_event.set()
        if self._engine_thread is not None:
            self._engine_thread.join(timeout=2.0)
            self._engine_thread = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _snapshot(self):
        with self._frames_lock:
            frames = list(self._frames)
        if not frames:
            return None
        return np.concatenate(frames, axis=0).flatten()

    # --------------------------------------------------------------- loop

    def _engine_loop(self):
        while not self._stop_event.is_set():
            time.sleep(POLL_INTERVAL_S)
            try:
                self._tick()
            except Exception:
                # A single bad tick (e.g. a transient transcription error)
                # must never kill the live session — just try again next tick.
                continue

    def _tick(self):
        all_audio = self._snapshot()
        if all_audio is None:
            return
        utterance_audio = all_audio[self._utterance_start_sample :]
        if len(utterance_audio) < MIN_UTTERANCE_SAMPLES:
            return

        flags = voice_activity.frame_voiced_flags(utterance_audio, SAMPLE_RATE)
        if not flags:
            return

        if not self._speech_started:
            if not any(flags):
                # Still nothing but lead-in silence — keep sliding the
                # utterance start forward so it never gets transcribed.
                self._utterance_start_sample = len(all_audio)
                return
            self._speech_started = True
            self._frontmost_app = _frontmost_app_id()

        current_app = _frontmost_app_id()
        if current_app != self._frontmost_app:
            # Focus moved to a different app mid-utterance: keystrokes from
            # here on would land in the wrong place. Stop touching whatever
            # was already typed and start clean for wherever focus is now.
            self._reset_utterance(all_audio, current_app)
            return

        trailing_silence_frames = 0
        for voiced in reversed(flags):
            if voiced:
                break
            trailing_silence_frames += 1
        trailing_silence_ms = trailing_silence_frames * voice_activity.FRAME_MS

        now = time.monotonic()
        if now - self._last_refresh_at >= REFRESH_INTERVAL_S:
            self._last_refresh_at = now
            self._refresh(utterance_audio)

        if trailing_silence_ms >= COMMIT_SILENCE_MS:
            self._commit(all_audio, utterance_audio)

    def _reset_utterance(self, all_audio, frontmost_app):
        self._utterance_start_sample = len(all_audio)
        self._speech_started = False
        self._last_typed = ""
        self._frontmost_app = frontmost_app

    # ------------------------------------------------------------ typing

    def _refresh(self, utterance_audio):
        wav_bytes = wav_io.encode_wav(utterance_audio, SAMPLE_RATE, CHANNELS)
        try:
            text = self._transcriber.transcribe(wav_bytes).strip()
        except Exception:
            return
        if not text:
            return
        self._apply_diff(self._last_typed, text)
        self._last_typed = text

    def _commit(self, all_audio, utterance_audio):
        self._refresh(utterance_audio)
        if self._last_typed:
            self._kb.type(" ")
        self._reset_utterance(all_audio, self._frontmost_app)

    def _flush_pending_utterance(self):
        all_audio = self._snapshot()
        if all_audio is None or not self._speech_started:
            return
        utterance_audio = all_audio[self._utterance_start_sample :]
        if len(utterance_audio) == 0:
            return
        self._commit(all_audio, utterance_audio)

    def _apply_diff(self, old_text, new_text):
        prefix_len = _common_prefix_len(old_text, new_text)
        self._backspace(len(old_text) - prefix_len)
        suffix = new_text[prefix_len:]
        if suffix:
            self._kb.type(suffix)

    def _backspace(self, count):
        for _ in range(count):
            self._kb.press(Key.backspace)
            self._kb.release(Key.backspace)
