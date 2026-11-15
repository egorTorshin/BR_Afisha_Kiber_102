"""Isolated regression scenarios for the event lifecycle and audit trail."""

import json

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.services.import_service import ImportService
from src.services.search_service import SearchService
from tests.test_backend import db, empty_db, event


def _login(client):
    response = client.post("/api/v1/admin/login", json={
        "username": "reviewer", "password": "test-password",
    })
    assert response.status_code == 200, response.text
    return {cookie.name: cookie.value for cookie in client.cookies.jar}["admin_csrf"]


def test_admin_draft_publish_edit_cancel_lifecycle_and_audit(db):
    settings = Settings(admin_username="reviewer", admin_password="test-password", admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        response = client.post("/api/v1/admin/events", headers=headers, json=event(
            title="Событие этапа 2.3", source_url="https://lifecycle.example/event",
        ))
        assert response.status_code == 201, response.text
        event_id = response.json()["id"]
        assert response.json()["status"] == "draft"
        assert client.get("/api/v1/events").json()["total"] == 0
        assert client.get(f"/api/v1/events/{event_id}").status_code == 404

        published = client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                                 json={"status": "published"})
        assert published.status_code == 200 and published.json()["status"] == "published"
        edited = client.patch(f"/api/v1/admin/events/{event_id}", headers=headers,
                              json={"title": "Отредактированное событие"})
        assert edited.status_code == 200 and edited.json()["title"] == "Отредактированное событие"
        cancelled = client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                                 json={"status": "cancelled"})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert client.get("/api/v1/events").json()["total"] == 0
        direct = client.get(f"/api/v1/events/{event_id}")
        assert direct.status_code == 200 and direct.json()["status"] == "cancelled"
        from src.bot.formatter import format_event_card
        assert "МЕРОПРИЯТИЕ ОТМЕНЕНО" in format_event_card(SearchService(db).get_event_by_id(event_id))
        from src.bot.router import BotRouter
        bot = BotRouter(db, settings)
        bot_card = bot.handle_message("reader", f"/event_{event_id}")
        assert "МЕРОПРИЯТИЕ ОТМЕНЕНО" in bot_card.text
        assert client.get("/api/v1/events", params={"query_text": "Отредактированное событие"}).json()["total"] == 0

        rows = db.fetchall("SELECT actor, action, entity_id, changes_json, created_at FROM audit_log WHERE entity_id = ? ORDER BY created_at, id", (event_id,))
        assert [(row["actor"], row["action"]) for row in rows] == [
            ("admin:reviewer", "add_source"),
            ("admin:reviewer", "create_event"),
            ("admin:reviewer", "change_status"),
            ("admin:reviewer", "update_event"),
            ("admin:reviewer", "change_status"),
        ]
        assert all(row["created_at"] and row["entity_id"] == event_id for row in rows)
        assert json.loads(rows[3]["changes_json"])["title"] == {
            "old": "Событие этапа 2.3", "new": "Отредактированное событие",
        }


@pytest.mark.parametrize("value", ["archived", "deleted", "PUBLISHED", ""])
def test_status_endpoint_rejects_values_outside_contract_without_mutation(db, value):
    _, record = ImportService(db).create_event_manual(event(), actor="fixture")
    settings = Settings(admin_username="reviewer", admin_password="test-password", admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        csrf = _login(client)
        response = client.patch(f"/api/v1/admin/events/{record.id}/status",
                                headers={"X-CSRF-Token": csrf}, json={"status": value})
    assert response.status_code == 422
    saved = db.fetchone("SELECT status, status_locked FROM events WHERE id = ?", (record.id,))
    assert (saved["status"], saved["status_locked"]) == ("published", 0)
    assert db.fetchall("SELECT * FROM audit_log WHERE entity_id = ? AND action = 'change_status'", (record.id,)) == []


def test_regular_telegram_user_cannot_edit_publish_or_archive_event(db):
    _, record = ImportService(db).create_event_manual(event(), actor="fixture")
    settings = Settings(telegram_bot_token="test-token", admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        from tests.test_unified_users import signed
        login = client.post("/api/v1/auth/telegram", json=signed(2345, "test-token"))
        assert login.status_code == 200 and login.json()["role"] == "user"
        headers = {"Origin": settings.web_origin, "X-CSRF-Token": client.cookies.get("user_csrf")}
        url = f"/api/v1/admin/events/{record.id}"
        assert client.patch(f"{url}/status", headers=headers, json={"status": "cancelled"}).status_code == 403
        assert client.patch(url, headers=headers, json={"title": "Несанкционировано"}).status_code == 403
        assert client.delete(url, headers=headers).status_code == 403
    assert db.fetchone("SELECT status, title FROM events WHERE id = ?", (record.id,))["status"] == "published"
    assert db.fetchall("SELECT * FROM audit_log WHERE entity_id = ? AND action IN ('change_status', 'update_event', 'archive_event')", (record.id,)) == []


@pytest.mark.parametrize("backend", ["memory", "sqlite_file"])
def test_edit_cancel_and_merge_preserve_source_links_and_audit(backend, tmp_path):
    from src.db.connection import Database
    from src.db.migrations import apply_migrations
    from pathlib import Path

    path = ":memory:" if backend == "memory" else str(tmp_path / "stage23.db")
    database = Database(path)
    try:
        apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
        service = ImportService(database)
        first = event(title="Сохраняемое событие", source_url="https://source.example/one")
        other_source = {**first, "source_url": "https://source.example/two"}
        report = service.import_records([first, other_source], "fixture.json", actor="collector")
        assert report.added_count == 1 and report.updated_count == 1
        original = database.fetchone("SELECT id FROM events")
        service.update_event_fields(original["id"], {"description": "Описание после правки."}, "moderator:7")
        service.update_event_status(original["id"], "cancelled", "moderator:7")
        assert {r["source_url"] for r in database.fetchall(
            "SELECT source_url FROM event_sources WHERE event_id = ?", (original["id"],)
        )} == {first["source_url"], other_source["source_url"]}
        assert SearchService(database).get_event_by_id(original["id"]).status == "cancelled"
        assert database.fetchone("SELECT COUNT(*) AS n FROM audit_log WHERE entity_id = ?", (original["id"],))["n"] >= 3

        target = event(title="Каноническое мероприятие", source_url="https://source.example/canonical")
        duplicate = event(title="Дубль мероприятия", source_url="https://source.example/duplicate")
        service.import_records([target, duplicate], "duplicates.json", actor="collector")
        ids = database.fetchall("SELECT id FROM events WHERE title IN (?, ?)",
                                (target["title"], duplicate["title"]))
        target_id, duplicate_id = [r["id"] for r in ids]
        service.merge_events(target_id, duplicate_id, actor="admin:root")
        assert database.fetchone("SELECT merged_into FROM events WHERE id = ?", (duplicate_id,))["merged_into"] == target_id
        assert {r["source_url"] for r in database.fetchall(
            "SELECT source_url FROM event_sources WHERE event_id = ?", (target_id,)
        )} == {target["source_url"], duplicate["source_url"]}
        audit = database.fetchone("SELECT actor, action, changes_json FROM audit_log WHERE entity_id = ? AND action = 'merge_event'", (target_id,))
        assert audit["actor"] == "admin:root"
        assert json.loads(audit["changes_json"])["sources"]
    finally:
        database.close()
