"""Stage 7.2 regression audit for external event normalization and pipeline boundaries."""

import json
from pathlib import Path

import pytest

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from src.services.normalizer import (
    compute_dedup_key, normalize_text, parse_iso_datetime, parse_price_fields,
    resolve_category, resolve_city,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_importer():
    db = Database(":memory:")
    apply_migrations(db, ROOT / "migrations")
    try:
        yield db, ImportService(db)
    finally:
        db.close()


def record(**changes):
    return {
        "title": "  Ёлка — для детей! ", "category": "спектакль",
        "date_start": "2030-12-20 18:30", "city": "г. Уфе",
        "venue": "ДК Уфа", "address": "ул. Ленина, 1",
        "description": "Подробное описание детского новогоднего представления.",
        "source_url": "https://source.example/event", "source_type": "страница",
        "confidence": 0.95, **changes,
    }


def test_text_date_city_and_category_normalization():
    assert normalize_text(" ЁЛКА— «тест» ") == "елка тест"
    assert parse_iso_datetime("20.12.2030 18:30") == "2030-12-20T18:30:00"
    assert resolve_city("г. Уфе") == "Уфа"
    assert resolve_category("театр") == "спектакль"


@pytest.mark.parametrize("raw,expected", [
    ("бесплатно", (0, 0, True)),
    ("вход свободный", (0, 0, True)),
    ("от 300 до 800 ₽", (300, 800, False)),
    ("500 ₽", (500, 500, False)),
])
def test_external_price_normalization(raw, expected):
    assert parse_price_fields(raw) == expected


def test_dedup_identity_uses_normalized_title_date_and_venue():
    first = compute_dedup_key("Ёлка", "2030-12-20T18:30:00", "Уфа", "ДК Уфа")
    same_identity_other_city = compute_dedup_key("елка", "2030-12-20T18:30:00", "Стерлитамак", "ДК Уфа")
    assert first == same_identity_other_city


def test_external_import_persists_sources_raw_and_keeps_unverified_draft(isolated_importer):
    db, importer = isolated_importer
    first, second = record(), record(source_url="https://other.example/event")
    report = importer.import_records([first, second], "fixture", source_type="страница", default_status="draft")
    assert report.added_count == 1 and report.updated_count == 1
    event = db.fetchone("SELECT * FROM events")
    raw = db.fetchall("SELECT * FROM raw_events ORDER BY ingested_at")
    sources = db.fetchall("SELECT * FROM event_sources")
    assert event["status"] == "draft"
    assert event["verified_at"] and event["confidence"] == 0.95
    assert len(sources) == 2
    assert all(row["source_type"] == "страница" and row["source_hash"] for row in sources)
    assert len(raw) == 2 and all(json.loads(row["raw_payload_json"]) for row in raw)


def test_low_confidence_incomplete_and_bad_url_are_not_public(isolated_importer):
    db, importer = isolated_importer
    report = importer.import_records([
        record(confidence=0.5), record(title="Краткое описание", description="Коротко"), record(source_url="javascript:bad()"),
    ], "fixture", source_type="страница", default_status="draft")
    assert report.added_count == 2 and report.rejected_count == 1
    rows = db.fetchall("SELECT status, is_disputed FROM events")
    assert len(rows) == 2 and all(row["status"] == "draft" and row["is_disputed"] for row in rows)
    rejected_raw = db.fetchall("SELECT raw_payload_json FROM raw_events")
    assert any("javascript:bad()" in row["raw_payload_json"] for row in rejected_raw)


def test_same_venue_name_in_different_cities_stays_separate_and_disputed(isolated_importer):
    db, importer = isolated_importer
    importer.import_records([record()], "one", source_type="страница", default_status="draft")
    importer.import_records([record(city="Стерлитамак")], "two", source_type="страница", default_status="draft")
    rows = db.fetchall("SELECT city, status, is_disputed FROM events WHERE merged_into IS NULL ORDER BY city")
    assert len(rows) == 2
    assert {row["city"] for row in rows} == {"Уфа", "Стерлитамак"}
    assert all(row["status"] == "draft" and row["is_disputed"] for row in rows)


def test_recognizable_missing_fields_and_past_date_go_to_moderation(isolated_importer):
    db, importer = isolated_importer
    report = importer.import_records([
        record(description="", address=""),
        record(title="Событие из прошлого", date_start="2020-12-20T18:30:00"),
    ], "fixture", source_type="страница", default_status="published")
    assert report.added_count == 2 and report.disputed_count == 2
    rows = db.fetchall("SELECT status, is_disputed, dispute_reason FROM events")
    assert all(row["status"] == "draft" and row["is_disputed"] for row in rows)
    assert any("Дата мероприятия не в будущем" in row["dispute_reason"] for row in rows)


def test_external_explicit_publish_requires_verification_and_manual_admin_is_trusted(isolated_importer):
    db, importer = isolated_importer
    importer.import_records([record(status="published")], "external", source_type="страница")
    external = db.fetchone("SELECT status FROM events")
    assert external["status"] == "draft"
    manual = record(id="manual-event", status="published", source_type="ручной ввод")
    _, saved = importer.create_event_manual(manual, actor="admin:test")
    assert saved.status == "published"


def test_moderator_publication_runs_common_external_validation(isolated_importer):
    db, importer = isolated_importer
    candidate = record(id="verified-external", status="draft")
    importer.import_records([candidate], "external", source_type="страница", default_status="draft")
    assert importer.update_event_status("verified-external", "published", actor="moderator:test").status == "published"
    importer.update_event_status("verified-external", "cancelled", actor="moderator:test")
    row = db.fetchone("SELECT status FROM events WHERE id = ?", ("verified-external",))
    assert row["status"] == "cancelled"
    bad = record(id="unverified-external", title="Другое событие", date_start="2020-01-01", status="draft")
    importer.import_records([bad], "external", source_type="страница", default_status="draft")
    with pytest.raises(ValueError, match="будущую дату"):
        importer.update_event_status("unverified-external", "published", actor="moderator:test")


def test_repeat_import_updates_one_event_and_retains_sources_and_audit(isolated_importer):
    db, importer = isolated_importer
    original = record()
    importer.import_records([original, {**original, "source_url": "https://other.example/event"}], "one", source_type="страница", default_status="draft")
    moved = {**original, "date_start": "2031-01-20T18:30:00",
             "description": "Новое описание события после переноса даты мероприятия."}
    importer.import_records([moved], "two", source_type="страница", default_status="draft")
    rows = db.fetchall("SELECT * FROM events WHERE merged_into IS NULL")
    assert len(rows) == 1 and rows[0]["date_start"].startswith("2031-01-20")
    assert len(db.fetchall("SELECT * FROM event_sources")) == 2
    assert db.fetchone("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'update_event'")["n"] >= 1


def test_reprocess_saved_raw_payload_without_http_and_recollect_updates(isolated_importer, monkeypatch):
    db, importer = isolated_importer
    original = record()
    importer.import_records([original], "fixture", source_type="страница", default_status="draft")
    raw = db.fetchone("SELECT id, raw_payload_json FROM raw_events")
    changed = {**original, "description": "Обновлённое подробное описание события после переноса."}
    db.execute("UPDATE raw_events SET raw_payload_json = ? WHERE id = ?",
               (json.dumps(changed, ensure_ascii=False), raw["id"]))
    monkeypatch.setattr("httpx.Client.get", lambda *a, **k: pytest.fail("reprocess made HTTP request"))
    assert importer.reprocess_raw_events() == 1
    event = db.fetchone("SELECT description, status FROM events")
    assert event["description"] == changed["description"] and event["status"] == "draft"
