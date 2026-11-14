import asyncio
import io
import wave
from types import SimpleNamespace

import httpx
import pytest

from src.services.speech_service import SpeechUnavailable, transcribe_audio


def settings(**overrides):
    values = {"groq_api_key": "key", "speech_max_bytes": 20 * 1024 * 1024,
              "speech_timeout_seconds": 1}
    values.update(overrides)
    return SimpleNamespace(**values)


def wav_audio(seconds):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * (8000 * seconds))
    return output.getvalue()


def test_groq_transcription(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            assert url.endswith("/audio/transcriptions")
            assert kwargs["data"]["model"] == "whisper-large-v3-turbo"
            assert kwargs["files"]["file"][1] == wav_audio(19)
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"text": "Концерт в Уфе"})

    monkeypatch.setattr("src.services.speech_service.httpx.AsyncClient", Client)
    assert asyncio.run(transcribe_audio(wav_audio(19), "voice.wav", "audio/wav", settings())) == "Концерт в Уфе"


def test_validation_errors():
    with pytest.raises(ValueError, match="пуста"):
        asyncio.run(transcribe_audio(b"", "voice.webm", "audio/webm", settings()))
    with pytest.raises(ValueError, match="размер"):
        asyncio.run(transcribe_audio(b"12345", "voice.webm", "audio/webm", settings(speech_max_bytes=4)))
    with pytest.raises(ValueError, match="Формат"):
        asyncio.run(transcribe_audio(b"audio", "voice.bin", "application/octet-stream", settings()))


@pytest.mark.parametrize("seconds", [19, 20])
def test_audio_duration_at_or_below_limit_reaches_groq(monkeypatch, seconds):
    calls = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            calls.append(True)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"text": "ok"})

    monkeypatch.setattr("src.services.speech_service.httpx.AsyncClient", Client)
    assert asyncio.run(transcribe_audio(wav_audio(seconds), "voice.wav", "audio/wav", settings())) == "ok"
    assert calls == [True]


def test_audio_over_limit_is_rejected_before_groq(monkeypatch):
    def unexpected_client(**kwargs):
        pytest.fail("Groq must not be called for over-limit audio")

    monkeypatch.setattr("src.services.speech_service.httpx.AsyncClient", unexpected_client)
    with pytest.raises(ValueError, match="20 секунд"):
        asyncio.run(transcribe_audio(wav_audio(21), "voice.wav", "audio/wav", settings()))


def test_configured_duration_cannot_raise_the_global_20_second_limit(monkeypatch):
    monkeypatch.setattr("src.services.speech_service.httpx.AsyncClient", lambda **kwargs: pytest.fail("Groq must not run"))
    with pytest.raises(ValueError, match="20 секунд"):
        asyncio.run(transcribe_audio(wav_audio(21), "voice.wav", "audio/wav", settings(speech_max_duration_sec=60)))


def test_corrupted_audio_is_rejected():
    with pytest.raises(ValueError, match="повреждён"):
        asyncio.run(transcribe_audio(b"not audio", "voice.wav", "audio/wav", settings()))


def test_missing_key_and_provider_failure(monkeypatch):
    with pytest.raises(SpeechUnavailable, match="GROQ_API_KEY"):
        asyncio.run(transcribe_audio(wav_audio(1), "voice.wav", "audio/wav", settings(groq_api_key="")))

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): raise httpx.ConnectError("offline")

    monkeypatch.setattr("src.services.speech_service.httpx.AsyncClient", Client)
    with pytest.raises(SpeechUnavailable, match="связаться"):
        asyncio.run(transcribe_audio(wav_audio(1), "voice.wav", "audio/wav", settings()))
