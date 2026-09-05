"""Transcribes an existing audio file end to end, independent of the length
or recording pipeline — a short voice memo and a one-hour recording go
through the exact same path.

Reading the file goes through AVFoundation (AVAudioFile), which the app
already depends on for permission handling, rather than a new native
dependency: it decodes any container/codec the OS itself supports, which in
practice covers wav/aiff/mp3/m4a/mp4/caf and Ogg-Opus (.opus) — verified
directly against real encoded files for each of those, not assumed.
"""

import math
from math import gcd

import numpy as np
from AVFoundation import AVAudioFile, AVAudioPCMBuffer
from Foundation import NSURL
from scipy.signal import resample_poly

import config
import wav_io
from transcriber import get_transcriber

TARGET_SAMPLE_RATE = 16000

# Cloud transcription APIs are built around one bounded request, not an
# hour-long upload — each chunk has to be small enough to encode, upload and
# process well within a normal request timeout regardless of how long the
# source file is. The local engine doesn't need this: faster-whisper already
# chunks long audio internally (see transcribe_file() below).
CLOUD_CHUNK_SECONDS = 8 * 60

AUDIO_EXTENSIONS = ["wav", "aiff", "aif", "mp3", "m4a", "mp4", "caf", "opus", "ogg"]


def _resample(mono, source_rate, target_rate):
    if int(source_rate) == target_rate:
        return mono
    g = gcd(int(source_rate), target_rate)
    up, down = target_rate // g, int(source_rate) // g
    return resample_poly(mono, up, down)


def decode_audio_file(path):
    """Returns (int16 mono PCM numpy array, sample_rate) for any audio file
    AVFoundation can open."""
    url = NSURL.fileURLWithPath_(str(path))
    audio_file, error = AVAudioFile.alloc().initForReading_error_(url, None)
    if audio_file is None:
        raise RuntimeError(f"Couldn't open audio file: {error}")

    fmt = audio_file.processingFormat()
    frame_count = int(audio_file.length())
    if frame_count <= 0:
        raise RuntimeError("Audio file is empty")

    channels = int(fmt.channelCount())
    source_rate = fmt.sampleRate()

    buf = AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, frame_count)
    ok, read_error = audio_file.readIntoBuffer_error_(buf, None)
    if not ok:
        raise RuntimeError(f"Couldn't read audio file: {read_error}")

    ch_data = buf.floatChannelData()
    frame_length = int(buf.frameLength())
    # AVAudioPCMBuffer hands back one raw float32 pointer per channel — each
    # one's as_buffer(frame_length) is frame_length *elements* (not bytes),
    # matching the pointer's own element type.
    channel_arrays = [
        np.frombuffer(ch_data[c].as_buffer(frame_length), dtype=np.float32)
        for c in range(channels)
    ]
    mono = channel_arrays[0] if channels == 1 else np.mean(channel_arrays, axis=0)

    resampled = _resample(mono, source_rate, TARGET_SAMPLE_RATE)
    pcm16 = (np.clip(resampled, -1.0, 1.0) * 32767.0).astype(np.int16)
    return pcm16, TARGET_SAMPLE_RATE


def transcribe_file(path, on_progress=None):
    """Transcribes an audio file using whichever engine/model is currently
    configured in Settings (the same batch-dictation engine, not the chat or
    live-dictation ones). `on_progress(fraction)` is called from this
    (background) thread as work completes; the caller is responsible for
    hopping back to the main thread before touching AppKit."""
    cfg = config.load()
    audio, sample_rate = decode_audio_file(path)
    if len(audio) == 0:
        raise RuntimeError("Audio file is empty")

    transcriber = get_transcriber(
        engine=cfg["engine"],
        model=cfg["model"],
        local_model_size=cfg["local_model_size"],
        api_key=cfg["openrouter_api_key"],
    )

    if cfg["engine"] == "local":
        # Handing faster-whisper the whole file in one call is both simpler
        # and more accurate than re-slicing it into fixed-length chunks
        # ourselves would be — its own internal VAD/window logic doesn't
        # risk cutting a sentence in half at an arbitrary chunk boundary.
        wav_bytes = wav_io.encode_wav(audio, sample_rate)
        if on_progress:
            on_progress(0.05)
        text = transcriber.transcribe(wav_bytes)
        if on_progress:
            on_progress(1.0)
        return text

    chunk_samples = CLOUD_CHUNK_SECONDS * sample_rate
    chunk_count = max(1, math.ceil(len(audio) / chunk_samples))
    parts = []
    for i in range(chunk_count):
        start = i * chunk_samples
        chunk = audio[start:start + chunk_samples]
        if len(chunk) == 0:
            continue
        wav_bytes = wav_io.encode_wav(chunk, sample_rate)
        text = transcriber.transcribe(wav_bytes)
        if text:
            parts.append(text)
        if on_progress:
            on_progress((i + 1) / chunk_count)
    return " ".join(parts).strip()
