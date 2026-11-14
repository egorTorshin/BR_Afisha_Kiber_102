import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from src.bot.router import BotResponse
from src.bot.telegram_runner import TelegramPollingRunner
from src.config import Settings
from src.services.speech_service import SpeechUnavailable


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class Download:
    def __init__(self, data=b"audio"):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self.data


class Client:
    def __init__(self, file_size=5):
        self.messages = []
        self.file_size = file_size

    async def get(self, url, params=None):
        return Response({"ok": True, "result": {"file_path": "voice/file.oga", "file_size": self.file_size}})

    def stream(self, method, url):
        return Download()

    async def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.messages.append((method, json))
        return Response({"ok": True, "result": {"message_id": len(self.messages)}})


class Router:
    import_service = SimpleNamespace(max_import_bytes=100)

    def __init__(self):
        self.calls = []

    def handle_message(self, user_id, text):
        self.calls.append((user_id, text))
        return BotResponse(text="Ответ")


def update(duration=4, file_size=5):
    return {"update_id": 1, "message": {
        "message_id": 10, "chat": {"id": 77}, "from": {"id": 88},
        "voice": {"file_id": "file-id", "duration": duration, "file_size": file_size},
    }}


def run_update(runner, client, payload):
    asyncio.run(runner._handle_update(client, payload))


def test_voice_transcribes_routes_same_user_and_shows_transcript():
    router = Router()
    runner = TelegramPollingRunner("token", router, settings=Settings())
    client = Client()
    with patch("src.bot.telegram_runner.transcribe_audio", return_value="Концерт в Уфе") as transcribe:
        run_update(runner, client, update())
    assert transcribe.call_args.args[:3] == (b"audio", "file.oga", "audio/ogg")
    assert router.calls == [("88", "Концерт в Уфе")]
    texts = [payload["text"] for method, payload in client.messages if method == "sendMessage"]
    assert "🎤 Распознано: Концерт в Уфе" in texts
    assert "Ответ" in texts


def test_voice_duration_limit_rejects_before_telegram_download():
    router = Router()
    runner = TelegramPollingRunner("token", router, settings=Settings(speech_max_duration_sec=3))
    client = Client()
    with patch.object(client, "get", side_effect=AssertionError("must not download")):
        run_update(runner, client, update(duration=4))
    assert "Максимум — 3 секунд" in client.messages[0][1]["text"]
    assert not router.calls


def test_voice_size_limit_and_transcription_failure_are_reported():
    runner = TelegramPollingRunner("token", Router(), settings=Settings(speech_max_bytes=4))
    client = Client(file_size=5)
    run_update(runner, client, update())
    assert "20 МБ" in client.messages[0][1]["text"]

    client = Client()
    runner = TelegramPollingRunner("token", Router(), settings=Settings())
    with patch("src.bot.telegram_runner.transcribe_audio", side_effect=SpeechUnavailable("Сервис распознавания временно перегружен")):
        run_update(runner, client, update())
    assert "перегружен" in client.messages[0][1]["text"]


def test_voice_transcription_timeout_is_reported():
    runner = TelegramPollingRunner("token", Router(), settings=Settings())
    client = Client()
    with patch("src.bot.telegram_runner.transcribe_audio", side_effect=httpx.ReadTimeout("timeout")):
        run_update(runner, client, update())
    assert "Превышено время ожидания" in client.messages[0][1]["text"]
