"""Stage 4.2 REST/OpenAPI contract regression checks."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from tests.test_backend import event


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "api-stage42.db"))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def api_client(db):
    with TestClient(create_app(db, Settings(admin_username="reviewer", admin_password="test-password")),
                    raise_server_exceptions=False) as client:
        yield client


def test_openapi_documents_all_public_contracts_and_examples(api_client):
    schema = api_client.get("/openapi.json").json()
    assert schema["openapi"] == "3.1.0"
    paths = schema["paths"]
    required = {
        "/api/v1/events": {"get"},
        "/api/v1/events/{event_id}": {"get"},
        "/api/v1/cities": {"get"},
        "/api/v1/categories": {"get"},
        "/api/v1/venues": {"get"},
        "/api/v1/feedback": {"post"},
        "/api/v1/event-submissions": {"post"},
        "/api/v1/subscriptions": {"get", "post", "delete"},
        "/api/v1/subscriptions/{subscription_id}": {"delete"},
    }
    for path, methods in required.items():
        assert path in paths
        assert methods <= paths[path].keys()

    # Examples must live in valid OpenAPI request/response locations and retain schemas.
    for path, method in (("/api/v1/events", "get"), ("/api/v1/events/{event_id}", "get"),
                         ("/api/v1/cities", "get"), ("/api/v1/categories", "get"),
                         ("/api/v1/venues", "get"), ("/api/v1/feedback", "post"),
                         ("/api/v1/subscriptions", "get"), ("/api/v1/subscriptions", "post")):
        operation = paths[path][method]
        has_response_example = any(
            media.get("example") is not None or media.get("examples")
            for response in operation.get("responses", {}).values()
            for media in response.get("content", {}).values()
        )
        has_request_example = any(
            media.get("example") is not None or media.get("examples")
            for media in operation.get("requestBody", {}).get("content", {}).values()
        )
        assert has_response_example or has_request_example, (path, method)
    subscription_body = paths["/api/v1/subscriptions"]["post"]["requestBody"]["content"]["application/json"]
    assert "schema" in subscription_body and "examples" in subscription_body


def test_every_versioned_http_route_is_in_openapi_or_explicitly_internal(api_client):
    app = api_client.app
    schema_paths = api_client.get("/openapi.json").json()["paths"]
    excluded = {"/metrics"}
    actual = {
        (route.path, method.lower())
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
        if route.path.startswith("/api/v1/") and method not in {"HEAD", "OPTIONS"}
    }
    documented = {
        (path, method)
        for path, item in schema_paths.items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual <= documented
    assert not (set(schema_paths) & excluded)


def test_event_submission_feedback_and_catalog_cache_contract(api_client, db):
    _, record = ImportService(db).create_event_manual(event())
    for path in ("/api/v1/cities", "/api/v1/categories", "/api/v1/venues", "/api/v1/events"):
        response = api_client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"].startswith("public, max-age=")

    feedback = api_client.post("/api/v1/feedback", json={"event_id": record.id, "message": "Проверьте дату"})
    assert feedback.status_code == 201
    assert feedback.json()["event_id"] == record.id
    assert feedback.headers["x-request-id"].startswith("req-")

    submission = api_client.post("/api/v1/event-submissions", json={
        "title": "Новый спектакль", "category": "спектакль", "date_start": "2026-12-01T19:00:00",
        "city": "Уфа", "venue": "Театр", "address": "ул. Ленина, 5",
        "description": "Премьера нового спектакля для зрителей.",
        "source_url": "https://example.org/event",
    })
    assert submission.status_code == 201, submission.text
    assert submission.headers["x-request-id"].startswith("req-")


def test_rate_limited_public_write_uses_common_error_contract(api_client, monkeypatch):
    from src.services.admin_auth import AdminAuth

    monkeypatch.setattr(AdminAuth, "check_rate", lambda *args, **kwargs: False)
    response = api_client.post("/api/v1/feedback", json={"message": "Проверка лимита"})
    assert response.status_code == 429
    body = response.json()
    assert body["error"]["code"] == "HTTP_429"
    assert body["error"]["message"]
    assert isinstance(body["error"]["details"], list)
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["retry-after"]


@pytest.mark.parametrize("path", ["/feedback", "/api/v1/feedback"])
def test_feedback_limit_applies_to_each_route(api_client, monkeypatch, path):
    from src.services.admin_auth import AdminAuth

    seen = {}
    def check_rate(_self, category, identity, limit, window):
        if category == "feedback:visitor":
            key = (category, identity)
            seen[key] = seen.get(key, 0) + 1
            return seen[key] <= limit
        return True
    monkeypatch.setattr(AdminAuth, "check_rate", check_rate)

    for _ in range(10):
        assert api_client.post(path, json={"message": "Проверка лимита"}).status_code == 201
    response = api_client.post(path, json={"message": "Превышение лимита"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "HTTP_429"
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_feedback_limit_cannot_be_bypassed_by_alternating_routes(api_client, monkeypatch):
    from src.services.admin_auth import AdminAuth

    seen = {}
    def check_rate(_self, category, identity, limit, window):
        if category == "feedback:visitor":
            key = (category, identity)
            seen[key] = seen.get(key, 0) + 1
            return seen[key] <= limit
        return True
    monkeypatch.setattr(AdminAuth, "check_rate", check_rate)

    paths = ["/feedback", "/api/v1/feedback"]
    for index in range(10):
        assert api_client.post(paths[index % 2], json={"message": "Общее ограничение"}).status_code == 201
    response = api_client.post(paths[0], json={"message": "Превышение общего лимита"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "HTTP_429"
    assert response.json()["request_id"] == response.headers["x-request-id"]
