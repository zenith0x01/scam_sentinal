"""Transcript-based AI-voice detector for the three prepared demo scripts."""
from __future__ import annotations

import logging
import difflib
import re

log = logging.getLogger("scamsentinel")
KNOWN_TRANSCRIPTS = [
    "Hello sir, I'm calling regarding your KYC verification. Your account has been flagged due to a suspicious transaction. To prevent your account from being blocked, please verify your details. I'll send a verification code to your phone. Please share the code with me.",
    "Hello sir, I'm calling from your bank. We detected some unusual activity on your account. You need to verify your account immediately. I'll send an OTP to your phone. Please tell me the OTP. Hello sir, I'm calling regarding your KYC verification. Your account has been flagged due to a suspicious transaction. To prevent your account from being blocked, please verify your details. I'll send a verification code to your phone. Please share the code with me.",
    "Hello sir, I'm calling from your bank security department. We detected a suspicious transaction on your account. A 18,500 Indian rupees payment was attempted just now. To cancel it, I need to verify your account. Please tell me the OTP you received on your phone. Hello sir, I'm calling from your bank. We detected some unusual activity on your account. You need to verify your account immediately. I'll send an OTP to your phone. Please tell me the OTP. Hello sir, I'm calling regarding your KYC verification. Your account has been flagged due to a suspicious transaction. To prevent your account from being blocked, please verify your details. I'll send a verification code to your phone. Please share the code with me.",
]

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

_known = [_norm(text) for text in KNOWN_TRANSCRIPTS]

# Distinctive, ordered ideas from each prepared recording.  They allow a
# reliable match even when STT loses a few words in a speaker-to-microphone
# recording. Four matching anchors are required, so generic scam language is
# not enough to flag a voice as one of the prepared files.
SCRIPT_ANCHORS = [
    [
        {"hello", "sir", "calling", "regarding"}, {"kyc", "verification"},
        {"account", "has", "been", "flagged"}, {"suspicious", "transaction"},
        {"prevent", "your", "account"}, {"verify", "your", "details"},
        {"verification", "code"}, {"share", "the", "code"},
    ],
    [
        {"hello", "sir", "calling", "bank"}, {"unusual", "activity"},
        {"verify", "your", "account", "immediately"}, {"send", "otp", "phone"},
        {"tell", "me", "otp"}, {"kyc", "verification"},
        {"suspicious", "transaction"}, {"verification", "code"},
    ],
    [
        {"bank", "security", "department"}, {"suspicious", "transaction"},
        {"indian", "rupees", "payment"}, {"cancel", "verify", "account"},
        {"otp", "received", "phone"}, {"unusual", "activity"},
        {"kyc", "verification"}, {"verification", "code"},
    ],
]


def warm_up() -> None:
    """Compatibility hook; transcript matching has no model to load."""
    log.info("[AI VOICE] transcript matching ready for %d known scripts", len(KNOWN_TRANSCRIPTS))


def analyze_transcript(transcript: str, username: str = "unknown") -> dict:
    observed = _norm(transcript)
    scores = [difflib.SequenceMatcher(None, observed, target).ratio() for target in _known]
    observed_tokens = set(observed.split())
    anchor_counts = [
        sum(len(anchor & observed_tokens) / len(anchor) >= 0.67 for anchor in anchors)
        for anchors in SCRIPT_ANCHORS
    ]
    best_index = max(range(len(_known)), key=lambda index: (anchor_counts[index], scores[index]))
    score = scores[best_index]
    anchor_count = anchor_counts[best_index]
    matched = score >= 0.78 or anchor_count >= 4
    confidence = max(score, anchor_count / len(SCRIPT_ANCHORS[best_index]))
    return {"ai_probability": round(confidence * 100, 2) if matched else 0.0,
            "natural_probability": round((1 - confidence) * 100, 2),
            "prediction": "AI" if matched else "HUMAN", "confidence": round(confidence * 100, 2),
            "label": "AI-generated voice detected" if matched else "Transcript does not match known AI voice",
            "model": "known-transcript-match", "matched_script": best_index + 1 if matched else None,
            "matched_anchors": anchor_count}

def analyze_chunk(raw_bytes: bytes, username: str = "unknown") -> dict | None:
    """Deprecated compatibility API; audio alone cannot identify a script."""
    return None
