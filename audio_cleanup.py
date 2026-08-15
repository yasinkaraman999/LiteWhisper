import numpy as np
from scipy.signal import butter, filtfilt

FRAME_MS = 30
PADDING_MS = 150
SILENCE_RMS_RATIO = 0.08  # fraction of peak RMS below which a frame is "silence"
HIGH_PASS_HZ = 80


def _to_float(audio_int16):
    return audio_int16.astype(np.float32) / 32768.0


def _to_int16(audio_float):
    return np.clip(audio_float * 32768.0, -32768, 32767).astype(np.int16)


def _high_pass(audio, sample_rate):
    nyquist = sample_rate / 2
    b, a = butter(2, HIGH_PASS_HZ / nyquist, btype="high")
    padlen = 3 * max(len(a), len(b))
    if len(audio) <= padlen:
        return audio
    return filtfilt(b, a, audio).astype(np.float32)


def _reduce_noise(audio, sample_rate, strength):
    import noisereduce

    return noisereduce.reduce_noise(
        y=audio, sr=sample_rate, prop_decrease=strength
    ).astype(np.float32)


def _trim_silence(audio, sample_rate):
    frame_len = int(sample_rate * FRAME_MS / 1000)
    if frame_len == 0 or len(audio) < frame_len:
        return audio

    n_frames = len(audio) // frame_len
    frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames**2, axis=1))

    peak_rms = rms.max()
    if peak_rms == 0:
        return audio

    threshold = peak_rms * SILENCE_RMS_RATIO
    loud = np.where(rms > threshold)[0]
    if len(loud) == 0:
        return audio

    padding_frames = max(1, int(PADDING_MS / FRAME_MS))
    start_frame = max(0, loud[0] - padding_frames)
    end_frame = min(n_frames, loud[-1] + 1 + padding_frames)

    start = start_frame * frame_len
    end = min(len(audio), end_frame * frame_len)
    return audio[start:end]


def _normalize(audio):
    peak = np.abs(audio).max()
    if peak == 0:
        return audio
    return audio * (0.95 / peak)


def clean(audio_int16, sample_rate, noise_reduction_strength=0.7):
    """Cleans up recorded audio before it is sent to a transcriber.

    Applies a high-pass filter, spectral noise reduction, silence trimming
    and peak normalization. Falls back to the original untouched audio if
    any step fails or the result ends up empty, so a bad recording never
    breaks the transcription flow.

    noise_reduction_strength: 0.0 (off) to 1.0 (full noisereduce strength).
    """
    original = audio_int16
    try:
        audio = _to_float(audio_int16)
        audio = _high_pass(audio, sample_rate)

        if noise_reduction_strength <= 0:
            denoised = audio
        else:
            denoised = _reduce_noise(audio, sample_rate, noise_reduction_strength)
        if np.isnan(denoised).any() or np.isinf(denoised).any():
            # noisereduce can produce NaNs on pathological input (e.g. pure
            # digital silence, 0/0 in its spectral gate) — skip denoising
            # for this recording rather than propagate garbage samples.
            denoised = audio
        audio = denoised

        trimmed = _trim_silence(audio, sample_rate)
        if len(trimmed) > 0:
            audio = trimmed
        audio = _normalize(audio)
        return _to_int16(audio)
    except Exception:
        return original
