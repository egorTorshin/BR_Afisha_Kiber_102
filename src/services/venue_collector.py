"""Conservative adapters for official Ufa venue event listings."""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from src.services.gkz_parser import _TreeParser, _has_class, _first
from src.services.collector_dedup import find_existing
from src.services.http_retry import get_source_page
from src.services.import_service import ImportService
from src.services.normalizer import compute_dedup_key, normalize_text


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    url: str
    host: str
    address: str
    parser: str = ""


DEFAULT_SOURCE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "event_sources.json"
SUPPORTED_HTML_PARSERS = {"opera", "philharmonic", "nur"}


def load_source_config(path: str | Path | None = None) -> list[dict[str, str]]:
    """Load and validate source declarations; no network access is performed."""
    config_path = Path(path or os.getenv("VENUE_SOURCES_CONFIG") or DEFAULT_SOURCE_CONFIG)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать конфигурацию источников: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("Конфигурация источников должна быть JSON-списком")
    result = []
    codes = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Источник #{index + 1} должен быть объектом")
        code, name, url, address, parser = (item.get(key) for key in ("code", "name", "url", "address", "parser"))
        parsed = urlsplit(url if isinstance(url, str) else "")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_-]{2,40}", code) or code in codes:
            raise ValueError(f"Некорректный или повторяющийся code у источника #{index + 1}")
        if not all(isinstance(v, str) and v.strip() for v in (name, url, address, parser)):
            raise ValueError(f"Источник {code} должен содержать name, url, address и parser")
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Некорректный URL источника {code}: требуется HTTPS URL без учётных данных")
        if parser != "gkz" and parser not in SUPPORTED_HTML_PARSERS:
            raise ValueError(f"Неподдерживаемый parser у источника {code}: {parser}")
        codes.add(code)
        result.append({"code": code, "name": name.strip(), "url": url.strip(),
                       "address": address.strip(), "parser": parser.strip()})
    return result


def venues_from_config(path: str | Path | None = None) -> tuple[Venue, ...]:
    venues = []
    for item in load_source_config(path):
        if item["parser"] == "gkz":
            continue  # GKZ keeps its dedicated adapter and scheduler.
        host = urlsplit(item["url"]).hostname or ""
        venues.append(Venue(item["code"], item["name"], item["url"], host,
                            item["address"], item["parser"]))
    return tuple(venues)


VENUES = venues_from_config()
BY_CODE = {venue.code: venue for venue in VENUES}
MONTHS = {name: i for i, name in enumerate(("января", "февраля", "марта", "апреля", "мая", "июня",
                                           "июля", "августа", "сентября", "октября", "ноября", "декабря"), 1)}
MONTH_RE = "|".join(MONTHS)
DATE_RE = re.compile(rf"\b(\d{{1,2}})\s+({MONTH_RE})\b", re.I)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
USER_AGENT = "KiberAfishaVenueCollector/1.0"
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _official_url(url: str, venue: Venue) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {venue.host, f"www.{venue.host}"}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _date(day: int, month_name: str, clock: str) -> str | None:
    now = datetime.now(ZoneInfo("Asia/Yekaterinburg"))
    month = MONTHS[month_name.lower()]
    year = now.year + (1 if month < now.month - 1 else 0)
    try:
        value = datetime(year, month, day, int(clock[:2]), int(clock[3:5]))
    except ValueError:
        return None
    return value.isoformat(timespec="seconds") if value.date() >= now.date() else None


def _record(venue: Venue, title: str, date_start: str, source_url: str,
            description: str = "", category: str = "спектакль",
            price_min: int | None = None, price_max: int | None = None,
            is_free: bool = False) -> dict:
    title = _clean(title)[:250]
    description = _clean(description)[:10000]
    if len(description) < 20:
        description = "Подробное описание пока не опубликовано площадкой."
    identity = f"{venue.code}|{source_url}|{date_start}|{title}"
    return {"id": hashlib.sha256(identity.encode()).hexdigest()[:32], "title": title,
            "category": category, "date_start": date_start, "city": "Уфа",
            "venue": venue.name, "address": venue.address, "description": description,
            "source_url": source_url, "source_type": "страница", "confidence": 0.7,
            "price_min": price_min, "price_max": price_max, "is_free": is_free}


def _philharmonic_price(article) -> tuple[int | None, int | None, bool]:
    tickets = article.select(lambda node: _has_class(node, "event-ticket"))
    prices: list[int] = []
    free = False
    for ticket in tickets:
        text = _clean(ticket.text()).casefold()
        if "бесплатно" in text or "свободный вход" in text:
            free = True
        if "₽" in text or "руб" in text or "цена:" in text:
            prices.extend(int(value.replace(" ", "")) for value in re.findall(r"\b\d[\d\s]*\b", text))
    return (min(prices), max(prices), False) if prices else (None, None, free)


