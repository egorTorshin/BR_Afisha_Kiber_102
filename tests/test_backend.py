import json
import os
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.models.schemas import SearchFilters
from src.services.import_service import ImportService
from src.services.normalizer import parse_price_fields
from src.services.search_service import SearchService

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def event(**changes):
    return {
        "title": "Вечер музыки", "category": "концерт",
        "date_start": "2026-11-20T18:00:00", "city": "Уфа",
        "venue": "Большой зал", "address": "ул. Ленина, 1",
        "description": "Концерт классической музыки для всей семьи.",
        "source_url": "https://example.com/one", **changes,
    }


@pytest.fixture(params=["memory", "sqlite_file", "postgres"])
def empty_db(request, tmp_path):
    admin = None
    if request.param == "postgres":
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Set TEST_POSTGRES_URL to an isolated PostgreSQL test database")
        schema = "test_" + uuid.uuid4().hex
        admin = Database(url)
        admin.execute(f'CREATE SCHEMA "{schema}"')
        separator = "&" if "?" in url else "?"
        database = Database(url + separator + "options=" + quote(f"-csearch_path={schema}"))
    else:
        database = Database(":memory:" if request.param == "memory" else str(tmp_path / "test.db"))
    try:
        yield database
    finally:
        database.close()
        if admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            admin.close()


@pytest.fixture
def db(empty_db):
    apply_migrations(empty_db, MIGRATIONS)
    return empty_db


def test_unknown_price():
    assert parse_price_fields(None) == (None, None, False)
    assert parse_price_fields("") == (None, None, False)
    assert parse_price_fields("уточняется") == (None, None, False)
    assert parse_price_fields(0) == (0, 0, True)
    assert parse_price_fields(None, None, True) == (0, 0, True)


def test_multiple_sources_survive_repeat(db):
    service = ImportService(db)
    for url in ("one", "two", "one", "two"):
        report = service.import_records([event(source_url=f"https://example.com/{url}")], "test")
        assert report.rejected_count == 0
    assert len(db.fetchall("SELECT * FROM events")) == 1
    assert len(db.fetchall("SELECT * FROM event_sources")) == 2


def test_draft_not_public_by_id_or_prefix(db):
    _, draft = ImportService(db).create_event_manual(event(status="черновик"))
    search = SearchService(db)
    assert search.get_event_by_id(draft.id) is None
    assert search.get_event_by_id(draft.id[:8]) is None
    assert search.search(SearchFilters())[1] == 0


@pytest.mark.parametrize("minimum,maximum,free,expected", [
    ("бесплатно", None, None, (0, 0, True)),
    ("от 300 до 800 ₽", None, None, (300, 800, False)),
    ("300 руб", None, None, (300, 300, False)),
    ("до 500", None, None, (None, 500, False)),
    (None, 500, None, (None, 500, False)),
    ("1 000", "2 000", None, (1000, 2000, False)),
    (0, 500, False, (0, 500, False)),
    (None, None, "false", (None, None, False)),
])
def test_price_variants(minimum, maximum, free, expected):
    assert parse_price_fields(minimum, maximum, free) == expected


@pytest.mark.parametrize("minimum,maximum,free", [(-1, None, None), ("-20 руб", None, None),
    ("от -10", None, None), (100, 50, None), (100, None, True), ("300 руб", None, "true")])
def test_invalid_prices(minimum, maximum, free):
    with pytest.raises(ValueError):
        parse_price_fields(minimum, maximum, free)


def test_prices_through_import_search_bot_api(db):
    from fastapi.testclient import TestClient
    from src.api import create_app
    from src.bot.formatter import format_price_ru

    service = ImportService(db)
    _, unknown = service.create_event_manual(event())
    _, free = service.create_event_manual(event(title="Свободный концерт", price_min=0))
    _, paid = service.create_event_manual(event(title="Платный концерт", price_min=300, price_max=500))
    _, mixed = service.create_event_manual(event(title="Разные билеты", price_min=0, price_max=500))
    assert unknown.price_min is None and unknown.price_max is None and not unknown.is_free
    assert "Бесплатно" not in format_price_ru(unknown)
    search = SearchService(db)
    assert {e.id for e in search.search(SearchFilters(is_free_only=True))[0]} == {free.id}
    assert {e.id for e in search.search(SearchFilters(max_price=500))[0]} == {free.id, paid.id, mixed.id}
    with TestClient(create_app(db)) as client:
        payload = client.get(f"/events/{unknown.id}").json()
        assert payload["price_min"] is None and payload["price_max"] is None
        assert payload["is_free"] is False and len(payload["sources"]) == 1
        assert client.get("/events?is_free_only=true").json()["total"] == 1
        assert client.get("/events?max_price=0").json()["total"] == 2
        assert client.get("/events?limit=0").status_code == 422


