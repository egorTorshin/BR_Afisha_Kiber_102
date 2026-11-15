"""Bounded retries for temporary errors from event source websites."""

import time

import httpx


def get_source_page(client: httpx.Client, url: str, *, timeout: float,
                    retries: int = 2, headers: dict[str, str] | None = None) -> httpx.Response:
    if retries < 0:
        raise ValueError("Число повторов должно быть неотрицательным")
    for attempt in range(retries + 1):
        try:
            response = client.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if attempt == retries or not (status == 429 or 500 <= status <= 599):
                raise
        except httpx.TransportError:
            if attempt == retries:
                raise
        time.sleep(min(0.25 * 2 ** attempt, 2.0))
    raise RuntimeError("Загрузка источника не выполнена")
