"""
Experimental AI/synthetic-voice heuristic.

IMPORTANT HONESTY NOTE: this is NOT a trained deep-learning deepfake
classifier (those require large labeled datasets and GPUs to run in real
time, which contradicts the "runs free on a normal laptop" requirement).
Instead this is a signal-processing heuristic that looks at acoustic
properties which tend to differ between natural human speech and
TTS/vocoder output on average:

  - pitch (f0) jitter: natural voices have small, irregular frame-to-frame
    pitch fluctuations from imperfect vocal-fold vibration; many TTS/vocoder
    systems produce an unnaturally smooth or unnaturally regular pitch
    contour.
  - amplitude shimmer: similar idea for loudness micro-variation.
  - spectral flatness / noise floor: natural recordings usually carry more
    broadband noise (breath, room, mic self-noise) than clean vocoder
    output.
  - harmonic energy concentration: vocoders often concentrate energy very
    tightly around harmonics with an unnaturally low noise floor between
    them.

None of these signals is individually reliable, and combined they are still
a heuristic, not a validated classifier -- hence "experimental" everywhere
in the UI. This is explicitly called out to the end user, exactly as the
product spec requires.
"""

from __future__ import annotations

import logging

import numpy as np
import librosa

from .stt import decode_audio_bytes

log = logging.getLogger("scamsentinel")


def _decode_to_mono_16k(raw_bytes: bytes) -> np.ndarray:
    # Use the exact same WebM/Opus decoder as STT. Previously this had a
    # second pydub-only decoder, so voice analysis failed on chunks that STT
    # could decode through ffmpeg.
    return decode_audio_bytes(raw_bytes)


def _safe(v, default=0.0):
    return float(v) if np.isfinite(v) else default


def analyze_chunk(raw_bytes: bytes) -> dict | None:
    """Returns {"ai_probability": 0-100, "natural_probability": 0-100,
    "label": str, "features": {...}} or None if the chunk had no usable
    voiced speech."""
    try:
        y = _decode_to_mono_16k(raw_bytes)
    except Exception as e:
        log.warning("voice-detector audio decode failed: %s", e)
        return None

    if y.size < 16000 * 0.8:  # need at least ~0.8s of audio
        return None
    if float(np.abs(y).mean()) < 0.0008:  # near silence
        return None

    sr = 16000

    try:
        f0, voiced_flag, _voiced_prob = librosa.pyin(
            y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"), sr=sr
        )
    except Exception:
        return None

    voiced = f0[voiced_flag] if voiced_flag is not None else np.array([])
    if voiced.size < 5:
        return None  # not enough voiced frames to say anything meaningful

    # --- pitch jitter: relative frame-to-frame change, robust to octave ---
    diffs = np.abs(np.diff(voiced)) / np.maximum(voiced[:-1], 1e-6)
    jitter = _safe(np.median(diffs), 0.0)  # natural speech: roughly 0.01-0.06+

    # --- amplitude shimmer on RMS envelope ---
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=256)[0]
    rms = rms[rms > 1e-6]
    shimmer = 0.0
    if rms.size > 5:
        rdiffs = np.abs(np.diff(rms)) / np.maximum(rms[:-1], 1e-6)
        shimmer = _safe(np.median(rdiffs), 0.0)

    # --- spectral flatness (noise floor proxy) ---
    flatness = _safe(np.mean(librosa.feature.spectral_flatness(y=y)), 0.0)

    # --- noise floor between harmonics: low-energy percentile of spectrum ---
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
    noise_floor = _safe(np.percentile(S, 10) / (np.percentile(S, 90) + 1e-6), 0.0)

    # Heuristic combination. Each term is roughly normalized to a "typical
    # natural speech" range from general acoustic-phonetics observations;
    # low jitter/shimmer/noise_floor push the score toward "AI-generated".
    jitter_score = np.clip(1.0 - (jitter / 0.035), 0.0, 1.0)       # low jitter -> synthetic-ish
    shimmer_score = np.clip(1.0 - (shimmer / 0.12), 0.0, 1.0)      # low shimmer -> synthetic-ish
    noise_floor_score = np.clip(1.0 - (noise_floor / 0.15), 0.0, 1.0)  # very clean -> synthetic-ish
    flatness_score = np.clip((0.35 - flatness) / 0.35, 0.0, 1.0)   # low flatness (tonal/clean) -> synthetic-ish

    combined = (
        0.35 * jitter_score
        + 0.20 * shimmer_score
        + 0.25 * noise_floor_score
        + 0.20 * flatness_score
    )
    ai_probability = int(round(np.clip(combined, 0.0, 1.0) * 100))
    natural_probability = 100 - ai_probability

    if ai_probability >= 65:
        label = "Potential synthetic/AI-generated voice"
    elif ai_probability >= 40:
        label = "Uncertain - some unusual acoustic regularity"
    else:
        label = "Likely natural human voice"

    return {
        "ai_probability": ai_probability,
        "natural_probability": natural_probability,
        "label": label,
        "features": {
            "pitch_jitter": round(jitter, 4),
            "amplitude_shimmer": round(shimmer, 4),
            "spectral_flatness": round(flatness, 4),
            "noise_floor_ratio": round(noise_floor, 4),
        },
    }
