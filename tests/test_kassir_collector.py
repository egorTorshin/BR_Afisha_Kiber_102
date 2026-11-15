import json
from datetime import datetime
from pathlib import Path

import httpx

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from src.services.kassir_collector import KassirCollector, parse_kassir_page


PATH = "/bilety-na-koncert"
URL = "https://ufa.kassir.ru/koncert/test-event"


def event(*, city="Уфа", venue="Тестовый зал", address=None):
    return {"@type": "Event", "name": "Концерт тестовой группы",
            "startDate": "2099-11-20T19:00:00+00:00", "url": URL,
            "description": "Анонс концерта тестовой группы в ноябре.",
            "eventStatus": "https://schema.org/EventScheduled",
            "location": {"name": venue, "address": address or f"{city}, улица Ленина, 10"},
            "offers": {"price": 700}}


def listing(*events):
    graph = {"@graph": list(events)}
    links = "".join('<a href="/koncert/test-event"><h2>Концерт тестовой группы</h2>'
                    '<time datetime="2099-11-20T19:00:00+00:00">20 ноября</time></a>' for _ in events)
    return f'<html><script type="application/ld+json">{json.dumps(graph, ensure_ascii=False)}</script>{links}</html>'


def test_parser_keeps_only_bashkortostan_and_visible_show_times():
    page = listing(event(), event(city="Казань", venue="Татнефть Арена"),
                   event(city="Салават", venue="Тестовый зал", address="ул. Салавата Юлаева, 10"))
    records = parse_kassir_page(page, PATH, now=datetime(2099, 11, 1))
    assert len(records) == 1
    assert records[0]["city"] == "Уфа"
    assert records[0]["date_start"] == "2099-11-20T19:00:00"
    assert records[0]["price_min"] == 700
    assert records[0]["org_link"] == URL


def test_parser_rejects_nonvisible_or_external_events():
    other = event()
    other["url"] = "https://evil.example/koncert/test-event"
    assert parse_kassir_page(listing(other), PATH, now=datetime(2099, 11, 1)) == []
    page = listing(event()).replace("2099-11-20T19:00:00+00:00\">20 ноября", "2099-11-21T19:00:00+00:00\">20 ноября")
    assert parse_kassir_page(page, PATH, now=datetime(2099, 11, 1)) == []


def test_collector_imports_detail_then_repeat_is_idempotent():
    db = Database(":memory:")
    apply_migrations(db, Path(__file__).resolve().parents[1] / "migrations")
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if str(request.url) == "https://ufa.kassir.ru" + PATH:
            return httpx.Response(200, text=listing(event()), request=request)
        detail_event = event()
        detail_event["description"] = "Подробная программа концерта с участием известных артистов и новой музыкой."
        detail = f'<script type="application/ld+json">{json.dumps({"@graph": [detail_event]}, ensure_ascii=False)}</script><span>12+</span>'
        return httpx.Response(200, text=detail, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    collector = KassirCollector(client=client, paths=(PATH,), detail_delay=0)
    try:
        first = collector.run(ImportService(db))
        second = collector.run(ImportService(db))
        assert first.ok and second.ok
        assert first.report and first.report.added_count == 1
        assert second.stats["duplicates"] == 1
        assert calls.count(URL) == 1
        row = db.fetchone("SELECT description, age_limit, status FROM events")
        assert row["description"].startswith("Подробная программа")
        assert row["age_limit"] == 12
        assert row["status"] == "draft"
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 1
    finally:
        client.close()
        db.close()


def test_dry_run_never_fetches_detail_or_writes():
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=listing(event()), request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        result = KassirCollector(client=client, paths=(PATH,)).run()
        assert result.ok and result.stats["accepted"] == 1
        assert calls == ["https://ufa.kassir.ru" + PATH]
    finally:
        client.close()


def test_same_source_url_with_two_show_times_keeps_both_sessions():
    db = Database(":memory:")
    apply_migrations(db, Path(__file__).resolve().parents[1] / "migrations")
    second = event()
    second["startDate"] = "2099-11-21T19:00:00+00:00"
    page = listing(event(), second).replace(
        '<a href="/koncert/test-event"><h2>Концерт тестовой группы</h2>'
        '<time datetime="2099-11-20T19:00:00+00:00">20 ноября</time></a>' * 2,
        '<a href="/koncert/test-event"><h2>Концерт тестовой группы</h2>'
        '<time datetime="2099-11-20T19:00:00+00:00">20 ноября</time></a>'
        '<a href="/koncert/test-event"><h2>Концерт тестовой группы</h2>'
        '<time datetime="2099-11-21T19:00:00+00:00">21 ноября</time></a>',
    )

    def respond(request):
        return httpx.Response(200, text=page, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        result = KassirCollector(client=client, paths=(PATH,), detail_limit=0).run(ImportService(db))
        assert result.ok and result.report and result.report.added_count == 2
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 2
        assert {row["date_start"] for row in db.fetchall("SELECT date_start FROM events")} == {
            "2099-11-20T19:00:00", "2099-11-21T19:00:00"}
    finally:
        client.close()
        db.close()
