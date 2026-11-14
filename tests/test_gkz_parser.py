import json
from pathlib import Path

import pytest

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.gkz_parser import ADDRESS, CITY, SOURCE_URL, VENUE, parse_gkz_html, parse_gkz_detail_html
from src.services.import_service import ImportService


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/gkz/afisha.html"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_real_gkz_snapshot_extracts_expected_fields():
    events = parse_gkz_html(FIXTURE.read_text(encoding="utf-8"))
    assert len(events) == 57
    first = events[0]
    assert first["title"] == "Спектакль «Пятницы Абеля» по пьесе Освальда Заградника «Соло для часов с боем»"
    assert first["date_start"] == "2026-11-14T18:00:00"
    assert first["description"].startswith("Аннотация к спектаклю")
    assert first["source_url"].startswith("https://gkzbashkortostan.ru/events/")
    assert first["city"] == CITY == "Уфа"
    assert first["venue"] == VENUE
    assert first["address"] == ADDRESS


def _card(*, start="2026-11-14", time="18:00", href="/events/example/", title="Проверочное событие", description="Достаточно длинное описание события для проверки парсера."):
    metadata = json.dumps({"@type": "Event", "startDate": start}, ensure_ascii=False)
    return f'''<script type="application/ld+json">{metadata}</script>
    <article class="mec-event-article"><a class="mec-color-hover" href="{href}">{title}</a>
    <div class="mec-event-description">{description}</div><span class="mec-start-time">{time}</span></article>'''


@pytest.mark.parametrize("html", [
    _card(start="2026-02-30"),
    _card(time="24:00"),
    _card(time="18:60"),
    _card(href=""),
    _card(title=""),
    _card(description="Коротко"),
    '<article class="mec-event-article"><a class="mec-color-hover">Без даты</a></article>',
])
def test_incomplete_or_invalid_cards_are_skipped(html):
    assert parse_gkz_html(html) == []


def test_structured_event_details_replace_cut_listing_excerpt():
    metadata = json.dumps({"@type": "Event", "startDate": "2026-11-14",
                           "description": "Полное описание спектакля, которое продолжается после вводного предложения.",
                           "offers": {"price": "750", "priceCurrency": "RUB"}}, ensure_ascii=False)
    html = f'''<script type="application/ld+json">{metadata}</script>
    <article class="mec-event-article"><a class="mec-color-hover" href="/events/example/">Спектакль</a>
    <div class="mec-event-description">Краткий анонс обрывается на запятой,</div>
    <span class="mec-start-time">18:00</span></article>'''
    item = parse_gkz_html(html)[0]
    assert item["description"].endswith("предложения.")
    assert item["price_min"] == 750
    assert parse_gkz_detail_html('<div class="mec-single-event-description"><p>Полный текст события на странице организатора.</p></div>')["description"] == "Полный текст события на странице организатора."


def test_import_keeps_gkz_events_as_drafts_and_is_idempotent():
    db = Database(":memory:")
    try:
        apply_migrations(db, MIGRATIONS)
        service = ImportService(db)
        records = parse_gkz_html(FIXTURE.read_text(encoding="utf-8"))
        first = service.import_records(records, "afisha.html", actor="test", source_type="страница")
        second = service.import_records(records, "afisha.html", actor="test", source_type="страница")

        assert first.added_count == len(records)
        assert first.rejected_count == 0
        assert first.disputed_count == len(records)
        assert second.added_count == 0
        assert second.unchanged_count == len(records)
        rows = db.fetchall("SELECT status, is_disputed FROM events")
        assert len(rows) == len(records)
        assert {row["status"] for row in rows} == {"draft"}
        assert all(row["is_disputed"] for row in rows)
    finally:
        db.close()
