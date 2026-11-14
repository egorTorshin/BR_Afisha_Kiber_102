from pathlib import Path
from types import SimpleNamespace
import sys

import httpx
import pytest

from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.gkz_collector import GKZCollector, USER_AGENT
from src.services.import_service import ImportService


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "fixtures/gkz/afisha.html").read_text(encoding="utf-8")
MIGRATIONS = ROOT / "migrations"


@pytest.fixture
def importer():
    db = Database(":memory:")
    apply_migrations(db, MIGRATIONS)
    try:
        yield db, ImportService(db)
    finally:
        db.close()


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_success_downloads_and_imports_as_drafts(importer):
    db, service = importer
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=HTML)

    client = client_for(handler)
    result = GKZCollector(client=client).collect(service)
    client.close()

    assert result.ok
    assert result.report and result.report.added_count == 57
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert {row["status"] for row in db.fetchall("SELECT status FROM events")} == {"draft"}


def test_dry_run_validates_without_writing_to_database(importer):
    db, _ = importer

    def handler(request):
        return httpx.Response(200, text=HTML, request=request)

    client = client_for(handler)
    result = GKZCollector(client=client).dry_run()
    client.close()

    assert result.ok
    assert result.stats["http_status"] == 200
    assert result.stats["html_bytes"] == len(HTML.encode("utf-8"))
    assert result.stats["cards_found"] == 57
    assert result.stats["parsed"] == 57
    assert result.stats["rejected"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM import_reports")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM raw_events")["n"] == 0


def test_full_import_cycle_on_isolated_sqlite_snapshot(tmp_path):
    db_path = tmp_path / "gkz-integration.sqlite"
    db = Database(str(db_path))
    apply_migrations(db, MIGRATIONS)
    service = ImportService(db)

    def handler(request):
        return httpx.Response(200, text=HTML, request=request)

    client = client_for(handler)
    collector = GKZCollector(client=client)
    try:
        first = collector.collect(service)
        assert first.ok and first.report
        assert first.report.added_count == 57
        assert first.report.rejected_count == 0
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 57
        assert db.fetchone("SELECT COUNT(*) AS n FROM raw_events")["n"] == 57
        assert db.fetchone("SELECT COUNT(*) AS n FROM event_sources")["n"] == 57
        assert db.fetchone("SELECT COUNT(*) AS n FROM import_reports")["n"] == 1
        assert {row["status"] for row in db.fetchall("SELECT status FROM events")} == {"draft"}

        moderated_id = db.fetchone("SELECT id FROM events ORDER BY id LIMIT 1")["id"]
        db.execute("UPDATE events SET status = 'published', status_locked = 1 WHERE id = ?", (moderated_id,))
        second = collector.collect(service)

        assert second.ok and second.report
        assert second.report.added_count == 0
        assert second.report.updated_count == 0
        assert second.report.unchanged_count == 57
        assert second.report.rejected_count == 0
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 57
        assert db.fetchone("SELECT COUNT(*) AS n FROM raw_events")["n"] == 57
        assert db.fetchone("SELECT COUNT(*) AS n FROM event_sources")["n"] == 57
        assert db.fetchone("SELECT COUNT(*) AS n FROM import_reports")["n"] == 2
        rows = db.fetchall("SELECT status FROM events")
        assert rows.count({"status": "published"}) == 1
        assert rows.count({"status": "draft"}) == 56
    finally:
        client.close()
        db.close()


def test_timeout_retries_then_leaves_database_unchanged(importer):
    db, service = importer
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    client = client_for(handler)
    result = GKZCollector(client=client, retries=2).collect(service)
    client.close()

    assert not result.ok
    assert "Таймаут" in result.message
    assert len(calls) == 3
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM import_reports")["n"] == 0


def test_http_error_does_not_import(importer):
    db, service = importer
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, request=request)

    client = client_for(handler)
    result = GKZCollector(client=client, retries=2).collect(service)
    client.close()

    assert not result.ok
    assert "HTTP 404" in result.message
    assert len(calls) == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 0


def test_temporary_http_error_retries_and_repeated_run_is_idempotent(importer):
    db, service = importer
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=HTML, request=request)

    client = client_for(handler)
    collector = GKZCollector(client=client, retries=2)
    first = collector.collect(service)
    second = collector.collect(service)
    client.close()

    assert first.ok and second.ok
    assert first.report and first.report.added_count == 57
    assert second.report and second.report.added_count == 0
    assert second.report.unchanged_count == 57
    assert len(calls) == 3
    assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 57


def test_repeat_preserves_existing_moderated_status(importer):
    db, service = importer

    def handler(request):
        return httpx.Response(200, text=HTML, request=request)

    client = client_for(handler)
    collector = GKZCollector(client=client)
    assert collector.collect(service).ok
    db.execute("UPDATE events SET status = 'published', status_locked = 1 WHERE id = (SELECT id FROM events LIMIT 1)")
    assert collector.collect(service).ok
    client.close()

    rows = db.fetchall("SELECT status FROM events")
    assert rows.count({"status": "published"}) == 1
    assert rows.count({"status": "draft"}) == 56


def test_cli_exposes_one_shot_collection_command(importer, monkeypatch, capsys):
    import src.cli as cli

    _, service = importer
    calls = {}

    class FakeCollector:
        def __init__(self, *, timeout, retries):
            calls["options"] = (timeout, retries)

        def collect(self, importer, *, actor):
            calls["actor"] = actor
            return SimpleNamespace(ok=True, message="Сбор завершён.", report=None)

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(cli, "init_db_and_services", lambda: (None, None, service, None))
    monkeypatch.setattr("src.services.gkz_collector.GKZCollector", FakeCollector)
    monkeypatch.setattr(sys, "argv", ["afisha", "collect-gkz", "--timeout", "4", "--retries", "1"])

    assert cli.main() == 0
    assert "Сбор завершён." in capsys.readouterr().out
    assert calls == {"options": (4.0, 1), "actor": "gkz_collector", "closed": True}


def test_cli_dry_run_does_not_initialize_database(monkeypatch, capsys):
    import src.cli as cli

    calls = []

    class FakeCollector:
        def __init__(self, *, timeout, retries):
            calls.append(("init", timeout, retries))

        def dry_run(self):
            return SimpleNamespace(ok=True, message="dry-run готово", stats={
                "http_status": 200, "html_bytes": 12, "cards_found": 2,
                "parsed": 1, "rejected": 1,
                "rejected_reasons": [{"title": "bad", "reason": "нет даты"}],
            })

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(cli, "init_db_and_services", lambda: (_ for _ in ()).throw(AssertionError("DB initialized")))
    monkeypatch.setattr("src.services.gkz_collector.GKZCollector", FakeCollector)
    monkeypatch.setattr(sys, "argv", ["afisha", "collect-gkz", "--dry-run"])

    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "карточек: 2" in output
    assert "нет даты" in output
    assert calls == [("init", 15.0, 2), ("close",)]


def test_isolated_cli_database_url_rejects_non_temporary_sqlite_paths(tmp_path):
    from src.cli import init_sqlite_services

    with pytest.raises(ValueError, match="временном каталоге"):
        init_sqlite_services(f"sqlite:///{ROOT / 'data' / 'not-temporary.db'}")
    with pytest.raises(ValueError, match="только SQLite"):
        init_sqlite_services("postgresql://production.example/catalog")
