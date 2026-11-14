import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.services.event_submission_service import EventSubmissionService
from src.services.search_service import SearchService
from src.models.schemas import SearchFilters
from tests.test_backend import db, empty_db, event


def test_submission_stays_private_until_review(db):
    service = EventSubmissionService(db)
    proposal = event(title="Предложенный концерт", source_url="https://example.com/proposal")
    saved = service.submit(proposal)
    assert saved["status"] == "pending"
    assert len(service.pending()) == 1
    assert SearchService(db).search(SearchFilters())[1] == 0
    reviewed = service.review(saved["id"], "approved", "admin:test")
    assert reviewed["event_id"]
    assert SearchService(db).search(SearchFilters())[1] == 1
    with pytest.raises(ValueError, match="уже рассмотрена"):
        service.review(saved["id"], "rejected", "admin:test")


def test_submission_accepts_multiple_showtimes_on_same_day(db):
    service = EventSubmissionService(db)
    proposal = event(title="Два показа", source_url="https://example.com/two",
                     date_start="2026-12-20T10:00:00",
                     schedule=["2026-12-20T10:00:00", "2026-12-20T13:00:00"],
                     language="башкирский")
    saved = service.submit(proposal)
    assert len(service.pending()[0]["schedule"]) == 2
    service.review(saved["id"], "approved", "admin:test")
    page, total, _ = SearchService(db).search(SearchFilters())
    assert total == 1
    assert len(page[0].schedule) == 2
    assert page[0].language == "башкирский"


def test_rejection_and_duplicate_guard(db):
    service = EventSubmissionService(db)
    proposal = event(title="Новое событие", source_url="https://example.com/new")
    rejected = service.submit(proposal)
    service.review(rejected["id"], "rejected", "admin:test")
    assert SearchService(db).search(SearchFilters())[1] == 0
    first = service.submit(proposal)
    service.review(first["id"], "approved", "admin:test")
    duplicate = service.submit(proposal)
    with pytest.raises(ValueError, match="уже есть"):
        service.review(duplicate["id"], "approved", "admin:test")
    assert db.fetchone("SELECT status FROM event_submissions WHERE id = ?", (duplicate["id"],))["status"] == "pending"


def test_public_endpoint_creates_pending_proposal(db):
    with TestClient(create_app(db, Settings())) as client:
        response = client.post("/api/v1/event-submissions", json=event())
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "pending"
        assert client.get("/api/v1/events").json()["total"] == 0
        assert client.get("/api/v1/admin/submissions").status_code in (401, 503)


def test_moderator_approval_requires_csrf_and_publishes(db):
    settings = Settings(admin_username="reviewer", admin_password="test-password", admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        submission = client.post("/api/v1/event-submissions", json=event()).json()
        assert client.get("/api/v1/admin/submissions").status_code == 401
        assert client.post("/api/v1/admin/login", json={"username": "reviewer", "password": "test-password"}).status_code == 200
        pending = client.get("/api/v1/admin/submissions")
        assert pending.status_code == 200
        assert [item["id"] for item in pending.json()] == [submission["id"]]
        path = f"/api/v1/admin/submissions/{submission['id']}"
        assert client.patch(path, json={"status": "approved"}).status_code == 403
        csrf = client.cookies.get("admin_csrf")
        approved = client.patch(path, json={"status": "approved"}, headers={"X-CSRF-Token": csrf})
        assert approved.status_code == 200, approved.text
        assert approved.json()["event_id"]
        assert client.get("/api/v1/events").json()["total"] == 1
