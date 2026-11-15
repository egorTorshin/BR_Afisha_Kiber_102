"""HTTP collection of the public GKZ Bashkortostan afisha page."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx
import re
import os
from urllib.parse import urlsplit

from src.services.gkz_parser import SOURCE_URL, parse_gkz_html, parse_gkz_detail_html
from src.services.collector_dedup import find_existing
from src.services.http_retry import get_source_page
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
            trust_env=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _download(self):
        fetch_url = os.getenv("GKZ_FETCH_URL", SOURCE_URL)
        return get_source_page(self.client, fetch_url, timeout=self.timeout,
                               retries=self.retries, headers={"User-Agent": USER_AGENT})

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
        if not records:
            return GKZCollectionResult(False, "На странице ГКЗ не найдено пригодных событий; база данных не изменена.")
        # Listing excerpts often end mid-sentence. Detail failures never block
        # collection; the source URL remains available to the reader.
        incomplete = [record for record in records
                      if len(record["description"]) < 120 or record["description"].rstrip().endswith((",", "…", "..."))]
        def enrich(record):
            parsed_url = urlsplit(record["source_url"])
            if parsed_url.scheme != "https" or parsed_url.hostname not in {"gkzbashkortostan.ru", "www.gkzbashkortostan.ru"}:
                return False
            try:
                response = get_source_page(self.client, record["source_url"],
                                           timeout=min(self.timeout, 7.0), retries=self.retries)
                if len(response.content) > 2_000_000:
                    return False
                detail = parse_gkz_detail_html(response.text)
                if len(detail.get("description", "")) > len(record["description"]):
                    record.update(detail)
                return True
            except httpx.HTTPError:
                return False
        if incomplete:
            if enrich(incomplete[0]) and len(incomplete) > 1:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(enrich, incomplete[1:]))
        # No explicit status means ImportService defaults new records to draft and
        # preserves the current status of matching, possibly moderated records.
        for record in records:
            record.pop("status", None)
        stats = {"source": "gkz", "parsed": len(records), "accepted": 0,
                 "duplicates": 0, "ambiguous": 0, "rejected": 0}
        fresh = []
        seen = set()
        for record in records:
            try:
                normalized = importer._validate_and_normalize(
                    record, default_source_type="страница", default_status="draft")
            except ValueError:
                stats["rejected"] += 1
                continue
            if normalized["dedup_key"] in seen:
                stats["duplicates"] += 1
                continue
            seen.add(normalized["dedup_key"])
            stats["accepted"] += 1
            match, existing = find_existing(importer, normalized)
            if match == "ambiguous":
                stats["ambiguous"] += 1
                continue
            if match == "exact" and existing["source_url"] != normalized["source_url"]:
                importer.save_source(existing["id"], normalized, actor)
                stats["duplicates"] += 1
                continue
            if match == "exact":
                stats["duplicates"] += 1
            fresh.append(record)
        report = importer.import_records(
            fresh,
            source_file=SOURCE_URL,
            actor=actor,
            source_type="страница",
            default_status="draft",
        ) if fresh else None
        return GKZCollectionResult(True, "Сбор страницы ГКЗ завершён.", report, stats)

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
