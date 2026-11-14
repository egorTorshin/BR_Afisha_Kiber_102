"""HTTP collection of the public GKZ Bashkortostan afisha page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx
import re

from src.services.gkz_parser import SOURCE_URL, parse_gkz_html
from src.services.import_service import ImportService
from src.models.schemas import ImportReport


USER_AGENT = "KiberAfishaGKZCollector/1.0 (+https://gkzbashkortostan.ru/)"


@dataclass(frozen=True)
class GKZCollectionResult:
    ok: bool
    message: str
    report: Optional[ImportReport] = None
    stats: Optional[dict] = None


class GKZCollector:
    def __init__(self, *, timeout: float = 15.0, retries: int = 2,
                 client: Optional[httpx.Client] = None) -> None:
        if timeout <= 0 or retries < 0:
            raise ValueError("Таймаут должен быть положительным, число повторов — неотрицательным")
        self.timeout = timeout
        self.retries = retries
        self.client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _download(self):
        for attempt in range(self.retries + 1):
            try:
                response = self.client.get(SOURCE_URL, timeout=self.timeout, headers={"User-Agent": USER_AGENT})
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status == 429 or 500 <= status <= 599
                if not retryable or attempt == self.retries:
                    raise
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.retries:
                    raise
        raise RuntimeError("Загрузка ГКЗ не выполнена")

    def collect(self, importer: ImportService, *, actor: str = "gkz_collector") -> GKZCollectionResult:
        try:
            response = self._download()
        except httpx.TimeoutException:
            return GKZCollectionResult(False, "Таймаут при загрузке страницы ГКЗ; база данных не изменена.")
        except httpx.HTTPStatusError as exc:
            return GKZCollectionResult(
                False,
                f"Сайт ГКЗ вернул HTTP {exc.response.status_code}; база данных не изменена.",
            )
        except httpx.TransportError as exc:
            return GKZCollectionResult(False, f"Сетевая ошибка при загрузке ГКЗ ({exc}); база данных не изменена.")

        html = response.text
        records = parse_gkz_html(html)
        cards_found = len(re.findall(r"<article\b[^>]*class=['\"][^'\"]*\bmec-event-article\b", html, re.I))
        if not records:
            return GKZCollectionResult(False, "На странице ГКЗ не найдено пригодных событий; база данных не изменена.")
        # No explicit status means ImportService defaults new records to draft and
        # preserves the current status of matching, possibly moderated records.
        for record in records:
            record.pop("status", None)
        report = importer.import_records(
            records,
            source_file=SOURCE_URL,
            actor=actor,
            source_type="страница",
            default_status="draft",
        )
        return GKZCollectionResult(True, "Сбор страницы ГКЗ завершён.", report)

    def dry_run(self) -> GKZCollectionResult:
        """Fetch and validate records without opening or mutating the application DB."""
        try:
            response = self._download()
        except httpx.TimeoutException:
            return GKZCollectionResult(False, "Таймаут при загрузке страницы ГКЗ.")
        except httpx.HTTPStatusError as exc:
            return GKZCollectionResult(False, f"Сайт ГКЗ вернул HTTP {exc.response.status_code}.", stats={"http_status": exc.response.status_code})
        except httpx.TransportError as exc:
            return GKZCollectionResult(False, f"Сетевая ошибка при загрузке ГКЗ ({exc}).")

        html = response.text
        records = parse_gkz_html(html)
        cards_found = len(re.findall(r"<article\b[^>]*class=['\"][^'\"]*\bmec-event-article\b", html, re.I))
        # ImportService's normalization path is shared; with db=None it uses the
        # built-in dictionaries and performs no persistence.
        validator = ImportService(None)  # type: ignore[arg-type]
        accepted = 0
        rejected_reasons = []
        for index, record in enumerate(records, 1):
            try:
                validator._validate_and_normalize(record, default_source_type="страница", default_status="draft")
                accepted += 1
            except Exception as exc:
                rejected_reasons.append({"row": index, "title": record.get("title", "<без названия>"), "reason": str(exc)})
        parse_rejected = max(0, cards_found - len(records))
        if parse_rejected:
            rejected_reasons.append({"row": "parser", "title": "карточки без разбора", "reason": f"Парсер отклонил карточек: {parse_rejected} (неполные или некорректные поля)"})
        stats = {
            "http_status": response.status_code,
            "html_bytes": len(response.content),
            "cards_found": cards_found,
            "parsed": accepted,
            "rejected": cards_found - accepted,
            "rejected_reasons": rejected_reasons,
        }
        return GKZCollectionResult(True, "Dry-run страницы ГКЗ завершён; база данных не открывалась.", stats=stats)