def test_moderation_api_and_reimport(db):
    from fastapi.testclient import TestClient
    from src.api import create_app

    service = ImportService(db)
    _, record = service.create_event_manual(event(status="published"))
    with TestClient(create_app(db)) as client:
        for status in ("draft", "cancelled", "published"):
            service.update_event_status(record.id, status, actor="moderator:42")
            report = service.import_records([event(status="published")], "repeat")
            assert report.rejected_count == 0
            assert db.fetchone("SELECT status FROM events WHERE id = ?", (record.id,))["status"] == status
            assert client.get("/events").json()["total"] == (1 if status == "published" else 0)
            response = client.get(f"/events/{record.id}")
            assert response.status_code == (404 if status == "draft" else 200)
            assert client.get(f"/events/{record.id[:8]}").status_code == response.status_code
        rows = db.fetchall("SELECT * FROM audit_log WHERE action = 'change_status'")
        assert len(rows) == 3
        assert all(r["actor"] == "moderator:42" and r["created_at"] for r in rows)
        assert json.loads(rows[0]["changes_json"])["status"] == {"old": "published", "new": "draft"}


def test_archive_event_hides_without_deleting_sources_or_history(db):
    service = ImportService(db)
    _, record = service.create_event_manual(event(), actor="moderator:42")
    archived = service.archive_event(record.id, actor="admin:root")
    assert archived.status == "draft"
    assert db.fetchone("SELECT id FROM events WHERE id = ?", (record.id,))
    assert db.fetchone("SELECT event_id FROM event_sources WHERE event_id = ?", (record.id,))
    audit = db.fetchone("SELECT actor, action, changes_json FROM audit_log WHERE entity_id = ? AND action = 'archive_event'",
                        (record.id,))
    assert audit["actor"] == "admin:root"
    assert json.loads(audit["changes_json"])["archived"]["new"] is True


def test_all_changed_event_fields_audited(db):
    service = ImportService(db)
    _, initial = service.create_event_manual(event(price_min=100))
    report = service.import_records([event(price_min=100, tags=["семья"], language="башкирский",
                                          duration_min=90, org_link="https://example.com/tickets")], "update")
    assert report.updated_count == 1
    updated = SearchService(db).get_event_by_id(initial.id)
    assert updated.tags == ["семья"] and updated.duration_min == 90
    changes = json.loads(db.fetchone("SELECT changes_json FROM audit_log WHERE action = 'update_event'")["changes_json"])
    assert {"tags", "duration_min", "language", "org_link"} <= changes.keys()


