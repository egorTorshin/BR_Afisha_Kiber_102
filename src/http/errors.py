"""Translate expected service failures to the HTTP error contract."""

from collections.abc import Callable
import logging
from typing import Any

from fastapi import HTTPException

from src.utils.logger import get_logger, log_event

logger = get_logger("afisha_rb.http.errors")


def run_service(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except LookupError as exc:
        log_event(logger, logging.WARNING, "Service resource lookup failed", exception_type=type(exc).__name__)
        raise HTTPException(404, "Ресурс не найден") from None
    except ValueError as exc:
        log_event(logger, logging.WARNING, "Service input rejected", exception_type=type(exc).__name__)
        raise HTTPException(422, "Некорректные данные запроса") from None
