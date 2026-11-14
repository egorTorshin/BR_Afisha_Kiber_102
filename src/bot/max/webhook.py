import asyncio
import html
import hmac
import json
import logging
from collections import deque
from hashlib import sha256
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from src.bot.max.adapter import MaxAdapter, MaxAudioError, max_keyboard, normalize_update
from src.bot.max.client import MaxClient, max_ssl_context
from src.bot.router import BotRouter
from src.services.speech_service import SpeechUnavailable
from src.utils.logger import get_logger, log_event


class MaxWebhookRunner:
    """Production MAX webhook transport; proxy MAX_WEBHOOK_URL to host port 8081."""

    def __init__(self, token: str, webhook_url: str, secret: str, router: BotRouter,
                 log_level: str = "INFO") -> None:
        parsed_url = urlsplit(webhook_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.path != "/webhook" or parsed_url.query or parsed_url.fragment:
            raise ValueError("MAX webhook URL must be an HTTPS URL ending in /webhook")
        if len(secret) < 16:
            raise ValueError("MAX_WEBHOOK_SECRET must contain at least 16 characters")
        self.token, self.webhook_url, self.secret = token, webhook_url, secret
        self.adapter = MaxAdapter(router)
        self.logger = get_logger("afisha_rb.max", log_level)
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()

    async def run_forever(self) -> None:
        timeout = httpx.Timeout(connect=10, read=30, write=15, pool=10)
        async with httpx.AsyncClient(timeout=timeout, verify=max_ssl_context(), trust_env=False) as http:
            client = MaxClient(self.token, http)
            app = FastAPI()

            @app.on_event("startup")
            async def subscribe() -> None:
                await client.subscribe_webhook(self.webhook_url, self.secret)
                log_event(self.logger, logging.INFO, "MAX webhook registered")

            @app.get("/healthz")
            async def healthz():
                return {"status": "ok"}

            @app.post("/webhook")
            async def webhook(request: Request, background_tasks: BackgroundTasks,
                              x_max_bot_api_secret: str | None = Header(default=None)):
                if not x_max_bot_api_secret or not hmac.compare_digest(x_max_bot_api_secret, self.secret):
                    raise HTTPException(status_code=401, detail="Invalid webhook secret")
                update = await request.json()
                key = self._event_key(update)
                if key in self._seen:
                    return {"status": "duplicate"}
                if key:
                    self._remember(key)
                background_tasks.add_task(self._handle, client, update)
                return {"status": "ok"}

            server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8081,
                                                   log_level="info"))
            await server.serve()

    @staticmethod
    def _event_key(update: dict) -> str:
        kind = update.get("update_type", "")
        event = update.get("callback") or update.get("message") or {}
        body = event.get("body") or {}
        identifier = event.get("callback_id") or body.get("mid")
        if identifier:
            return f"{kind}:{identifier}"
        timestamp = update.get("timestamp") or event.get("timestamp")
        if timestamp:
            return f"{kind}:{timestamp}"
        fingerprint = sha256(json.dumps(update, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return f"{kind}:{fingerprint}"

    def _remember(self, key: str) -> None:
        self._seen.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > 10000:
            self._seen.discard(self._seen_order.popleft())

    async def _handle(self, client: MaxClient, update: dict) -> None:
        audio_message = normalize_update(update)
        if audio_message and audio_message["kind"] == "audio":
            try:
                audio_result = await self.adapter.dispatch_audio(update, client._http)
                if audio_result is not None:
                    message, transcript, response = audio_result
                    await client.send_message(message["chat_id"], f"🎤 Распознано: {html.escape(transcript)}")
                    await client.send_message(message["chat_id"], response.text, max_keyboard(response.reply_markup))
            except (MaxAudioError, ValueError) as exc:
                await client.send_message(audio_message["chat_id"], f"❌ {exc}")
            except httpx.TimeoutException:
                await client.send_message(audio_message["chat_id"], "❌ Истекло время ожидания при загрузке или распознавании аудио.")
            except httpx.HTTPError:
                await client.send_message(audio_message["chat_id"], "❌ Не удалось загрузить аудио из MAX.")
            except SpeechUnavailable as exc:
                await client.send_message(audio_message["chat_id"], f"❌ {exc}")
            return
        normalized = await asyncio.to_thread(self.adapter.dispatch, update)
        if normalized is None:
            return
        incoming, response = normalized
        keyboard = max_keyboard(response.reply_markup)
        if incoming["kind"] == "callback":
            if incoming.get("callback_id"):
                await client.answer_callback(str(incoming["callback_id"]), response.text, keyboard)
                return
        await client.send_message(incoming["chat_id"], response.text, keyboard)
