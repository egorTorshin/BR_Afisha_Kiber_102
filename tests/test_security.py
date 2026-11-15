"""Regression tests for the exposed security boundaries."""

from datetime import datetime, timedelta, timezone
import pytest

from fastapi.testclient import TestClient

from src.api import create_app
from src.bootstrap import prune
from src.config import Settings
from src.services.feedback_service import FeedbackService
from src.services.admin_auth import hash_password
from src.services.import_service import ImportService
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.models.schemas import SearchFilters
from src.bot.router import BotRouter
from src.bot.formatter import format_search_results
from tests.test_backend import db, empty_db, event
from tests.test_api_level2 import catalog


def _settings(**overrides):
    return Settings(admin_username="reviewer", admin_password="test-password",
                    admin_cookie_secure=True, **overrides)


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("admin_csrf")}


def test_admin_responses_are_never_publicly_cacheable(db, catalog):
    with TestClient(create_app(db, _settings()), base_url="https://testserver") as client:
        public_event = client.get(f"/api/v1/events/{catalog['free'].id}").json()
        assert not {"source_hash", "dedup_key", "raw_event_id", "normalized_title"} & public_event.keys()
        assert client.post("/api/v1/admin/login", json={"username": "reviewer", "password": "test-password"}).status_code == 200
        for path in ("session", "events", "moderation", "audit", "feedback"):
            response = client.get(f"/api/v1/admin/{path}")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/events").headers["cache-control"] == "no-store"


def test_logout_revokes_replayed_session_and_moderator_permissions(db, catalog):
    settings = _settings(moderator_username="editor", moderator_password="editor-password")
    with TestClient(create_app(db, settings), base_url="https://testserver") as client:
        assert client.post("/api/v1/admin/login", json={"username": "editor", "password": "editor-password"}).status_code == 200
        token = client.cookies.get("admin_session")
        csrf = client.cookies.get("admin_csrf")
        assert client.get("/api/v1/admin/session").json()["role"] == "moderator"
        assert client.get("/api/v1/admin/audit").status_code == 403
        assert client.post("/api/v1/admin/import?filename=x.csv", content=b"", headers=_csrf(client)).status_code == 403
        event_id = catalog["free"].id
        assert client.patch(f"/api/v1/admin/events/{event_id}/status", json={"status": "draft"},
                            headers=_csrf(client)).status_code == 200
        assert client.post("/api/v1/admin/logout", headers=_csrf(client)).status_code == 200
    with TestClient(create_app(db, settings), base_url="https://testserver") as replay:
        replay.cookies.set("admin_session", token)
        replay.cookies.set("admin_csrf", csrf)
        assert replay.get("/api/v1/admin/session").status_code == 401


