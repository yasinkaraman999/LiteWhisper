"""Voice activity detection.

Distinguishes speech from silence/background noise so that a recording with
no actual speech in it never gets sent to a transcription API, and so that
non-speech padding can be trimmed more reliably than a fixed energy
threshold manages.
"""

import webrtcvad

FRAME_MS = 30  # webrtcvad only accepts 10, 20 or 30 ms frames
DEFAULT_AGGRESSIVENESS = 2  # 0 (least strict) - 3 (most strict)
DEFAULT_MIN_VOICED_MS = 300


def frame_voiced_flags(audio_int16, sample_rate, aggressiveness=DEFAULT_AGGRESSIVENESS):
    """One speech/silence decision per FRAME_MS-ms frame of 16-bit PCM audio.

    LiteWhisper always records 16kHz mono int16, one of the rates webrtcvad
    supports directly. Trailing audio shorter than one frame is dropped.
    """
    vad = webrtcvad.Vad(aggressiveness)
    frame_len = int(sample_rate * FRAME_MS / 1000)
    if frame_len == 0 or len(audio_int16) < frame_len:
        return []

    n_frames = len(audio_int16) // frame_len
    flags = []
    for i in range(n_frames):
        frame = audio_int16[i * frame_len : (i + 1) * frame_len]
        flags.append(vad.is_speech(frame.tobytes(), sample_rate))
    return flags


def has_speech(
    audio_int16,
    sample_rate,
    aggressiveness=DEFAULT_AGGRESSIVENESS,
    min_voiced_ms=DEFAULT_MIN_VOICED_MS,
):
    """Whether the recording contains a meaningful amount of speech.

    A single voiced frame isn't enough on its own — a click or noise burst
    can trip webrtcvad for one frame — so a minimum total voiced duration is
    required before this counts as "has speech".
    """
    try:
        flags = frame_voiced_flags(audio_int16, sample_rate, aggressiveness)
    except Exception:
        # webrtcvad failing must never block transcription outright — assume
        # there is speech and let the transcriber be the final judge.
        return True
    voiced_ms = sum(flags) * FRAME_MS
    return voiced_ms >= min_voiced_ms
