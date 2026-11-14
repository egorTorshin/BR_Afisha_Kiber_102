from types import SimpleNamespace

import httpx
import pytest

from src.services.speech_service import SpeechUnavailable, transcribe_audio


def test_groq_transcription(monkeypatch):
    monkeypatch.setattr("src.services.speech_service.httpx.post", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"text": "Концерт в Уфе"}))
    settings = SimpleNamespace(groq_api_key="key", speech_max_bytes=1024)
    assert transcribe_audio(b"audio", "voice.webm", "audio/webm", settings) == "Концерт в Уфе"


def test_rejects_oversized_audio():
    with pytest.raises(ValueError, match="размер"):
        transcribe_audio(b"12345", "voice.webm", "audio/webm",
                         SimpleNamespace(groq_api_key="", speech_max_bytes=4))


def test_unavailable_without_groq_or_local_model(monkeypatch):
    monkeypatch.setattr("src.services.speech_service.httpx.post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)
    with pytest.raises(SpeechUnavailable):
        transcribe_audio(b"audio", "voice.webm", "audio/webm",
                         SimpleNamespace(groq_api_key="key", speech_max_bytes=1024))
