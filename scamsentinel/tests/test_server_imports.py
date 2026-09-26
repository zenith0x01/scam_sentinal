import numpy as np

from detection import scam_detector, stt, voice_detector


def test_detection_modules_import():
    assert hasattr(scam_detector, "analyze_transcript")
    assert hasattr(stt, "transcribe_chunk")
    assert hasattr(voice_detector, "analyze_chunk")


def test_transcribe_chunk_returns_empty_when_local_stt_fails(monkeypatch):
    monkeypatch.setattr(stt, "decode_audio_bytes", lambda raw: np.ones(16000, dtype=np.float32) * 0.2)
    monkeypatch.setattr(stt, "_transcribe_with_faster_whisper", lambda samples: (_ for _ in ()).throw(RuntimeError("fast fail")))
    assert stt.transcribe_chunk(b"sample") == ""


def test_gemini_result_is_used_when_it_returns_valid_json(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"risk_level":"HIGH","confidence":91,"reasons":["Asked for an OTP"]}'}]}}]}

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(scam_detector.requests, "post", lambda *args, **kwargs: Response())
    result = scam_detector.analyze_transcript(["Caller: give me your OTP"])
    assert (result.risk_level, result.confidence, result.backend) == ("HIGH", 91, "gemini:gemini-2.5-flash-lite")


def test_heuristic_is_used_without_a_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = scam_detector.analyze_transcript(["Caller: give me your OTP immediately or your bank account is blocked"])
    assert result.backend == "heuristic"
    assert result.risk_level == "HIGH"
