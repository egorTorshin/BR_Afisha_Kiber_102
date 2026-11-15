"""Cross-component regression: admin publication reaches the public catalog and both bots."""

from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from src.api import create_app
from src.bot.max.adapter import MaxAdapter
from src.bot.router import BotRouter
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from tests.test_backend import event


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "stage821.db"))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield database
    finally:
        database.close()


def test_admin_published_event_is_consistent_across_rest_telegram_and_max(db):
    settings = Settings(admin_username="reviewer", admin_password="test-password",
                        admin_cookie_secure=False, rate_limit_per_minute=1000)
    with TestClient(create_app(db, settings)) as client:
        assert client.post("/api/v1/admin/login", json={
            "username": "reviewer", "password": "test-password",
        }).status_code == 200
        csrf = client.cookies.get("admin_csrf")
        headers = {"X-CSRF-Token": csrf}
        created = client.post("/api/v1/admin/events", headers=headers, json=event(
            title="Интеграционный концерт", city="Уфа", category="концерт",
            source_url="https://source.example/integration",
        ))
        assert created.status_code == 201, created.text
        event_id = created.json()["id"]
        assert created.json()["status"] == "draft"
        assert client.patch(f"/api/v1/admin/events/{event_id}/status", headers=headers,
                            json={"status": "published"}).status_code == 200

        page = client.get("/api/v1/events", params={
            "city": "Уфа", "category": "концерт", "query_text": "интеграционный",
        }).json()
        assert page["total"] == 1
        assert [item["id"] for item in page["events"]] == [event_id]
        card = client.get(f"/api/v1/events/{event_id}").json()
        assert card["source_url"] == "https://source.example/integration"

        telegram = BotRouter(db, settings)
        max_bot = BotRouter(db, settings, platform="max")
        assert "Интеграционный концерт" in telegram.handle_message("reader", "/search").text
        dispatched = MaxAdapter(max_bot).dispatch({
            "update_type": "message_created",
            "message": {"sender": {"user_id": "reader"},
                        "recipient": {"chat_id": 22}, "body": {"text": "/search", "mid": "m-821"}},
        })
        assert dispatched is not None
        assert "Интеграционный концерт" in dispatched[1].text