def test_import_rolls_back_when_audit_fails(db, monkeypatch):
    service = ImportService(db)
    original = service.log_audit

    def fail_create(*args, **kwargs):
        if kwargs.get("action") == "create_event":
            raise RuntimeError("audit unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "log_audit", fail_create)
    report = service.import_records([event()], "failure")
    assert report.rejected_count == 1
    for table in ("events", "event_sources", "raw_events"):
        assert db.fetchall(f"SELECT * FROM {table}") == []
    assert db.fetchall("SELECT * FROM audit_log WHERE entity_type = 'event'") == []


def test_feedback_service_and_public_submission(db):
    from fastapi.testclient import TestClient
    from src.api import create_app
    from src.services.feedback_service import FeedbackService

    _, record = ImportService(db).create_event_manual(event())
    service = FeedbackService(db)
    request = service.submit(" Исправьте время ", record.id, actor="organizer:1")
    assert request.message == "Исправьте время" and request.status == "pending"
    service.update_status(request.id, "in_progress", actor="moderator:42")
    assert service.list_requests("in_progress")[0].id == request.id
    assert service.update_status(request.id, "resolved", actor="moderator:42").status == "resolved"
    with pytest.raises(ValueError):
        service.submit("  ")
    with pytest.raises(LookupError):
        service.submit("Сообщение", "missing")
    with pytest.raises(ValueError):
        service.update_status(request.id, "anything", actor="moderator:42")
    with TestClient(create_app(db)) as client:
        assert client.post("/feedback", json={"message": "Добавьте событие"}).status_code == 201
        assert client.post("/feedback", json={"message": " "}).status_code == 422
        ImportService(db).update_event_status(record.id, "draft")
        assert client.post("/feedback", json={"message": "Тест", "event_id": record.id}).status_code == 404
    assert len(db.fetchall("SELECT * FROM audit_log WHERE entity_type = 'feedback'")) == 4


def insert_legacy(db, item, *, event_id, raw_id):
    # Reproduce the original normalizer's lossy conversion without modifying 001.
    norm = ImportService(db)._validate_and_normalize(item)
    norm = {k: v for k, v in norm.items() if not k.startswith("_")}
    norm.update(id=event_id, raw_event_id=raw_id, created_at="2026-01-01", updated_at="2026-01-01")
    norm["status"] = item.get("status", "опубликовано")
    norm["tags"] = json.dumps(norm["tags"])
    norm["price_min"] = norm["price_min"] or 0
    norm["price_max"] = norm["price_max"] or 0
    norm["is_free"] = int(norm["price_min"] == norm["price_max"] == 0)
    norm["is_disputed"] = int(norm["is_disputed"])
    db.execute("INSERT INTO raw_events (id, source_type, source_name, source_hash, raw_payload_json, ingested_at) VALUES (?, ?, ?, ?, ?, ?)",
               (raw_id, "импорт", "legacy", norm["source_hash"], json.dumps(item), "2026-01-01"))
    db.execute("INSERT INTO events (" + ", ".join(norm) + ") VALUES (" + ", ".join("?" for _ in norm) + ")", tuple(norm.values()))


def _columns(db, table):
    if db.is_postgres:
        return {row["column_name"] for row in db.fetchall(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?",
            (table,))}
    return {row["name"] for row in db.fetchall(f"PRAGMA table_info({table})")}


def _table_exists(db, table):
    if db.is_postgres:
        return bool(db.fetchone("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename = ?", (table,)))
    return bool(db.fetchone("SELECT name FROM sqlite_master WHERE name = ?", (table,)))


def test_fresh_baseline_is_atomic_and_repeatable(empty_db, tmp_path):
    db = empty_db
    assert apply_migrations(db, MIGRATIONS) == 3
    assert apply_migrations(db, MIGRATIONS) == 0
    assert {row["version"] for row in db.fetchall("SELECT version FROM schema_migrations")} == {"001_baseline.sql", "002_event_submissions.sql", "002_legacy_compat.sql"}
    columns = _columns(db, "bot_profiles")
    assert "phone" not in columns
    assert {"period_from", "period_to"}.issubset(_columns(db, "subscriptions"))


def test_legacy_history_refused_without_data_loss(empty_db):
    db = empty_db
    db.execute("CREATE TABLE preserved (id TEXT PRIMARY KEY)")
    db.execute("INSERT INTO preserved VALUES ('keep')")
    with pytest.raises(RuntimeError, match="Existing database"):
        apply_migrations(db, MIGRATIONS)
    assert db.fetchone("SELECT id FROM preserved")["id"] == "keep"
    assert not _table_exists(db, "events")


def test_legacy_006_marker_does_not_replay_baseline(db):
    _, record = ImportService(db).create_event_manual(event())
    db.execute("UPDATE schema_migrations SET version = '006_unified_users.sql' WHERE version = '002_event_submissions.sql'")
    with pytest.raises(RuntimeError, match="Legacy migration history"):
        apply_migrations(db, MIGRATIONS)
    assert db.fetchone("SELECT id FROM events WHERE id = ?", (record.id,))["id"] == record.id
    assert db.fetchone("SELECT version FROM schema_migrations WHERE version = '006_unified_users.sql'")["version"] == "006_unified_users.sql"


def test_failed_baseline_is_atomic(empty_db, tmp_path):
    db = empty_db
    (tmp_path / "001_baseline.sql").write_text(
        "CREATE TABLE transient (id TEXT); INVALID SQL;", encoding="utf-8")
    with pytest.raises(Exception):
        apply_migrations(db, tmp_path)
    assert not _table_exists(db, "transient")


def test_concurrent_import_is_idempotent(db):
    from concurrent.futures import ThreadPoolExecutor

    def run(number):
        return ImportService(db).import_records(
            [event(source_url=f"https://example.com/{number % 2}")], f"concurrent-{number}"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(executor.map(run, range(8)))
    assert sum(r.added_count for r in reports) == 1
    assert sum(r.rejected_count for r in reports) == 0
    assert len(db.fetchall("SELECT * FROM events WHERE merged_into IS NULL")) == 1
    assert len(db.fetchall("SELECT * FROM event_sources")) == 2
    assert len(db.fetchall("SELECT * FROM raw_events")) == 2


def test_merge_preserves_sources_feedback_and_old_links(db):
    from src.services.feedback_service import FeedbackService

    service = ImportService(db)
    _, first = service.create_event_manual(event())
    _, second = service.create_event_manual(event(title="Другое название", source_url="https://example.com/two"))
    request = FeedbackService(db).submit("Исправление", second.id)
    service.update_event_status(second.id, "cancelled")
    # A corrected source bridges two previously separate identities.
    report = service.import_records([event(id=second.id, source_url="https://example.com/two")], "correction")
    assert report.rejected_count == report.added_count == 0
    canonical = SearchService(db).get_event_by_id(first.id)
    assert SearchService(db).get_event_by_id(second.id).id == canonical.id
    assert canonical.status == "cancelled" and len(canonical.sources) == 2
    assert FeedbackService(db).list_requests()[0].event_id == canonical.id
    assert db.fetchone("SELECT id FROM feedback WHERE id = ?", (request.id,))
    assert db.fetchone("SELECT * FROM audit_log WHERE action = 'merge_event'")
    assert SearchService(db).search(SearchFilters())[1] == 0


def test_reprocess_preserves_sources_and_moderation(db):
    service = ImportService(db)
    report = service.import_records([event(), event(source_url="https://example.com/two")], "multi")
    assert report.rejected_count == 0
    record = SearchService(db).search(SearchFilters())[0][0]
    service.update_event_status(record.id, "draft", actor="human")
    assert service.reprocess_raw_events() == 2
    assert SearchService(db).get_event_by_id(record.id) is None
    assert len(service.list_sources(record.id)) == 2
    assert len(db.fetchall("SELECT * FROM events")) == 1
    assert not db.fetchone("SELECT is_disputed FROM events WHERE id = ?", (record.id,))["is_disputed"]


def test_partial_and_zero_range_prices_are_not_free(db):
    from src.bot.formatter import format_price_ru

    _, upper_only = ImportService(db).create_event_manual(event(price_max=500))
    _, zero_range = ImportService(db).create_event_manual(event(title="Диапазон цен", price_min=0, price_max=500))
    assert format_price_ru(upper_only) == "до 500 ₽"
    assert format_price_ru(zero_range) == "до 500 ₽"
    search = SearchService(db)
    assert search.search(SearchFilters(is_free_only=True))[1] == 0
    assert search.search(SearchFilters(max_price=500))[1] == 2


def test_explicit_source_verification_is_saved_and_audited(db):
    service = ImportService(db)
    _, record = service.create_event_manual(event(verified_at="2026-11-01T10:00:00+00:00"))
    updated = event(verified_at="2026-11-02T10:00:00+00:00", source_type="ручной ввод")
    report = service.import_records([updated], "verify")
    assert report.updated_count == 1
    assert service.list_sources(record.id)[0].verified_at == updated["verified_at"]
    assert service.import_records([updated], "repeat").unchanged_count == 1
    assert db.fetchone("SELECT * FROM audit_log WHERE action = 'update_source'")


def test_feedback_status_rolls_back_on_audit_failure(db, monkeypatch):
    from src.services.feedback_service import FeedbackService

    service = FeedbackService(db)
    request = service.submit("Сообщение организатора")

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service.audit, "log_audit", fail)
    with pytest.raises(RuntimeError):
        service.update_status(request.id, "resolved", actor="moderator")
    assert service.list_requests()[0].status == "pending"


def test_cli_missing_price_is_unknown(db, monkeypatch, capsys):
    import sys
    import src.cli as cli

    monkeypatch.setattr(cli, "init_db_and_services", lambda: (None, db, ImportService(db), SearchService(db)))
    args = ["afisha", "add-manual"]
    for key, value in event().items():
        args.extend(["--" + key.replace("_", "-"), value])
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == 0
    assert "Стоимость уточняется" in capsys.readouterr().out
    assert db.fetchone("SELECT price_min FROM events")["price_min"] is None


def test_moderation_rolls_back_on_audit_failure(db, monkeypatch):
    service = ImportService(db)
    _, record = service.create_event_manual(event())

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service, "log_audit", fail)
    with pytest.raises(RuntimeError):
        service.update_event_status(record.id, "draft", actor="moderator")
    assert SearchService(db).get_event_by_id(record.id).status == "published"


def test_bot_draft_access_respects_admin(db):
    from src.bot.router import BotRouter
    from src.config import Settings

    _, record = ImportService(db).create_event_manual(event(status="draft"))
    settings = Settings()
    settings.admin_user_ids = ["admin"]
    router = BotRouter(db, settings)
    assert record.title not in router.handle_message("public", f"/event_{record.id}").text
    assert "СТАТУС: ЧЕРНОВИК" in router.handle_message("admin", f"/event_{record.id}").text
