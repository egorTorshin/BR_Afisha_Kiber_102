"""Adapter for the event list HTML published by GKZ Bashkortostan."""

from __future__ import annotations

import json
import re
from html import unescape
from datetime import date as date_type
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin


SOURCE_URL = "https://gkzbashkortostan.ru/afisha/"
CITY = "Уфа"
VENUE = "ГКЗ «Башкортостан»"
ADDRESS = "450077, Уфа, улица Ленина, 50"


class _Node:
    def __init__(self, tag: str, attrs: dict[str, Optional[str]]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []

    def text(self) -> str:
        return " ".join(
            child.text() if isinstance(child, _Node) else child
            for child in self.children
        )

    def select(self, predicate) -> list["_Node"]:
        found = [self] if predicate(self) else []
        for child in self.children:
            if isinstance(child, _Node):
                found.extend(child.select(predicate))
        return found


class _TreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _has_class(node: _Node, name: str) -> bool:
    return name in (node.attrs.get("class") or "").split()


def _first(node: _Node, predicate) -> Optional[_Node]:
    matches = node.select(predicate)
    return matches[0] if matches else None


def _category(title: str) -> str:
    value = title.casefold()
    if any(word in value for word in ("спектакль", "балет", "опера", "мюзикл", "представление", "сказка")):
        return "спектакль"
    if "выставк" in value:
        return "выставка"
    if "фестивал" in value:
        return "фестиваль"
    if any(word in value for word in ("концерт", "музыкальн", "рок", "джаз", "трибьют", "оркестр", "ансамбл", "вокал")):
        return "концерт"
    return "другое"


def parse_gkz_html(content: str) -> list[dict[str, Any]]:
    """Convert the current GKZ afisha page into ImportService records."""
    parser = _TreeParser()
    parser.feed(content)
    root = parser.root
    events: list[dict[str, Any]] = []
    # Associate the immediately preceding Event JSON-LD with each card in one
    # pass; the markup contains whitespace text nodes between the elements.
    metadata: dict[int, dict[str, Any]] = {}
    def collect_metadata(node: _Node) -> None:
        last_event: dict[str, Any] = {}
        for child in node.children:
            if not isinstance(child, _Node):
                continue
            if child.tag == "script" and child.attrs.get("type") == "application/ld+json":
                try:
                    value = json.loads(child.text())
                    last_event = value if isinstance(value, dict) and value.get("@type") == "Event" else {}
                except (json.JSONDecodeError, TypeError):
                    last_event = {}
            elif child.tag == "article" and _has_class(child, "mec-event-article"):
                metadata[id(child)] = last_event
            collect_metadata(child)
    collect_metadata(root)

    for article in root.select(lambda n: n.tag == "article" and _has_class(n, "mec-event-article")):
        title_link = _first(article, lambda n: n.tag == "a" and _has_class(n, "mec-color-hover"))
        if title_link is None:
            continue
        title = re.sub(r"\s+", " ", title_link.text()).strip()
        href = (title_link.attrs.get("href") or "").strip()
        if len(title) < 2 or not href:
            continue
        source_url = urljoin(SOURCE_URL, href)
        description_node = _first(article, lambda n: _has_class(n, "mec-event-description"))
        description = re.sub(r"\s+", " ", description_node.text() if description_node else "").strip()
        description = re.sub(r"\s*\.\.\.\s*$", "", description)
        event_metadata = metadata.get(id(article), {})
        structured_description = event_metadata.get("description")
        if isinstance(structured_description, str):
            candidate = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", unescape(structured_description))).strip()
            if len(candidate) > len(description) and "�" not in candidate:
                description = candidate[:10000]

        # JSON-LD supplies the year omitted by the visible date label.
        date = event_metadata.get("startDate", "")
        time_node = _first(article, lambda n: _has_class(n, "mec-start-time"))
        time = time_node.text().strip() if time_node else ""
        if not re.fullmatch(r"\d{1,2}:\d{2}", time):
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        try:
            date_type.fromisoformat(date)
            hour, minute = (int(part) for part in time.split(":"))
            if hour > 23 or minute > 59:
                continue
        except ValueError:
            continue
        if not description or len(description) < 20:
            continue
        offer = event_metadata.get("offers")
        raw_price = offer.get("price") if isinstance(offer, dict) else None
        try:
            price = int(float(raw_price)) if raw_price not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        events.append({
            "title": title,
            "category": _category(title),
            # ImportService stores its accepted ISO format without an offset;
            # the source's displayed wall time is local to Ufa (UTC+05).
            "date_start": f"{date}T{int(time.split(':')[0]):02d}:{time.split(':')[1]}:00",
            "city": CITY,
            "venue": VENUE,
            "address": ADDRESS,
            "description": description or title,
            "price_min": price if price and price > 0 else None,
            "is_free": price == 0,
            "source_url": source_url,
            "source_type": "страница",
            "status": "draft",
            "confidence": 0.7,
            "is_disputed": True,
            "dispute_reason": "Категория и время требуют проверки модератором",
        })
    return events


def parse_gkz_detail_html(content: str) -> dict[str, Any]:
    """Read the full event body; the listing description is often cut mid-sentence."""
    parser = _TreeParser()
    parser.feed(content)
    for class_name in ("mec-single-event-description", "mec-event-content", "entry-content"):
        body = _first(parser.root, lambda node: _has_class(node, class_name))
        if body:
            description = re.sub(r"\s+", " ", body.text()).strip()
            if len(description) >= 20:
                return {"description": description[:10000]}
    return {}