def test_password_hash_and_secret_rotation_invalidate_sessions(db):
    settings = Settings(admin_username="reviewer", admin_password_hash=hash_password("long-local-password", iterations=100_000),
                        admin_secret="f" * 32, admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        assert client.post("/api/v1/admin/login", json={"username": "reviewer", "password": "long-local-password"}).status_code == 200
        assert client.get("/api/v1/admin/session").status_code == 200
        settings.admin_secret = "r" * 32
        assert client.get("/api/v1/admin/session").status_code == 401


def test_plaintext_password_needs_no_extra_admin_secret_and_rotation_revokes_sessions(db):
    settings = _settings()
    with TestClient(create_app(db, settings), base_url="https://testserver") as client:
        assert client.post("/api/v1/admin/login", json={"username": "reviewer", "password": "test-password"}).status_code == 200
        assert client.get("/api/v1/admin/session").status_code == 200
        settings.admin_password = "replacement-password"
        assert client.get("/api/v1/admin/session").status_code == 401


def test_admin_session_expiry_and_unauthorized_routes(db):
    settings = _settings()
    with TestClient(create_app(db, settings), base_url="https://testserver") as client:
        for path in ("session", "events", "overview", "moderation", "submissions",
                     "feedback", "audit", "traces", "settings", "monitoring"):
            assert client.get(f"/api/v1/admin/{path}").status_code == 401
        assert client.post("/api/v1/admin/logout").status_code == 401
        assert client.post("/api/v1/admin/login", json={
            "username": "reviewer", "password": "test-password",
        }).status_code == 200
        db.execute("UPDATE admin_sessions SET expires_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        assert client.get("/api/v1/admin/session").status_code == 401
        # The old /api/v1 route namespace is the registered API path; no alternate
        # unversioned administrative endpoints should expose the same resource.
        assert client.get("/admin/session").status_code == 404


def test_login_errors_and_session_endpoint_do_not_echo_credentials(db, caplog):
    username, password = "reviewer", "do-not-leak-this-password"
    settings = Settings(admin_username=username, admin_password=password, admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        response = client.post("/api/v1/admin/login", json={"username": username, "password": "wrong-secret"})
        assert response.status_code == 401
        assert "wrong-secret" not in response.text
        assert username not in response.text
        assert "admin_session" not in response.text
        assert "wrong-secret" not in caplog.text
        assert client.post("/api/v1/admin/login", json={"username": username, "password": password}).status_code == 200
        session = client.get("/api/v1/admin/session")
        assert session.status_code == 200
        assert password not in session.text
        assert client.cookies.get("admin_session") not in session.text


def test_admin_configuration_rejects_conflicting_credentials_and_weak_secrets():
    with pytest.raises(ValueError, match="только один способ"):
        Settings(admin_password="plaintext", admin_password_hash=hash_password("test-password", iterations=100_000))
    with pytest.raises(ValueError, match="ADMIN_SECRET"):
        Settings(admin_password="plaintext", admin_secret="short")
    with pytest.raises(ValueError, match="Некорректный хеш"):
        Settings(admin_password_hash="pbkdf2_sha256:1:bad:bad", admin_secret="s" * 32)
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings(cors_origins=["*"])


def test_admin_rejects_foreign_origin_and_limits_login_by_account(db, catalog):
    settings = _settings(web_origin="https://site.example")
    with TestClient(create_app(db, settings), base_url="https://testserver") as client:
        path = "/api/v1/admin/login"
        credentials = {"username": "reviewer", "password": "test-password"}
        assert client.post(path, json=credentials, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post(path, json={"username": "чужой", "password": "test-password"}).status_code == 401
        assert client.post(path, json=credentials, headers={"Origin": "https://site.example"}).status_code == 200
        event_id = catalog["free"].id
        assert client.patch(f"/api/v1/admin/events/{event_id}/status", json={"status": "draft"},
                            headers={**_csrf(client), "Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/v1/admin/events", headers={"Origin": "https://evil.example"}).status_code == 403
        for _ in range(7):
            assert client.post(path, json={"username": "reviewer", "password": "incorrect"}).status_code == 401
    with TestClient(create_app(db, settings), base_url="https://testserver") as other_client:
        assert other_client.post(path, json=credentials).status_code == 429


def test_feedback_visitor_cookie_is_signed(db):
    with TestClient(create_app(db, Settings(rate_limit_secret="r" * 32))) as client:
        first = client.post("/feedback", json={"message": "Предложение"})
        assert first.status_code == 201
        assert first.cookies.get("afisha_visitor")
        client.cookies.set("afisha_visitor", "forged.signature")
        second = client.post("/feedback", json={"message": "Предложение"})
        assert second.status_code == 201
        assert second.cookies.get("afisha_visitor") != "forged.signature"


def test_administrative_write_and_import_quotas(db):
    with TestClient(create_app(db, _settings()), base_url="https://testserver") as client:
        assert client.post("/api/v1/admin/login", json={"username": "reviewer", "password": "test-password"}).status_code == 200
        headers = _csrf(client)
        for _ in range(2):
            assert client.post("/api/v1/admin/import?filename=test.csv", content=b"", headers=headers).status_code == 200
        assert client.post("/api/v1/admin/import?filename=test.csv", content=b"", headers=headers).status_code == 429
        path = "/api/v1/admin/events/nonexistent/status"
        for _ in range(30):
            assert client.patch(path, json={"status": "draft"}, headers=headers).status_code == 404
        assert client.patch(path, json={"status": "draft"}, headers=headers).status_code == 429


def test_request_quotas_and_body_limit(db):
    with TestClient(create_app(db, Settings())) as client:
        oversized = client.post("/feedback", content=b"x" * 65537,
                                headers={"Content-Type": "application/json"})
        assert oversized.status_code == 413
        assert oversized.headers["cache-control"] == "no-store"
        for _ in range(10):
            assert client.post("/feedback", json={"message": "Предложение"}).status_code == 201
        assert client.post("/feedback", json={"message": "Предложение"}).status_code == 429


def test_import_rejects_unsafe_urls_and_unbounded_batches(db):
    service = ImportService(db)
    assert service.import_records([event(source_url="https://user@example.com/event")], "test").rejected_count == 1
    assert service.import_records([event(org_link="javascript:alert(1)")], "test").rejected_count == 1
    assert service.import_records([event(lat=999)], "test").rejected_count == 1
    try:
        service.import_from_content("[]", "events.json", ".json", max_rows=0)
    except ValueError:
        pass
    else:
        raise AssertionError("Empty row allowance was accepted")


def test_retention_erases_old_feedback_and_raw_data(db):
    imported = ImportService(db).import_records([event()], "old.csv")
    feedback = FeedbackService(db).submit("Чувствительное сообщение")
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    db.execute("UPDATE feedback SET created_at = ? WHERE id = ?", (old, feedback.id))
    db.execute("UPDATE audit_log SET created_at = ? WHERE entity_type = 'feedback'", (old,))
    db.execute("UPDATE raw_events SET ingested_at = ?", (old,))
    db.execute("UPDATE import_reports SET created_at = ? WHERE id = ?", (old, imported.report_id))
    result = prune(db, Settings(feedback_retention_days=1, raw_retention_days=1,
                                import_report_retention_days=1, audit_retention_days=2))
    assert result["feedback"] == 1
    assert result["raw_events"] == 1
    assert result["import_reports"] == 1
    assert db.fetchone("SELECT raw_event_id FROM events")["raw_event_id"] is None
    assert not db.fetchall("SELECT * FROM audit_log WHERE entity_type = 'feedback'")


def test_public_search_uses_bounded_sql_page(db, catalog, monkeypatch):
    original = db.fetchall
    queries = []

    def track(sql, params=()):
        queries.append(sql)
        return original(sql, params)

    monkeypatch.setattr(db, "fetchall", track)
    page, total, _ = SearchService(db).search(SearchFilters(limit=1, offset=1))
    assert len(page) == 1 and total >= 2
    assert any("FROM events WHERE" in sql and "LIMIT ? OFFSET ?" in sql for sql in queries)


def test_bot_escapes_imported_and_query_text(db):
    ImportService(db).import_records([event(title="<b>Подмена</b>", confidence=0.7)], "unsafe.csv")
    router = BotRouter(db, Settings(admin_user_ids=["moderator"]))
    queue = router._handle_admin_command("moderator", "/admin_disputed").text
    assert "&lt;b&gt;Подмена&lt;/b&gt;" in queue
    assert "<b>Подмена</b>" not in queue
    filters = SearchFilters(query_text="<i>неизвестно</i>")
    _, total, explanation = SearchService(db).search(filters)
    rendered = format_search_results([], total, filters, empty_explanation=explanation)
    assert "&lt;i&gt;неизвестно&lt;/i&gt;" in rendered


def test_subscription_inputs_are_bounded_and_command_prefixes_are_exact(db):
    service = SubscriptionService(db)
    with pytest.raises(ValueError):
        service.subscribe("x" * 129, "Уфа", "концерт")
    with pytest.raises(ValueError):
        service.subscribe("user", "Уфа\nинъекция", "концерт")

    router = BotRouter(db, Settings(rate_limit_secret="r" * 32))
    response = router.handle_message("user", "/helpful")
    assert "Справка по сервису" not in response.text
