"""Stage 5.3 API regressions for admin imports, reports, audit, and visibility."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.user_service import UserService
from tests.test_backend import event
from tests.test_unified_users import signed


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "stage53-isolated.db"))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield database
    finally:
        database.close()


def login(client):
    response = client.post("/api/v1/admin/login", json={
        "username": "reviewer", "password": "test-password",
    })
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies.get("admin_csrf")}


def test_admin_import_api_report_repeat_and_public_rest_consistency(db):
    settings = Settings(admin_username="reviewer", admin_password="test-password",
                        telegram_bot_token="test-token", admin_cookie_secure=False)
    record = event(title="Импортированный концерт", source_url="https://source.example/import-53")
    rows = [record, {**event(title="Некорректное событие"), "date_start": "не дата"}]
    with TestClient(create_app(db, settings)) as client:
        headers = login(client)
        first = client.post("/api/v1/admin/import?filename=stage53.json", headers=headers,
                            content=json.dumps(rows, ensure_ascii=False).encode("utf-8"))
        assert first.status_code == 200, first.text
        report = first.json()
        assert (report["total_rows"], report["added_count"], report["updated_count"],
                report["rejected_count"]) == (2, 1, 0, 1)
        assert report["rejected_reasons"] and report["rejected_reasons"][0]["reason"]

        repeated = client.post("/api/v1/admin/import?filename=stage53.json", headers=headers,
                               content=json.dumps(rows, ensure_ascii=False).encode("utf-8"))
        assert repeated.status_code == 200
        assert (repeated.json()["added_count"], repeated.json()["unchanged_count"],
                repeated.json()["rejected_count"]) == (0, 1, 1)
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 1

        event_id = client.get("/api/v1/admin/events").json()["events"][0]["id"]
        assert client.get("/api/v1/events").json()["total"] == 1
        assert client.get(f"/api/v1/events/{event_id}").json()["title"] == record["title"]

        assert client.patch(f"/api/v1/admin/events/{event_id}", headers=headers,
                            json={"title": "Отредактированный импорт"}).status_code == 200
        assert client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                            json={"status": "published"}).status_code == 200
        assert client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                            json={"status": "cancelled"}).status_code == 200
        proposal = client.post("/api/v1/event-submissions", json=event(
            title="Предложение для аудита", source_url="https://source.example/audit-53")).json()
        reviewed = client.patch(f"/api/v1/admin/submissions/{proposal['id']}", headers=headers,
                                json={"status": "rejected", "note": "Проверено"})
        assert reviewed.status_code == 200

        audit = client.get("/api/v1/admin/audit").json()
        imported = [row for row in audit if row["action"] == "import_batch"]
        assert len(imported) == 2
        assert imported[0]["actor"] == "admin:reviewer"
        assert imported[0]["entity_type"] == "import_report"
        assert imported[0]["created_at"]
        assert {"create_event", "update_event", "change_status", "review_submission"}.issubset(
            {row["action"] for row in audit}
        )
        assert all(row["actor"] and row["created_at"] and row["entity_id"] for row in audit)


def test_admin_import_encoding_errors_and_role_boundary(db):
    settings = Settings(admin_username="reviewer", admin_password="test-password",
                        telegram_bot_token="test-token", admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        headers = login(client)
        invalid_encoding = client.post("/api/v1/admin/import?filename=bad.json", headers=headers,
                                       content=b'[{"title":"\xff"}]')
        assert invalid_encoding.status_code == 422

        invalid_fields = client.post("/api/v1/admin/import?filename=bad.json", headers=headers,
                                     content=json.dumps([{"title": "missing fields"}]).encode())
        assert invalid_fields.status_code == 200
        assert invalid_fields.json()["rejected_count"] == 1
        assert invalid_fields.json()["rejected_reasons"][0]["reason"]

        # Import and audit are restricted to admin even though moderators can manage events.
        moderator = UserService(db).telegram_user("530", "Модератор")
        db.execute("UPDATE users SET role = 'moderator' WHERE id = ?", (moderator["id"],))
        client.cookies.clear()
        auth = client.post("/api/v1/auth/telegram", json=signed(530, "test-token"))
        assert auth.status_code == 200
        assert client.get("/api/v1/admin/events").status_code == 200
        assert client.get("/api/v1/admin/audit").status_code == 403
        denied_import = client.post("/api/v1/admin/import?filename=x.json", content=b"[]")
        assert denied_import.status_code == 403

