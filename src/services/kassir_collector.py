"""Collect public event listings from ufa.kassir.ru JSON-LD pages."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from src.services.collector_dedup import find_existing
from src.services.http_retry import get_source_page
from src.services.import_service import ImportService
from src.services.normalizer import compute_dedup_key


BASE_URL = "https://ufa.kassir.ru"
CATEGORY_PATHS = (
    "/bilety-na-koncert", "/bilety-v-teatr", "/detskaya-afisha",
    "/bilety-na-shou", "/bilety-v-muzei", "/bilety-na-festival",
    "/bilety-for-tourists", "/obrazovanie-i-kursy", "/bilety-special",
    "/bilety-na-sportivnye-meropriyatiya",
)
CATEGORY_BY_PATH = {
    "/bilety-na-koncert": "концерт", "/bilety-v-teatr": "спектакль",
    "/detskaya-afisha": "другое", "/bilety-na-shou": "другое",
    "/bilety-v-muzei": "выставка", "/bilety-na-festival": "фестиваль",
    "/bilety-for-tourists": "экскурсия", "/obrazovanie-i-kursy": "другое",
    "/bilety-special": "другое", "/bilety-na-sportivnye-meropriyatiya": "другое",
}
EVENT_PATH_CATEGORIES = {
    "koncert": "концерт", "teatr": "спектакль", "muzey": "выставка",
    "film": "кино", "ekskursii": "экскурсия", "festival": "фестиваль",
}
# The Ufa storefront can include recommendations for Kazan. Accept only places
# whose address or venue explicitly identifies a Bashkortostan settlement.
CITY_STEMS = {
    "Уфа": ("уфа", "уфе", "уфим"),
    "Стерлитамак": ("стерлитамак", "стерлитамакск"),
    "Салават": ("салават", "салаватск"),
    "Нефтекамск": ("нефтекамск",),
    "Октябрьский": ("октябрьский",),
    "Белорецк": ("белорецк",),
    "Бирск": ("бирск",),
    "Туймазы": ("туймаз",),
    "Сибай": ("сибай", "сибайск"),
    "Ишимбай": ("ишимбай", "ишимбайск"),
    "Мелеуз": ("мелеуз",),
    "Кумертау": ("кумертау",),
    "Белебей": ("белебе",),
    "Благовещенск": ("благовещенск",),
    "Учалы": ("учал",),
    "Дюртюли": ("дюртюл",),
    "Баймак": ("баймак",),
    "Янаул": ("янаул",),
    "Чекмагуш": ("чекмагуш",),
}
JSON_LD_RE = re.compile(r'<script\b[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
CARD_TIME_RE = re.compile(
    r'<a\b[^>]*href="(?P<href>[^"]+)"[^>]*>\s*<h2\b[^>]*>.*?</h2>\s*'
    r'<time\b[^>]*datetime="(?P<when>[^"]+)"', re.S | re.I,
)


def _official_url(value: str) -> str | None:
    url = urldefrag(urljoin(BASE_URL, html.unescape(value)))[0]
    parsed = urlsplit(url)
    return url if parsed.scheme == "https" and parsed.hostname == "ufa.kassir.ru" and not parsed.username else None


def _city(address: str, venue: str) -> str | None:
    parts = address.casefold().split(",")
    first_address_parts = parts[0]
    if any(region in parts[0] for region in ("башкортостан", "башкирия", "рб")) and len(parts) > 1:
        first_address_parts += "," + parts[1]
    if any(other in first_address_parts for other in ("казань", "татарстан", "республика татарстан")):
        return None
    for city, stems in CITY_STEMS.items():
        if any(stem in first_address_parts for stem in stems):
            return city
    venue_name = venue.casefold()
    for city, stems in CITY_STEMS.items():
        if any(stem in venue_name for stem in stems):
            return city
    return None


def parse_kassir_page(content: str, category_path: str, *, now: datetime | None = None) -> list[dict]:
    """Use structured Event data and its matching visible local session time."""
    now = now or datetime.now(ZoneInfo("Asia/Yekaterinburg"))
    visible: dict[str, set[str]] = {}
    for match in CARD_TIME_RE.finditer(content):
        url = _official_url(match.group("href"))
        if url:
            try:
                when = datetime.fromisoformat(match.group("when")).replace(tzinfo=None)
                visible.setdefault(url, set()).add(when.isoformat(timespec="seconds"))
            except ValueError:
                pass

    records: list[dict] = []
    seen: set[str] = set()
    for script in JSON_LD_RE.finditer(content):
        try:
            payload = json.loads(script.group(1))
        except json.JSONDecodeError:
            continue
        graph = payload.get("@graph", []) if isinstance(payload, dict) else []
        if isinstance(graph, dict):
            graph = [graph]
        for event in graph if isinstance(graph, list) else []:
            if not isinstance(event, dict) or event.get("@type") != "Event":
                continue
            url = _official_url(str(event.get("url") or ""))
            if not url or url not in visible:
                continue
            location = event.get("location")
            if not isinstance(location, dict):
                continue
            venue = re.sub(r"\s+", " ", str(location.get("name") or "")).strip()
            address = re.sub(r"\s+", " ", str(location.get("address") or "")).strip()
            city = _city(address, venue)
            if not city or not venue or not address:
                continue
            try:
                start = datetime.fromisoformat(str(event["startDate"])).replace(tzinfo=None)
            except (KeyError, ValueError):
                continue
            date_start = start.isoformat(timespec="seconds")
            if date_start not in visible[url] or start.date() < now.date():
                continue
            if "EventCancelled" in str(event.get("eventStatus") or ""):
                continue
            title = re.sub(r"\s+", " ", html.unescape(str(event.get("name") or ""))).strip()[:250]
            if len(title) < 2:
                continue
            identity = compute_dedup_key(title, date_start, city, venue)
            if identity in seen:
                continue
            seen.add(identity)
            description = re.sub(r"\s+", " ", html.unescape(str(event.get("description") or ""))).strip()[:10000]
            offers = event.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            offers = offers if isinstance(offers, dict) else {}
            try:
                price = int(float(offers["price"])) if offers.get("price") is not None else None
            except (ValueError, TypeError):
                price = None
            path_prefix = urlsplit(url).path.strip("/").split("/", 1)[0]
            records.append({
                "id": hashlib.sha256(f"kassir|{identity}".encode()).hexdigest()[:32],
                "title": title, "category": EVENT_PATH_CATEGORIES.get(path_prefix, CATEGORY_BY_PATH[category_path]),
                "date_start": date_start, "city": city, "venue": venue,
                "address": address, "description": description,
                "price_min": price if price is not None and price > 0 else None,
                "is_free": price == 0,
                "source_url": f"{url}#session={date_start}", "org_link": url,
                "source_type": "страница", "confidence": 0.85,
            })
    return records


def parse_kassir_detail(content: str, source_url: str) -> dict:
    """Read longer description and age from an event's own page."""
    result: dict = {}
    for script in JSON_LD_RE.finditer(content):
        try:
            payload = json.loads(script.group(1))
        except json.JSONDecodeError:
            continue
        graph = payload.get("@graph", []) if isinstance(payload, dict) else []
        if isinstance(graph, dict):
            graph = [graph]
        for event in graph if isinstance(graph, list) else []:
            if isinstance(event, dict) and event.get("@type") == "Event" and _official_url(str(event.get("url") or "")) == source_url:
                description = re.sub(r"\s+", " ", html.unescape(str(event.get("description") or ""))).strip()
                if description:
                    result["description"] = description[:10000]
                break
    age = re.search(r"<span>\s*(0|6|12|16|18)\+\s*</span>", content[:100000])
    if age:
        result["age_limit"] = int(age.group(1))
    return result


