import ssl
from pathlib import Path
from typing import Any
import certifi
import httpx


class MaxApiError(RuntimeError):
    pass


def max_ssl_context() -> ssl.SSLContext:
    """Use standard public roots plus MAX's documented Russian Trusted Root CA."""
    context = ssl.create_default_context(cafile=certifi.where())
    certificate = Path(__file__).with_name("certs") / "russian_trusted_root_ca.pem"
    context.load_verify_locations(cafile=str(certificate))
    return context


class MaxClient:
    """Small asynchronous client for the official MAX Bot API."""

    def __init__(self, token: str, http: httpx.AsyncClient | None = None) -> None:
        self.token = token
        self.base_url = "https://platform-api2.max.ru"
        self._http = http

    async def request(self, method: str, path: str, *, params=None, json=None) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("MAX HTTP client is not open")
        response = await self._http.request(method, self.base_url + path, params=params, json=json,
                                            headers={"Authorization": self.token})
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error or body.get("success") is False:
            code = body.get("code", "unknown")
            detail = body.get("message")
            suffix = f" — {detail}" if detail else ""
            raise MaxApiError(f"MAX API error ({response.status_code}): {code}{suffix}")
        return body

    async def send_message(self, chat_id: str | int, text: str, keyboard=None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text[:4000], "format": "html", "notify": True}
        if keyboard:
            payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
        return await self.request("POST", "/messages", params={"chat_id": chat_id}, json=payload)

    async def edit_message(self, message_id: str, text: str, keyboard=None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text[:4000], "format": "html", "notify": False}
        payload["attachments"] = ([{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
                                  if keyboard else [])
        return await self.request("PUT", "/messages", params={"message_id": message_id}, json=payload)

    async def answer_callback(self, callback_id: str, text: str, keyboard=None) -> dict[str, Any]:
        """Acknowledge a button press and replace its message in one API call."""
        message: dict[str, Any] = {"text": text[:4000], "format": "html", "notify": False}
        message["attachments"] = ([{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
                                  if keyboard else [])
        return await self.request("POST", "/answers", params={"callback_id": callback_id},
                                  json={"message": message})

    async def get_updates(self, marker: str | None = None) -> dict[str, Any]:
        # MAX defines `types` as a comma-separated query value (style=form, explode=false).
        params: dict[str, Any] = {
            "timeout": 30,
            "types": "message_created,message_callback,bot_started",
        }
        if marker:
            params["marker"] = marker
        return await self.request("GET", "/updates", params=params)

    async def subscribe_webhook(self, url: str, secret: str) -> dict[str, Any]:
        return await self.request("POST", "/subscriptions", json={
            "url": url,
            "update_types": ["message_created", "message_callback", "bot_started"],
            "secret": secret,
        })