def _philharmonic_category(title: str, description: str) -> str:
    heading = normalize_text(title)
    context = normalize_text(f"{title} {description}")
    if "фестивал" in heading:
        return "фестиваль"
    if any(word in context for word in ("спектакль", "пьеса", "постановка")):
        return "спектакль"
    return "концерт"


def parse_philharmonic(html: str, venue: Venue = BY_CODE["bashgf"]) -> list[dict]:
    parser = _TreeParser()
    parser.feed(html)
    records = []
    for article in parser.root.select(lambda n: n.tag == "article" and _has_class(n, "type-event")):
        heading = _first(article, lambda n: n.tag == "h3" and _has_class(n, "entry-title"))
        link = _first(heading, lambda n: n.tag == "a") if heading else None
        when = _first(article, lambda n: n.tag == "abbr" and _has_class(n, "dtstart"))
        if not (link and when):
            continue
        source_url = urljoin(venue.url, link.attrs.get("href") or "")
        if not _official_url(source_url, venue):
            continue
        try:
            date_start = datetime.strptime((when.attrs.get("title") or "").strip(), "%Y-%m-%d %H:%M").isoformat(timespec="seconds")
        except ValueError:
            continue
        if date_start[:10] < datetime.now(ZoneInfo("Asia/Yekaterinburg")).date().isoformat():
            continue
        title = _clean(link.text())
        body = _first(article, lambda n: _has_class(n, "my-poster-content")) or _first(
            article, lambda n: _has_class(n, "my-posters-content"))
        description = body.text() if body else ""
        price_min, price_max, is_free = _philharmonic_price(article)
        if title:
            records.append(_record(venue, title, date_start, source_url, description,
                                   _philharmonic_category(title, description),
                                   price_min, price_max, is_free))
    return records


def parse_philharmonic_detail(html: str) -> dict:
    parser = _TreeParser()
    parser.feed(html)
    body = _first(parser.root, lambda node: _has_class(node, "my-poster-content"))
    result: dict = {}
    if body:
        description = _clean(body.text())[:10000]
        if len(description) >= 20:
            result["description"] = description
    article = _first(parser.root, lambda node: node.tag == "article" and _has_class(node, "type-event"))
    if article:
        price_min, price_max, is_free = _philharmonic_price(article)
        if price_min is not None or is_free:
            result.update(price_min=price_min, price_max=price_max, is_free=is_free)
    return result


