import contextvars
import hashlib
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="system")

# Регулярные выражения для маскирования случайных телефонов/email/токенов в логах (Раздел 6.1, 10)
PHONE_RE = re.compile(r"(\+?7|8)[\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
SECRET_RE = re.compile(r"(?i)\b(?:sensitive_token|api_key|secret|token)[_:= -]*[A-Za-z0-9_-]{3,}\b")


def new_request_id() -> str:
    rid = f"req-{uuid.uuid4().hex[:12]}"
    request_id_ctx.set(rid)
    return rid


def get_request_id() -> str:
    return request_id_ctx.get()


def anonymize_user_id(user_id: Any) -> str:
    if user_id is None:
        return "anonymous"
    raw = str(user_id).encode("utf-8")
    return "usr_" + hashlib.sha256(raw).hexdigest()[:10]


def scrub_pii(text: str) -> str:
    if not text:
        return text
    text = PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = SECRET_RE.sub("[REDACTED_SECRET]", text)
    return text


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "request_id": getattr(record, "request_id", None) or get_request_id(),
            "logger": record.name,
            "message": scrub_pii(record.getMessage()),
        }
        extra_fields = getattr(record, "structured_data", None)
        if isinstance(extra_fields, dict):
            for k, v in extra_fields.items():
                if k in ("phone", "email", "address_personal", "full_name", "username"):
                    continue
                if k == "user_id":
                    payload["user_hash"] = anonymize_user_id(v)
                elif isinstance(v, str):
                    payload[k] = scrub_pii(v)
                else:
                    payload[k] = v
        if record.exc_info:
            payload["exception"] = scrub_pii(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "afisha_rb", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def log_event(logger: logging.Logger, level: int, message: str, **kwargs: Any) -> None:
    logger.log(level, message, extra={"structured_data": kwargs, "request_id": get_request_id()})
