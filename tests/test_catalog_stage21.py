"""Isolated regression coverage for event catalog acceptance criteria."""

from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.services.import_service import ImportService
from src.services.normalizer import parse_free_text_query, resolve_city
from src.services.search_service import SearchService
from tests.test_backend import db, empty_db, event


def test_catalog_combined_filters_and_exact_price_age_boundaries(db):
    service = ImportService(db)
    _, match = service.create_event_manual(event(
        title="Семейный фестиваль науки", category="фестиваль", city="Уфа",
        venue="Дом учёных", date_start="2026-11-22T14:30:00", price_min=500,
        price_max=900, age_limit=6, org_link="https://tickets.example/event",
    ))
    service.create_event_manual(event(
        title="Семейный фестиваль музыки", category="фестиваль", city="Уфа",
        venue="Дом учёных", date_start="2026-11-22T14:30:00", price_min=500,
        price_max=900, age_limit=12,
    ))
    with TestClient(create_app(db, Settings())) as client:
        response = client.get("/api/v1/events", params={
            "query_text": "фестиваль науки", "city": "Уфа", "category": "фестиваль",
            "venue": "учёных", "date_from": "2026-11-22", "date_to": "2026-11-22",
            "max_price": 500, "max_age_limit": 6,
        })
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["events"]] == [match.id]
        assert client.get("/api/v1/events", params={"max_price": 499}).json()["total"] == 0
        assert client.get("/api/v1/events", params={"max_price": 500}).json()["total"] == 2


def test_public_catalog_card_preserves_only_catalog_facts(db):
    _, record = ImportService(db).create_event_manual(event(
        title="Проверяемая постановка", category="спектакль", price_min=1200,
        price_max=1500, age_limit=16, date_start="2026-11-24T19:15:00",
        description="Описание из тестовой записи.", org_link="https://tickets.example/show",
        source_url="https://venue.example/show",
    ))
    with TestClient(create_app(db, Settings())) as client:
        card = client.get(f"/api/v1/events/{record.id}")
        assert card.status_code == 200, card.text
        body = card.json()
        assert body["title"] == "Проверяемая постановка"
        assert body["date_start"] == "2026-11-24T19:15:00"
        assert body["city"] == "Уфа" and body["venue"] == "Большой зал"
        assert body["address"] == "ул. Ленина, 1"
        assert (body["price_min"], body["price_max"], body["age_limit"]) == (1200, 1500, 16)
        assert body["description"] == "Описание из тестовой записи."
        assert body["org_link"] == "https://tickets.example/show"
        assert body["source_url"] == "https://venue.example/show"
        assert body["sources"][0]["source_url"] == "https://venue.example/show"


def test_public_search_omits_merged_rows_and_typo_does_not_invent_events(db):
    service = ImportService(db)
    _, canonical = service.create_event_manual(event(title="Подтверждённый концерт"))
    _, merged = service.create_event_manual(event(
        title="Другое мероприятие", source_url="https://example.com/merged"
    ))
    db.execute("UPDATE events SET merged_into = ? WHERE id = ?", (canonical.id, merged.id))
    with TestClient(create_app(db, Settings())) as client:
        result = client.get("/api/v1/events").json()
        assert [item["id"] for item in result["events"]] == [canonical.id]
        typo = client.get("/api/v1/events", params={"query_text": "подтверджённый"}).json()
        assert typo["events"] == [] and typo["total"] == 0


def test_city_typo_in_control_query_keeps_other_filters(db):
    service = ImportService(db)
    _, expected = service.create_event_manual(event(
        title="Субботний концерт", city="Стерлитамак", category="концерт",
        date_start="2026-11-14T18:00:00", price_min=400, age_limit=6,
    ))
    service.create_event_manual(event(
        title="Субботний концерт в Уфе", city="Уфа", category="концерт",
        date_start="2026-11-14T18:00:00", price_min=400, age_limit=6,
    ))
    parsed = parse_free_text_query(
        "Концерт в стерлитакмае в субботу", ref_date=__import__("datetime").date(2026, 11, 13), db=db
    )
    assert parsed.city == "Стерлитамак"
    assert parsed.category == "концерт"
    assert parsed.date_preset == "saturday"
    results, total, _ = SearchService(db).search(parsed)
    assert total == 1 and [record.id for record in results] == [expected.id]
    assert resolve_city("Стерлитамак", db=db) == "Стерлитамак"
    assert resolve_city("Казань", db=db) is None


def test_city_fuzzy_match_refuses_equally_close_dictionary_entries():
    class Dictionary:
        @staticmethod
        def fetchall(_query):
            return [
                {"name": "Город-А", "normalized_name": "abcdefg", "aliases_json": "[]"},
                {"name": "Город-Б", "normalized_name": "abcdeh", "aliases_json": "[]"},
            ]

    assert resolve_city("abcdef", db=Dictionary()) is None
