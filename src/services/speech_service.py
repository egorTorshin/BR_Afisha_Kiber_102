"""Speech transcription through Groq's hosted Whisper API."""
from pathlib import Path
import asyncio
import io
import json
import subprocess
import tempfile
import wave

import httpx


class SpeechUnavailable(RuntimeError):
    """The configured transcription provider could not return a transcript."""


MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 20
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
SUPPORTED_AUDIO_TYPES = {
    "audio/flac", "audio/mp3", "audio/mpeg", "audio/mp4", "audio/m4a",
    "audio/mpga", "audio/ogg", "audio/wav", "audio/x-wav", "audio/webm",
    "video/mp4", "application/ogg",
}
SUPPORTED_AUDIO_SUFFIXES = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}


def _validate_audio(audio: bytes, filename: str, content_type: str, settings) -> None:
    if not audio:
        raise ValueError("Аудиозапись пуста")
    limit = min(MAX_AUDIO_BYTES, max(1, int(settings.speech_max_bytes)))
    if len(audio) > limit:
        raise ValueError("Аудиозапись превышает допустимый размер 20 МБ")
    mime = content_type.partition(";")[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    if mime not in SUPPORTED_AUDIO_TYPES and suffix not in SUPPORTED_AUDIO_SUFFIXES:
        raise ValueError("Формат аудио не поддерживается")
    duration = _audio_duration_seconds(audio)
    limit = min(MAX_AUDIO_DURATION_SECONDS, max(1, int(getattr(settings, "speech_max_duration_sec", 20))))
    if duration > limit:
        raise ValueError(f"Длительность аудио превышает допустимые {limit} секунд")


def _audio_duration_seconds(audio: bytes) -> float:
    """Read duration from the encoded media itself, not request metadata."""
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(audio), "rb") as media:
                return media.getnframes() / media.getframerate()
        except (EOFError, wave.Error, ZeroDivisionError) as exc:
            raise ValueError("Не удалось прочитать аудиофайл: файл повреждён или формат не поддерживается") from exc
    try:
        with tempfile.TemporaryDirectory() as directory:
            media_path = Path(directory) / "recording"
            media_path.write_bytes(audio)
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration",
                 "-of", "json", str(media_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10,
            )
        metadata = json.loads(result.stdout)
        duration = metadata.get("format", {}).get("duration")
        if duration is None:
            durations = [stream.get("duration") for stream in metadata.get("streams", [])
                         if stream.get("duration") is not None]
            duration = max(durations, key=float) if durations else None
        if duration is None:
            raise ValueError
        return float(duration)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать аудиофайл: файл повреждён или формат не поддерживается") from exc


async def transcribe_audio(audio: bytes, filename: str, content_type: str, settings) -> str:
    """Call Groq asynchronously; audio is held in memory and never persisted."""
    # ffprobe is a subprocess and must not stall Telegram/MAX/API event loops.
    await asyncio.to_thread(_validate_audio, audio, filename, content_type, settings)
    api_key = settings.groq_api_key
    if not api_key:
        raise SpeechUnavailable("Распознавание речи недоступно: не настроен GROQ_API_KEY")

    mime = content_type.partition(";")[0].strip().lower() or "audio/webm"
    try:
        timeout = httpx.Timeout(float(getattr(settings, "speech_timeout_seconds", 30.0)))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                GROQ_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": "whisper-large-v3-turbo", "language": "ru", "response_format": "json"},
                files={"file": (Path(filename).name or "recording.webm", audio, mime)},
            )
            response.raise_for_status()
            text = response.json().get("text", "").strip()
            if not text:
                raise SpeechUnavailable("Провайдер не распознал речь в аудиозаписи")
            return text[:20_000]
    except SpeechUnavailable:
        raise
    except httpx.TimeoutException as exc:
        raise SpeechUnavailable("Превышено время ожидания сервиса распознавания") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            message = "Сервис распознавания отклонил API-ключ"
        elif status == 413:
            message = "Провайдер отклонил слишком большой аудиофайл"
        elif status == 429:
            message = "Сервис распознавания временно перегружен"
        else:
            message = "Ошибка сервиса распознавания речи"
        raise SpeechUnavailable(message) from exc
    except httpx.HTTPError as exc:
        raise SpeechUnavailable("Не удалось связаться с сервисом распознавания") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise SpeechUnavailable("Сервис распознавания вернул некорректный ответ") from exc
