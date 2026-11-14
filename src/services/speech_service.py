"""Speech transcription with a hosted Groq endpoint and optional local fallback."""
import httpx
from functools import lru_cache


class SpeechUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _local_model():
    from faster_whisper import WhisperModel
    return WhisperModel("small", device="cpu", compute_type="int8")


def transcribe_audio(audio: bytes, filename: str, content_type: str, settings) -> str:
    if not audio:
        raise ValueError("Аудиозапись пуста")
    if len(audio) > settings.speech_max_bytes:
        raise ValueError("Аудиозапись превышает допустимый размер")
    if settings.groq_api_key:
        try:
            response = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                data={"model": "whisper-large-v3-turbo", "language": "ru", "response_format": "json"},
                files={"file": (filename, audio, content_type or "audio/webm")}, timeout=90,
            )
            response.raise_for_status()
            text = response.json().get("text", "").strip()
            if text:
                return text[:20_000]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    try:
        import tempfile
        from pathlib import Path
        suffix = Path(filename).suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix) as source:
            source.write(audio)
            source.flush()
            segments, _ = _local_model().transcribe(source.name, language="ru")
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if text:
                return text[:20_000]
    except Exception:
        pass
    raise SpeechUnavailable("Распознавание речи сейчас недоступно. Настройте GROQ_API_KEY или установите faster-whisper.")
