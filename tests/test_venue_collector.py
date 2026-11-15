from pathlib import Path

import httpx

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from src.services.venue_collector import BY_CODE, VenueCollector, parse_nur, parse_opera, parse_philharmonic, parse_philharmonic_detail


PHIL = '''<article class="hcalendar event type-event">
<abbr class="dtstart" title="2099-11-18 19:00"></abbr>
<h3 class="entry-title summary"><a href="https://bashgf.ru/afisha/event/test/">Концерт народной музыки</a></h3>
<div class="my-posters-content">Большая концертная программа для всех зрителей.</div></article>'''
OPERA = '''<div>16 ноября</div><h3><a href="/repertoire/test/">Легенда о любви</a></h3>
<div>Состав 16 ноября в 19:00</div>'''
NUR = '''<div>17 ноября 19:00 Вторник</div><h4>Узелки любви</h4>
<div>Место проведения: Большой зал</div>'''


def test_each_official_listing_has_a_parser(monkeypatch):
    monkeypatch.setattr("src.services.venue_collector._date", lambda day, month, clock: "2099-11-17T19:00:00")
    assert parse_philharmonic(PHIL)[0]["title"] == "Концерт народной музыки"
    assert parse_opera(OPERA)[0]["title"] == "Легенда о любви"
    assert parse_nur(NUR)[0]["title"] == "Узелки любви"


def test_philharmonic_reads_published_price_and_full_detail():
    listing = PHIL.replace('</article>', '<div class="event-ticket"><span class="ticket-name">600 - </span><span class="ticket-price">1200 ₽</span></div></article>')
    item = parse_philharmonic(listing)[0]
    assert (item["price_min"], item["price_max"], item["is_free"]) == (600, 1200, False)
    detail = parse_philharmonic_detail('''<article class="type-event"><div class="my-poster-content">
    <p>Полное описание мероприятия с подробностями программы.</p></div>
    <span class="event-ticket"><span class="ticket-name">Бесплатно</span></span></article>''')
    assert detail["description"] == "Полное описание мероприятия с подробностями программы."
    assert detail["is_free"] is True


def test_philharmonic_uses_accepted_browser_headers():
    collector = VenueCollector(BY_CODE["bashgf"])
    try:
        assert collector.client.headers["User-Agent"].startswith("Mozilla/5.0")
        assert "text/html" in collector.client.headers["Accept"]
    finally:
        collector.close()


def test_import_is_draft_and_repeat_is_idempotent():
    db = Database(":memory:")
    apply_migrations(db, Path(__file__).resolve().parents[1] / "migrations")
    importer = ImportService(db)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=PHIL)))
    collector = VenueCollector(BY_CODE["bashgf"], client=client)
    first = collector.run(importer)
    second = collector.run(importer)
    client.close()
    assert first.ok and second.ok
    assert first.report and first.report.added_count == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 1
    assert db.fetchone("SELECT status FROM events")["status"] == "draft"
    assert second.stats["duplicates"] == 1
    db.close()


def test_failed_parse_never_writes():
    db = Database(":memory:")
    apply_migrations(db, Path(__file__).resolve().parents[1] / "migrations")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>")))
    result = VenueCollector(BY_CODE["nur"], client=client).run(ImportService(db))
    client.close()
    assert not result.ok
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 0
    db.close()


def test_venue_listing_retries_temporary_error(monkeypatch):
    monkeypatch.setattr("src.services.http_retry.time.sleep", lambda _seconds: None)
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=NUR, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        result = VenueCollector(BY_CODE["nur"], client=client, retries=2).run()
        assert result.ok
        assert calls == 2
    finally:
        client.close()


def test_venue_listing_does_not_retry_permanent_error(monkeypatch):
    monkeypatch.setattr("src.services.http_retry.time.sleep", lambda _seconds: None)
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        result = VenueCollector(BY_CODE["nur"], client=client, retries=2).run()
        assert not result.ok
        assert calls == 1
    finally:
        client.close()