class _TextListing(HTMLParser):
    def __init__(self, headings: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.headings = headings
        self.parts: list[str] = []
        self._heading: str | None = None
        self._heading_start = 0
        self._heading_href = ""
        self._links: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.headings:
            self._heading = tag
            self._heading_href = ""
            self._heading_start = len(self.parts)
        if tag == "a":
            self._links.append(attrs.get("href") or "")
            if self._heading:
                self._heading_href = attrs.get("href") or ""
        if tag in {"br", "p", "div", "article", "section", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag == self._heading:
            title = _clean("".join(self.parts[self._heading_start:]))
            del self.parts[self._heading_start:]
            self.parts.append(f" [[EVENT:{title}|{self._heading_href}]] ")
            self._heading = None
        if tag == "a" and self._links:
            self._links.pop()
        if tag in {"br", "p", "div", "article", "section", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)

    @property
    def text(self):
        return _clean("".join(self.parts))


def parse_opera(html: str, venue: Venue = BY_CODE["bashopera"]) -> list[dict]:
    page = _TextListing({"h3"})
    page.feed(html)
    text = page.text
    records = []
    pattern = re.compile(rf"\[\[EVENT:([^|\]]+)\|([^\]]*)\]\].{{0,1000}}?Состав\s+(\d{{1,2}})\s+({MONTH_RE})\s+в\s+(\d{{1,2}}:\d{{2}})", re.I)
    for title, href, day, month, clock in pattern.findall(text):
        date_start = _date(int(day), month, clock)
        source_url = urljoin(venue.url, href or venue.url)
        if date_start and _official_url(source_url, venue):
            category = "концерт" if "концерт" in normalize_text(title) else "спектакль"
            records.append(_record(venue, title, date_start, source_url, category=category))
    return records


def parse_nur(html: str, venue: Venue = BY_CODE["nur"]) -> list[dict]:
    page = _TextListing({"h4"})
    page.feed(html)
    text = page.text
    records = []
    pattern = re.compile(rf"(\d{{1,2}})\s+({MONTH_RE})\s+(\d{{1,2}}:\d{{2}}).{{0,300}}?\[\[EVENT:([^|\]]+)\|([^\]]*)\]\]", re.I)
    for day, month, clock, title, href in pattern.findall(text):
        date_start = _date(int(day), month, clock)
        source_url = urljoin(venue.url, href or venue.url)
        if date_start and _official_url(source_url, venue):
            records.append(_record(venue, title, date_start, source_url))
    return records


PARSER_ADAPTERS = {"opera": parse_opera, "philharmonic": parse_philharmonic, "nur": parse_nur}
PARSERS = {venue.code: PARSER_ADAPTERS[venue.parser] for venue in VENUES}


@dataclass
class VenueResult:
    ok: bool
    message: str
    stats: dict[str, Any]
    report: Any = None


class VenueCollector:
    def __init__(self, venue: Venue, *, client: httpx.Client | None = None,
                 timeout: float = 20.0, retries: int = 2):
        if timeout <= 0 or retries < 0:
            raise ValueError("Таймаут должен быть положительным, число повторов — неотрицательным")
        self.venue = venue
        headers = {"User-Agent": BROWSER_USER_AGENT if venue.code == "bashgf" else USER_AGENT,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False,
                                             headers=headers)
        self.timeout = timeout
        self.retries = retries
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    def run(self, importer: ImportService | None = None) -> VenueResult:
        stats: dict[str, Any] = {"source": self.venue.code, "parsed": 0, "accepted": 0,
                                 "duplicates": 0, "ambiguous": 0, "rejected": 0}
        try:
            response = get_source_page(self.client, self.venue.url, timeout=self.timeout,
                                       retries=self.retries)
            if not _official_url(str(response.url), self.venue):
                raise ValueError("Источник перенаправил запрос на другой домен")
            if len(response.content) > 2_000_000:
                raise ValueError("Страница превышает допустимый размер")
            records = PARSERS[self.venue.code](response.text, self.venue)
            stats["parsed"] = len(records)
            if not records:
                return VenueResult(False, "На странице не найдены пригодные события; база не изменена.", stats)
            if self.venue.code == "bashgf":
                short = [record for record in records if len(record["description"]) < 120]
                def enrich(record):
                    if not _official_url(record["source_url"], self.venue):
                        return False
                    try:
                        detail_response = get_source_page(self.client, record["source_url"],
                                                          timeout=min(self.timeout, 7.0), retries=self.retries)
                        if len(detail_response.content) > 2_000_000:
                            return False
                        detail = parse_philharmonic_detail(detail_response.text)
                        if len(detail.get("description", "")) <= len(record["description"]):
                            detail.pop("description", None)
                        record.update(detail)
                        return True
                    except httpx.HTTPError:
                        return False
                if short and enrich(short[0]) and len(short) > 1:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        list(pool.map(enrich, short[1:]))
            validator = ImportService(None)  # type: ignore[arg-type]
            valid = []
            seen = set()
            for record in records:
                key = compute_dedup_key(record["title"], record["date_start"], record["city"], record["venue"])
                if key in seen:
                    continue
                seen.add(key)
                try:
                    validator._validate_and_normalize(record, default_source_type="страница", default_status="draft")
                    valid.append(record)
                except ValueError:
                    stats["rejected"] += 1
            stats["accepted"] = len(valid)
            if not valid:
                return VenueResult(False, "Все записи отклонены проверкой; база не изменена.", stats)
            if importer is None:
                return VenueResult(True, "Сухая проверка завершена; база не изменена.", stats)
            fresh = []
            for record in valid:
                norm = importer._validate_and_normalize(record, default_source_type="страница", default_status="draft")
                match, existing = find_existing(importer, norm)
                if match == "exact":
                    if existing["source_url"] != norm["source_url"]:
                        importer.save_source(existing["id"], norm, "venue_collector")
                        stats["duplicates"] += 1
                        continue
                    stats["duplicates"] += 1
                if match == "ambiguous":
                    stats["ambiguous"] += 1
                    continue
                fresh.append(record)
            report = importer.import_records(fresh, source_file=self.venue.url, actor="venue_collector",
                                             source_type="страница", default_status="draft") if fresh else None
            return VenueResult(True, "Сбор площадки завершён.", stats, report)
        except (httpx.HTTPError, ValueError) as exc:
            return VenueResult(False, f"Сбор площадки не выполнен: {type(exc).__name__}: {exc}", stats)
