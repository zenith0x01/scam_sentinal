"""Pretrained audio deepfake classifier used for the configured demo stream."""
from __future__ import annotations

import logging
import os
import threading

import numpy as np
from .stt import decode_audio_bytes

log = logging.getLogger("scamsentinel")
MODEL_ID = os.getenv("VOICE_DETECTOR_MODEL", "Vansh180/deepfake-audio-wav2vec2")
SAMPLE_RATE = 16000
MIN_DURATION_SECONDS = 0.8
MIN_RMS = 0.00035
_model = _processor = _torch = _load_error = None
_lock = threading.Lock()


def _load_model():
    global _model, _processor, _torch, _load_error
    if _model is not None:
        return _processor, _model, _torch
    if _load_error is not None:
        raise _load_error
    with _lock:
        if _model is not None:
            return _processor, _model, _torch
        try:
            import torch
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

            log.info("[AI VOICE] loading model=%s", MODEL_ID)
            _processor = AutoFeatureExtractor.from_pretrained(MODEL_ID)
            _model = AutoModelForAudioClassification.from_pretrained(MODEL_ID)
            _model.eval()
            _torch = torch
            log.info("[AI VOICE] model loaded labels=%s expected_sample_rate=%s", _model.config.id2label, getattr(_processor, "sampling_rate", "unknown"))
            return _processor, _model, _torch
        except Exception as exc:
            _load_error = exc
            log.exception("[AI VOICE] model load failed: %s", exc)
            raise


def warm_up() -> None:
    """Load the model before analysis begins."""
    _load_model()


def analyze_chunk(raw_bytes: bytes, username: str = "unknown") -> dict | None:
    """Decode and classify one WebM/MP3/PCM chunk; output scores from model logits."""
    user = username or "unknown"
    try:
        samples = decode_audio_bytes(raw_bytes)
    except Exception as exc:
        log.exception("[AI VOICE] user=%s audio decode failed bytes=%d: %s", user, len(raw_bytes), exc)
        return None

    if samples.size == 0:
        log.warning("[AI VOICE] user=%s invalid/empty decoded audio bytes=%d", user, len(raw_bytes))
        return None

    # decode_audio_bytes returns mono float samples resampled to 16 kHz.
    samples = np.nan_to_num(samples.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    duration = samples.size / SAMPLE_RATE
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    peak = float(np.max(np.abs(samples)))
    log.info("[AI VOICE] user=%s decoded duration=%.2fs sample_rate=%d channels=1 rms=%.6f peak=%.6f", user, duration, SAMPLE_RATE, rms, peak)

    if duration < MIN_DURATION_SECONDS:
        log.info("[AI VOICE] user=%s skipped: duration %.2fs below %.2fs minimum", user, duration, MIN_DURATION_SECONDS)
        return None
    if rms < MIN_RMS or peak < MIN_RMS * 2:
        log.info("[AI VOICE] user=%s skipped: low audio level rms=%.6f peak=%.6f thresholds=(%.6f, %.6f)", user, rms, peak, MIN_RMS, MIN_RMS * 2)
        return None

    try:
        processor, model, torch = _load_model()
        model_rate = int(getattr(processor, "sampling_rate", SAMPLE_RATE))
        if model_rate != SAMPLE_RATE:
            raise ValueError(f"decoder supplied {SAMPLE_RATE} Hz but model expects {model_rate} Hz")

        encoded = processor(samples, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True)
        model_input = encoded.get("input_values")
        if model_input is None or model_input.ndim != 2 or model_input.shape[0] != 1:
            raise ValueError(f"unexpected processor output; input_values shape={getattr(model_input, 'shape', None)}")
        shape = tuple(int(dim) for dim in model_input.shape)
        log.info("[AI VOICE] user=%s model_input_shape=%s sample_rate=%d duration=%.2fs", user, shape, model_rate, duration)
        log.info("[AI VOICE] user=%s inference started", user)
        with torch.inference_mode():
            logits = model(**encoded).logits[0]
            probabilities = torch.softmax(logits, dim=-1).cpu().numpy()

        labels = model.config.id2label
        label_scores = {str(labels.get(index, f"LABEL_{index}")).strip().lower(): float(probabilities[index]) for index in range(len(probabilities))}
        fake_labels = {label: score for label, score in label_scores.items() if any(token in label for token in ("fake", "synthetic", "spoof", "deepfake"))}
        real_labels = {label: score for label, score in label_scores.items() if any(token in label for token in ("real", "human", "bonafide", "bona fide", "natural", "genuine"))}
        if not fake_labels or not real_labels:
            raise ValueError(f"model labels cannot be mapped to AI/HUMAN: {labels}")

        ai_probability = float(np.clip(sum(fake_labels.values()), 0.0, 1.0))
        human_probability = float(np.clip(sum(real_labels.values()), 0.0, 1.0))
        is_ai = ai_probability >= human_probability
        prediction = "AI" if is_ai else "HUMAN"
        confidence = max(ai_probability, human_probability)
        log.info("[AI VOICE] user=%s prediction=%s confidence=%.2f%% ai_probability=%.2f%%", user, prediction, confidence * 100, ai_probability * 100)
        return {
            "ai_probability": round(ai_probability * 100, 2),
            "natural_probability": round(human_probability * 100, 2),
            "prediction": prediction,
            "confidence": round(confidence * 100, 2),
            "label": "AI-generated voice detected" if is_ai else "Human voice detected",
            "model": MODEL_ID,
            "sample_rate": SAMPLE_RATE,
            "duration_seconds": round(duration, 2),
            "model_input_shape": shape,
            "model_scores": label_scores,
        }
    except Exception as exc:
        log.exception("[AI VOICE] user=%s inference failed: %s", user, exc)
        return {"ai_probability": None, "natural_probability": None, "prediction": "ERROR", "confidence": None,
                "label": "Voice classifier failed", "model": MODEL_ID, "error": str(exc),
                "sample_rate": SAMPLE_RATE, "duration_seconds": round(duration, 2)}
