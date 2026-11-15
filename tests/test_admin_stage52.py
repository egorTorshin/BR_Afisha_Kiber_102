"""Stage 5.2 regression coverage for admin event management and moderation."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from src.services.user_service import UserService
from tests.test_backend import event
from tests.test_unified_users import signed


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "stage52.db"))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield database
    finally:
        database.close()


def admin_client(db):
    settings = Settings(admin_username="reviewer", admin_password="test-password",
                        telegram_bot_token="test-token", admin_cookie_secure=False)
    client = TestClient(create_app(db, settings))
    response = client.post("/api/v1/admin/login", json={
        "username": "reviewer", "password": "test-password",
    })
    assert response.status_code == 200
    return client, {"X-CSRF-Token": client.cookies.get("admin_csrf")}


def test_admin_create_validation_and_rest_public_catalog_contract(db):
    client, headers = admin_client(db)
    with client:
        assert client.post("/api/v1/admin/events", headers=headers,
                           json={"title": "X", "unexpected": True}).status_code == 422
        assert client.post("/api/v1/admin/events", headers=headers,
                           json={"title": "Нет обязательных полей"}).status_code == 422
        assert client.post("/api/v1/admin/events", headers=headers,
                           json={**event(), "price_min": -1}).status_code == 422
        made = client.post("/api/v1/admin/events", headers=headers, json=event())
        assert made.status_code == 201, made.text
        body = made.json()
        assert body["status"] == "draft"
        event_id = body["id"]
        assert client.get("/api/v1/admin/events").json()["events"][0]["id"] == event_id
        assert client.get("/api/v1/events").json()["total"] == 0
        published = client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                                 json={"status": "published"})
        assert published.status_code == 200
        public = client.get(f"/api/v1/events/{event_id}")
        assert public.status_code == 200
        assert public.json()["title"] == body["title"]
        edited = client.patch(f"/api/v1/admin/events/{event_id}", headers=headers,
                              json={"title": "Новое название", "description": "Обновленное описание."})
        assert edited.status_code == 200
        assert client.get(f"/api/v1/events/{event_id}").json()["title"] == "Новое название"
        invalid = client.patch(f"/api/v1/admin/events/{event_id}", headers=headers,
                               json={"title": "x"})
        assert invalid.status_code == 422
        assert client.get(f"/api/v1/events/{event_id}").json()["title"] == "Новое название"


def test_status_repeats_archive_sources_and_audit_are_consistent(db):
    client, headers = admin_client(db)
    with client:
        made = client.post("/api/v1/admin/events", headers=headers, json=event()).json()
        event_id = made["id"]
        updated = client.patch(f"/api/v1/admin/events/{event_id}", headers=headers,
                               json={"title": "Новое название"})
        assert updated.status_code == 200
        for status, expected_public in (("published", 1), ("draft", 0), ("cancelled", 0)):
            response = client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                                    json={"status": status})
            assert response.status_code == 200
            assert client.get("/api/v1/events").json()["total"] == expected_public
        assert client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                           json={"status": "cancelled"}).status_code == 200
        archived = client.delete(f"/api/v1/admin/events/{event_id}", headers=headers)
        assert archived.status_code == 200
        assert db.fetchone("SELECT source_type FROM event_sources WHERE event_id = ?", (event_id,))["source_type"] == "ручной ввод"
        audit = db.fetchall("SELECT actor, action, changes_json FROM audit_log WHERE entity_id = ? ORDER BY id", (event_id,))
        assert any(row["action"] == "archive_event" for row in audit)
        assert all(row["actor"] == "admin:reviewer" for row in audit)
        assert json.loads(next(row["changes_json"] for row in audit if row["action"] == "update_event"))["title"]["new"] == "Новое название"
        assert client.delete(f"/api/v1/admin/events/{event_id}", headers=headers).status_code == 200


def test_moderation_disputed_items_approval_rejection_and_role_boundary(db):
    settings = Settings(telegram_bot_token="test-token", admin_cookie_secure=False, admin_user_ids=["1"])
    # Imported disputed card appears in the moderation queue with its source.
    _, record = ImportService(db).create_event_manual(event(title="Спорная карточка"), actor="collector")
    db.execute("UPDATE events SET is_disputed = 1, dispute_reason = 'Разные даты' WHERE id = ?", (record.id,))
    with TestClient(create_app(db, settings)) as client:
        proposal = client.post("/api/v1/event-submissions", json=event(title="Предложение к модерации")).json()
        admin = client.post("/api/v1/auth/telegram", json=signed(1, "test-token"))
        assert admin.json()["role"] == "admin"
        csrf = client.cookies.get("user_csrf")
        assert client.get("/api/v1/admin/moderation").json()[0]["id"] == record.id
        queue = client.get("/api/v1/admin/submissions").json()
        assert [row["id"] for row in queue] == [proposal["id"]]
        assert client.get("/api/v1/admin/settings").status_code == 200
        # Admin has access to admin-only routes; moderator does not.
        client.cookies.clear()
        moderator = UserService(db).telegram_user("2", "Модератор")
        db.execute("UPDATE users SET role = 'moderator' WHERE id = ?", (moderator["id"],))
        assert client.post("/api/v1/auth/telegram", json=signed(2, "test-token")).json()["role"] == "moderator"
        assert client.get("/api/v1/admin/events").status_code == 200
        assert client.get("/api/v1/admin/moderation").status_code == 200
        assert client.get("/api/v1/admin/settings").status_code == 403
        assert client.get("/api/v1/admin/audit").status_code == 403
        csrf = client.cookies.get("user_csrf")
        path = f"/api/v1/admin/submissions/{proposal['id']}"
        assert client.patch(path, json={"status": "approved"}, headers={
            "Origin": settings.web_origin, "X-CSRF-Token": csrf,
        }).status_code == 200
        assert client.patch(path, json={"status": "rejected"}, headers={
            "Origin": settings.web_origin, "X-CSRF-Token": csrf,
        }).status_code == 422
        rejected = client.post("/api/v1/event-submissions", json=event(title="Отклоняемое предложение")).json()
        assert client.patch(f"/api/v1/admin/submissions/{rejected['id']}", json={"status": "rejected"}, headers={
            "Origin": settings.web_origin, "X-CSRF-Token": csrf,
        }).status_code == 200

