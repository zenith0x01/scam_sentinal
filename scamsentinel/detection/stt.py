"""Local speech-to-text for browser-recorded WebM/Opus audio chunks."""

from __future__ import annotations

import io
import logging
import os
import subprocess
import wave

import numpy as np
from pydub import AudioSegment

log = logging.getLogger("scamsentinel")
_model = None
# base.en is still practical on a laptop but is substantially less error-prone
# than tiny.en on noisy/short browser chunks.  Users can select tiny.en/small.en
# with WHISPER_MODEL when latency or accuracy is the priority.
_model_name = os.environ.get("WHISPER_MODEL", "base.en")
_ffmpeg_available: bool | None = None


def _check_ffmpeg() -> bool:
    global _ffmpeg_available
    if _ffmpeg_available is not None:
        return _ffmpeg_available
    try:
        _ffmpeg_available = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=2).returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        _ffmpeg_available = False
    if not _ffmpeg_available:
        log.error("ffmpeg is unavailable. Install it and restart (Windows: winget install Gyan.FFmpeg).")
    return _ffmpeg_available


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info("Loading faster-whisper model %s (first run downloads it once)", _model_name)
        _model = WhisperModel(_model_name, device="cpu", compute_type="int8")
    return _model


def _decode_ffmpeg(raw_bytes: bytes) -> np.ndarray:
    if not _check_ffmpeg():
        return np.array([], dtype=np.float32)
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "wav", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
            input=raw_bytes, capture_output=True, timeout=20, check=False,
        )
        if result.returncode or not result.stdout:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[:400])
        with wave.open(io.BytesIO(result.stdout), "rb") as decoded:
            if decoded.getsampwidth() != 2:
                raise ValueError("ffmpeg did not return 16-bit PCM")
            samples = np.frombuffer(decoded.readframes(decoded.getnframes()), dtype=np.int16).astype(np.float32)
        return np.clip(samples / 32768.0, -1.0, 1.0)
    except Exception as exc:
        log.warning("ffmpeg could not decode audio chunk (%d bytes): %s", len(raw_bytes), exc)
        return np.array([], dtype=np.float32)


def decode_audio_bytes(raw_bytes: bytes) -> np.ndarray:
    """Decode common browser formats to mono, 16 kHz float samples."""
    if len(raw_bytes) < 16:
        return np.array([], dtype=np.float32)
    try:
        audio = AudioSegment.from_file(io.BytesIO(raw_bytes)).set_channels(1).set_frame_rate(16000)
        samples = np.array(audio.get_array_of_samples()).astype(np.float32)
        samples /= float(1 << (8 * audio.sample_width - 1))
        return np.clip(np.nan_to_num(samples), -1.0, 1.0)
    except Exception as exc:
        log.debug("pydub decode failed; trying ffmpeg: %s", exc)
        return _decode_ffmpeg(raw_bytes)


_HALLUCINATIONS = {"thank you.", "thanks for watching.", "thank you for watching.", "bye.", "silence.", "(silence)"}


def _transcribe_with_faster_whisper(samples: np.ndarray) -> str:
    segments, _info = _get_model().transcribe(
        samples, language="en", vad_filter=True,
        vad_parameters={
            "threshold": float(os.environ.get("WHISPER_VAD_THRESHOLD", "0.35")),
            "min_speech_duration_ms": 200,
            "min_silence_duration_ms": 350,
            "speech_pad_ms": 300,
        },
        # A wider beam is noticeably more stable for accents and telephone audio.
        beam_size=int(os.environ.get("WHISPER_BEAM_SIZE", "5")),
        best_of=2,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.75,
        compression_ratio_threshold=2.4,
    )
    kept = []
    for segment in segments:
        if getattr(segment, "no_speech_prob", 0.0) > 0.75 or getattr(segment, "avg_logprob", 0.0) < -1.2:
            continue
        if text := segment.text.strip():
            kept.append(text)
    text = " ".join(kept).strip()
    return "" if text.lower() in _HALLUCINATIONS else text


def transcribe_chunk(raw_bytes: bytes) -> str:
    """Return local faster-whisper text, or empty text for silence/errors.

    Call audio remains local: unlike SpeechRecognition's Google recognizer,
    there is no cloud fallback if the local model is unavailable.
    """
    samples = decode_audio_bytes(raw_bytes)
    if samples.size < 1600:
        return ""
    rms = float(np.sqrt(np.mean(np.square(samples))))
    # Remove DC offset and apply gentle gain for quiet laptop microphones. Do
    # not normalize silence: the RMS gate still prevents hallucinations.
    samples = samples - float(np.mean(samples))
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.01:
        # Cap gain at 4x so a genuinely quiet microphone is lifted without
        # turning background hiss into a full-scale signal.
        samples = np.clip(samples * min(4.0, 0.35 / peak), -1.0, 1.0)
        rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < float(os.environ.get("WHISPER_MIN_RMS", "0.0015")):
        return ""
    try:
        text = _transcribe_with_faster_whisper(samples)
        if text:
            log.info("Transcribed: %r", text)
        return text
    except Exception as exc:
        log.exception("faster-whisper failed; no transcript produced: %s", exc)
        return ""
