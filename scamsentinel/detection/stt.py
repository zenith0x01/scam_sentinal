"""Groq Whisper speech-to-text for browser-recorded WebM audio chunks."""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("scamsentinel")


def _transcribe_with_groq(raw_bytes: bytes) -> str:
    """Fast single-request STT via Groq Whisper Large V3 Turbo."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    response = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.webm", raw_bytes, "audio/webm")},
        data={
            "model": os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
            "language": "en",
            "prompt": "English phone call. Terms may include KYC, OTP, verification code, suspicious transaction, and bank account.",
            "response_format": "json",
            "temperature": "0",
        },
        timeout=(5, 30),
    )
    response.raise_for_status()
    text = str(response.json().get("text", "")).strip()
    if not text:
        raise RuntimeError("Groq returned an empty transcript")
    log.info("STT engine: Groq model=%s", os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo"))
    return text


def warm_up() -> None:
    """Confirm Groq STT configuration before anybody turns on a microphone."""
    if os.environ.get("GROQ_API_KEY", "").strip():
        log.info("STT remote engine ready: Groq Whisper")
    else:
        log.warning("STT is unavailable: set GROQ_API_KEY before starting the server")


def transcribe_chunk(raw_bytes: bytes) -> str:
    """Send one WebM chunk to Groq Whisper and return its transcript."""
    if len(raw_bytes) < 16:
        return ""
    if not os.environ.get("GROQ_API_KEY", "").strip():
        log.warning("STT skipped: GROQ_API_KEY is not configured")
        return ""
    try:
        text = _transcribe_with_groq(raw_bytes)
        if text:
            log.info("Transcribed: %r", text)
        return text
    except (requests.RequestException, ValueError, TypeError, RuntimeError) as exc:
        log.warning("Groq transcription failed: %s", exc)
        return ""
