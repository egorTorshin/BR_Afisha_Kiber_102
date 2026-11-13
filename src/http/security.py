"""Browser origin and anonymous rate identity helpers."""

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request

from src.config import Settings


VISITOR_COOKIE = "afisha_visitor"


def check_browser_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    same_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin not in {same_origin, settings.web_origin}:
        raise HTTPException(403, "Недопустимый источник запроса")


def visitor_identity(request: Request, secret: str) -> tuple[str, str | None]:
    value = request.cookies.get(VISITOR_COOKIE, "")
    try:
        if len(value) > 128:
            raise ValueError("cookie too long")
        token, signature = value.split(".", 1)
        expected = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        if len(token) == 43 and hmac.compare_digest(signature, expected):
            return token, None
    except ValueError:
        pass
    token = secrets.token_urlsafe(32)
    signature = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    return token, f"{token}.{signature}"
