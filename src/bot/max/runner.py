import asyncio
import html
import logging

import httpx

from src.bot.max.adapter import MaxAdapter, MaxAudioError, max_keyboard, normalize_update
from src.bot.max.client import MaxClient, max_ssl_context
from src.bot.router import BotRouter
from src.services.speech_service import SpeechUnavailable
from src.utils.logger import get_logger, log_event


class MaxPollingRunner:
    """MAX long-polling transport. MAX documents polling for development only."""

    def __init__(self, token: str, router: BotRouter, log_level: str = "INFO") -> None:
        self.token = token
        self.adapter = MaxAdapter(router)
        self.logger = get_logger("afisha_rb.max", log_level)
        self.marker: int | None = None

    async def run_forever(self) -> None:
        timeout = httpx.Timeout(connect=10, read=100, write=15, pool=10)
        async with httpx.AsyncClient(timeout=timeout, verify=max_ssl_context(), trust_env=False) as http:
            client = MaxClient(self.token, http)
            while True:
                try:
                    result = await client.get_updates(self.marker)
                    updates = result.get("updates", [])
                    for update in updates:
                        await self._handle(client, update)
                    if result.get("marker") is not None:
                        self.marker = int(result["marker"])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_event(self.logger, logging.ERROR, "MAX polling failed", error_type=type(exc).__name__)
                    await asyncio.sleep(3)

    async def _handle(self, client: MaxClient, update: dict) -> None:
        audio_message = normalize_update(update)
        if audio_message and audio_message["kind"] == "audio":
            try:
                audio_result = await self.adapter.dispatch_audio(update, client._http)
                if audio_result is None:
                    return
                _, transcript, response = audio_result
                await client.send_message(audio_message["chat_id"], f"🎤 Распознано: {html.escape(transcript)}")
                await client.send_message(audio_message["chat_id"], response.text, max_keyboard(response.reply_markup))
            except (MaxAudioError, ValueError) as exc:
                await client.send_message(audio_message["chat_id"], f"❌ {exc}")
            except httpx.TimeoutException:
                await client.send_message(audio_message["chat_id"], "❌ Истекло время ожидания при загрузке или распознавании аудио.")
            except httpx.HTTPError:
                await client.send_message(audio_message["chat_id"], "❌ Не удалось загрузить аудио из MAX.")
            except SpeechUnavailable as exc:
                await client.send_message(audio_message["chat_id"], f"❌ {exc}")
            return
        normalized = self.adapter.dispatch(update)
        if normalized is None:
            return
        incoming, response = normalized
        keyboard = max_keyboard(response.reply_markup)
        try:
            if incoming["kind"] == "callback":
                callback_id = incoming.get("callback_id")
                if callback_id:
                    await client.answer_callback(str(callback_id), response.text, keyboard)
                else:
                    await client.send_message(incoming["chat_id"], response.text, keyboard)
            else:
                await client.send_message(incoming["chat_id"], response.text, keyboard)
        except Exception as exc:
            log_event(self.logger, logging.ERROR, "MAX response failed", error_type=type(exc).__name__)
