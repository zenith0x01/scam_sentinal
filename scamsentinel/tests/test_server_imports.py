from detection import scam_detector, stt, voice_detector


def test_detection_modules_import():
    assert hasattr(scam_detector, "analyze_transcript")
    assert hasattr(stt, "transcribe_chunk")
    assert hasattr(voice_detector, "analyze_chunk")


def test_transcribe_chunk_returns_empty_without_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
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
    assert (result.risk_level, result.confidence, result.backend) == ("HIGH", 91, "gemini:gemini-3.5-flash-lite")


def test_heuristic_is_used_without_a_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = scam_detector.analyze_transcript(["Caller: give me your OTP immediately or your bank account is blocked"])
    assert result.backend == "heuristic"
    assert result.risk_level == "HIGH"


def test_groq_transcription_posts_a_webm_file(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "Hello sir, I am calling regarding your KYC verification."}

    captured = {}
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(stt.requests, "post", lambda *args, **kwargs: (captured.update(kwargs), Response())[1])

    assert "KYC verification" in stt.transcribe_chunk(b"webm-test-bytes-123")
    assert captured["files"]["file"][0] == "audio.webm"
    assert captured["data"]["model"] == "whisper-large-v3-turbo"


def test_partial_first_known_script_is_detected():
    partial_transcript = (
        "Hello sir, I am calling regarding your KYC verification. "
        "Your account has been faxed due to suspicious transaction. "
        "When your account is being blocked, please verify your details. "
        "I will send a verification code to your phone."
    )
    result = voice_detector.analyze_transcript(partial_transcript)
    assert result["prediction"] == "AI"
    assert result["matched_script"] == 1
    assert result["matched_anchors"] >= 4
