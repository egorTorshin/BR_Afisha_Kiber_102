"""Translate expected service failures to the HTTP error contract."""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


def run_service(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