@dataclass
class KassirResult:
    ok: bool
    message: str
    stats: dict
    report: object | None = None


class KassirCollector:
    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 20.0,
                 retries: int = 2, paths: tuple[str, ...] = CATEGORY_PATHS,
                 detail_limit: int = 200, detail_delay: float = 0.5):
        if (timeout <= 0 or retries < 0 or detail_limit < 0 or detail_delay < 0
                or any(path not in CATEGORY_BY_PATH for path in paths)):
            raise ValueError("Некорректные параметры сборщика KASSIR.RU")
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False,
                                             headers={"User-Agent": "KiberAfishaKassirCollector/1.0"})
        self.timeout, self.retries, self.paths = timeout, retries, paths
        self.detail_limit, self.detail_delay = detail_limit, detail_delay
        self._owns_client = client is None

    def _get(self, url: str) -> httpx.Response:
        response = get_source_page(self.client, url, timeout=self.timeout, retries=self.retries)
        if str(response.url) != url or len(response.content) > 2_000_000:
            raise ValueError("Некорректный ответ источника")
        return response

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def run(self, importer: ImportService | None = None) -> KassirResult:
        stats = {"source": "kassir", "pages": 0, "parsed": 0, "accepted": 0,
                 "duplicates": 0, "ambiguous": 0, "rejected": 0, "failed_pages": []}
        records: dict[str, dict] = {}
        for path in self.paths:
            url = BASE_URL + path
            try:
                response = self._get(url)
                parsed = parse_kassir_page(response.text, path)
                if not parsed and "Найдено 0 событ" not in response.text:
                    raise ValueError("На странице не найдены события")
                stats["pages"] += 1
                stats["parsed"] += len(parsed)
                for record in parsed:
                    key = compute_dedup_key(record["title"], record["date_start"], record["city"], record["venue"])
                    records.setdefault(key, record)
            except (httpx.HTTPError, ValueError) as exc:
                stats["failed_pages"].append({"path": path, "error": type(exc).__name__})
        validator = ImportService(None)  # type: ignore[arg-type]
        valid = []
        for record in records.values():
            try:
                validator._validate_and_normalize(record, default_source_type="страница", default_status="draft")
                valid.append(record)
            except ValueError:
                stats["rejected"] += 1
        stats["accepted"] = len(valid)
        if not valid:
            return KassirResult(False, "Не удалось получить пригодные события; база не изменена.", stats)
        if importer is None:
            return KassirResult(not stats["failed_pages"], "Сухая проверка завершена; база не изменена.", stats)
        fresh = []
        for record in valid:
            norm = importer._validate_and_normalize(record, default_source_type="страница", default_status="draft")
            match, existing = find_existing(importer, norm)
            if match == "exact":
                if existing["source_url"] != norm["source_url"]:
                    importer.save_source(existing["id"], norm, "kassir_collector")
                stats["duplicates"] += 1
                continue
            if match == "ambiguous":
                stats["ambiguous"] += 1
                continue
            fresh.append(record)
        details: dict[str, dict] = {}
        detail_calls = 0
        for record in fresh:
            url = record["org_link"]
            if url not in details and detail_calls < self.detail_limit:
                if detail_calls:
                    time.sleep(self.detail_delay)
                detail_calls += 1
                try:
                    response = self._get(url)
                    details[url] = parse_kassir_detail(response.text, url)
                except (httpx.HTTPError, ValueError):
                    details[url] = {}
            detail = details.get(url, {})
            if len(detail.get("description", "")) > len(record["description"]):
                record["description"] = detail["description"]
            if "age_limit" in detail:
                record["age_limit"] = detail["age_limit"]
        stats["detail_pages"] = detail_calls
        report = importer.import_records(fresh, source_file=BASE_URL, actor="kassir_collector",
                                         source_type="страница", default_status="draft") if fresh else None
        return KassirResult(not stats["failed_pages"], "Сбор KASSIR.RU завершён.", stats, report)
