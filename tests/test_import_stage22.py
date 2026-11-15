"""Isolated regression coverage for CSV/JSON import and event deduplication."""

import csv
import json
from pathlib import Path

import pytest

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


@pytest.fixture(params=["memory", "sqlite_file"])
def importer(request, tmp_path):
    db = Database(":memory:" if request.param == "memory" else str(tmp_path / "isolated.db"))
    apply_migrations(db, MIGRATIONS)
    yield ImportService(db), db
    db.close()


def record(**changes):
    return {
        "title": "Вечер камерной музыки", "category": "концерт",
        "date_start": "2026-12-20T18:00:00", "city": "Уфа",
        "venue": "Малый зал", "address": "ул. Ленина, 1",
        "description": "Концерт камерной музыки в историческом зале.",
        "source_url": "https://first.example/event", "price_min": 300,
        **changes,
    }


def test_csv_fixture_100_and_repeat_are_idempotent(importer):
    service, db = importer
    path = ROOT / "data" / "import_100_events.csv"
    first = service.import_from_file(path)
    assert first.total_rows == 100
    assert first.added_count == 100 and first.rejected_count == 0
    second = service.import_from_file(path)
    assert (second.added_count, second.updated_count, second.unchanged_count, second.rejected_count) == (0, 0, 100, 0)
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 100
    assert db.fetchone("SELECT COUNT(*) AS n FROM raw_events")["n"] == 100


def test_json_import_updates_changed_record_and_preserves_raw_separately(importer):
    service, db = importer
    one = record()
    first = service.import_from_content(json.dumps([one], ensure_ascii=False), "one.json", ".json")
    changed = {**one, "description": "Обновлённое подробное описание камерного концерта.", "price_min": 500}
    second = service.import_from_content(json.dumps([changed], ensure_ascii=False), "one.json", ".json")
    assert first.added_count == 1 and second.updated_count == 1
    event_row = db.fetchone("SELECT * FROM events")
    raw_row = db.fetchone("SELECT * FROM raw_events")
    assert event_row["description"] == changed["description"]
    assert json.loads(raw_row["raw_payload_json"])["price_min"] == 500
    assert event_row["raw_event_id"] == raw_row["id"]


def test_invalid_json_rows_report_dates_prices_required_fields_and_urls(importer):
    service, db = importer
    invalid = [
        record(date_start="завтра"),
        record(price_min=-15),
        record(address=""),
        record(source_url="javascript:alert(1)"),
    ]
    report = service.import_from_content(json.dumps(invalid, ensure_ascii=False), "bad.json", ".json")
    # Empty address is recognizable but incomplete, so it is saved for moderation.
    assert report.rejected_count == 3 and report.added_count == 1
    assert report.disputed_count == 1
    incomplete = db.fetchone("SELECT status, is_disputed, address FROM events")
    assert incomplete["status"] == "draft" and incomplete["is_disputed"] == 1 and not incomplete["address"]
    assert [reason["row"] for reason in report.rejected_reasons] == [1, 2, 4]
    assert all(reason["reason"] for reason in report.rejected_reasons)
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM raw_events")["n"] == 4


def test_csv_bad_rows_are_reported_without_rejecting_valid_rows(importer):
    service, _ = importer
    rows = [record(), record(date_start="not-a-date"), record(source_url="ftp://bad.example/x")]
    output = __import__("io").StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    report = service.import_from_content(output.getvalue(), "mixed.csv", ".csv")
    assert (report.added_count, report.rejected_count) == (1, 2)
    assert "даты" in report.rejected_reasons[0]["reason"].lower()
    assert "URL" in report.rejected_reasons[1]["reason"]


def test_distinct_events_and_same_event_from_two_sources(importer):
    service, db = importer
    same = record()
    second_source = {**same, "source_url": "https://second.example/event"}
    similar_different_event = {**same, "date_start": "2026-12-21T18:00:00", "source_url": "https://third.example/event"}
    report = service.import_records([same, second_source, similar_different_event], "sources.json")
    assert report.added_count == 2 and report.updated_count == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 2
    sources = db.fetchall("SELECT source_url FROM event_sources ORDER BY source_url")
    assert {row["source_url"] for row in sources} == {same["source_url"], second_source["source_url"], similar_different_event["source_url"]}


def test_same_title_different_venue_is_not_merged(importer):
    service, db = importer
    service.import_records([record(), record(venue="Другой зал", source_url="https://second.example/event")], "similar.json")
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 2


@pytest.mark.parametrize("suffix,content", [
    ("csv", b"title,category\n\xff\xfe"),
    ("json", b'[{"title":"valid-looking row"},\xff]'),
])
def test_invalid_utf8_file_reports_import_error_without_partial_data(tmp_path, importer, suffix, content):
    service, db = importer
    path = tmp_path / f"invalid.{suffix}"
    path.write_bytes(content)

    with pytest.raises(ValueError, match="кодировка.*UTF-8"):
        service.import_from_file(path)

    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM raw_events")["n"] == 0
