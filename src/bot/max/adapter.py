from __future__ import annotations

import copy
from urllib.parse import urlsplit
from typing import Any

import httpx

from src.bot.router import BotResponse, BotRouter
from src.services.speech_service import MAX_AUDIO_BYTES, MAX_AUDIO_DURATION_SECONDS, transcribe_audio


class MaxAudioError(ValueError):
    pass


def _audio_url_allowed(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                # MAX's official media upload examples return these CDN hosts.
                # Keep this exact to prevent arbitrary subdomains becoming SSRF targets.
                and host in {"omu.okcdn.ru", "fu.oneme.ru", "cdn.max.ru"})
    except ValueError:
        return False


def max_keyboard(reply_markup: dict[str, Any] | None) -> list[list[dict[str, str]]] | None:
    """Translate the router's Telegram-shaped inline keyboard to MAX buttons."""
    markup = reply_markup or {}
    rows = markup.get("inline_keyboard") or markup.get("keyboard")
    if not rows:
        return None
    out = []
    for row in rows:
        buttons = []
        for button in row:
            label = str(button.get("text", "Действие"))[:128]
            callback = button.get("callback_data")
            url = button.get("url")
            if button.get("request_location"):
                buttons.append({"type": "request_geo_location", "text": label, "quick": False})
            elif callback is not None:
                payload = str(callback)
                if len(payload) > 1024:
                    continue
                buttons.append({"type": "callback", "text": label, "payload": payload})
            elif url:
                buttons.append({"type": "link", "text": label, "url": str(url)[:2048]})
        if buttons:
            out.append(buttons[:7])
    return out[:30] or None


def normalize_update(update: dict[str, Any]) -> dict[str, Any] | None:
    kind = update.get("update_type")
    if kind == "bot_started":
        user = update.get("user") or {}
        return {"kind": "message", "chat_id": update.get("chat_id"),
                "user_id": user.get("user_id"), "text": "/start"}
    if kind == "message_created":
        message = update.get("message") or {}
        user = message.get("sender") or update.get("user") or {}
        body = message.get("body") or {}
        attachments = body.get("attachments") or []
        location = next((item.get("payload", item) for item in attachments if item.get("type") == "location"), None)
        audio = next((item.get("payload", {}) for item in attachments if item.get("type") == "audio"), None)
        normalized = {"kind": "message", "chat_id": (message.get("recipient") or {}).get("chat_id") or update.get("chat_id"),
                      "user_id": user.get("user_id"), "text": body.get("text", "")}
        if location and location.get("latitude") is not None and location.get("longitude") is not None:
            normalized.update(kind="location", latitude=location["latitude"], longitude=location["longitude"])
        elif audio is not None:
            normalized.update(kind="audio", audio_url=audio.get("url"),
                              audio_mime=audio.get("mime_type") or audio.get("content_type"),
                              audio_name=audio.get("file_name") or audio.get("name"))
        return normalized
    if kind == "message_callback":
        callback = update.get("callback") or {}
        message = update.get("message") or {}
        user = callback.get("user") or {}
        return {"kind": "callback", "chat_id": (message.get("recipient") or {}).get("chat_id") or update.get("chat_id"),
                "user_id": user.get("user_id"), "payload": callback.get("payload", ""),
                "callback_id": callback.get("callback_id"), "message_id": message.get("body", {}).get("mid")}
    return None


class MaxAdapter:
    def __init__(self, router: BotRouter) -> None:
        self.router = router

    def dispatch(self, update: dict[str, Any]) -> tuple[dict[str, Any], BotResponse] | None:
        message = normalize_update(update)
        if not message or message["user_id"] is None:
            return None
        if message["kind"] == "callback":
            response = self.router.handle_callback(str(message["user_id"]), str(message["payload"]))
        elif message["kind"] == "location":
            response = self.router.handle_location(str(message["user_id"]), float(message["latitude"]),
                                                   float(message["longitude"]))
        elif message["kind"] == "audio":
            return None
        else:
            response = self.router.handle_message(str(message["user_id"]), str(message["text"]))
        return message, response

    async def dispatch_audio(self, update: dict[str, Any], http: httpx.AsyncClient):
        message = normalize_update(update)
        if not message or message["user_id"] is None or message["kind"] != "audio":
            return None
        url = message.get("audio_url")
        if not isinstance(url, str) or not _audio_url_allowed(url):
            raise MaxAudioError("Недопустимый адрес аудиофайла MAX")
        content = bytearray()
        # Redirects are deliberately disabled: a trusted MAX host cannot redirect to an internal URL.
        async with http.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                raise MaxAudioError("Перенаправление аудиофайла MAX запрещено")
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > MAX_AUDIO_BYTES:
                raise MaxAudioError("Аудиозапись превышает допустимый размер 20 МБ")
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_AUDIO_BYTES:
                    raise MaxAudioError("Аудиозапись превышает допустимый размер 20 МБ")
        if not content:
            raise MaxAudioError("Аудиозапись пуста")
        filename = message.get("audio_name") or "recording.webm"
        content_type = message.get("audio_mime") or "audio/webm"
        speech_settings = copy.copy(self.router.settings)
        speech_settings.speech_max_duration_sec = min(
            MAX_AUDIO_DURATION_SECONDS, max(1, int(getattr(speech_settings, "speech_max_duration_sec", 20)))
        )
        transcript = await transcribe_audio(bytes(content), str(filename), str(content_type), speech_settings)
        response = self.router.handle_message(str(message["user_id"]), transcript)
        return message, transcript, response
