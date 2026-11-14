from __future__ import annotations

from typing import Any

from src.bot.router import BotResponse, BotRouter


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
        normalized = {"kind": "message", "chat_id": (message.get("recipient") or {}).get("chat_id") or update.get("chat_id"),
                      "user_id": user.get("user_id"), "text": body.get("text", "")}
        if location and location.get("latitude") is not None and location.get("longitude") is not None:
            normalized.update(kind="location", latitude=location["latitude"], longitude=location["longitude"])
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
        else:
            response = self.router.handle_message(str(message["user_id"]), str(message["text"]))
        return message, response
