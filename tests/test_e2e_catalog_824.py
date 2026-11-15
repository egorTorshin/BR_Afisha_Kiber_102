"""Regression coverage for the real SSR catalog assertion used by stage 8.2.4."""
import importlib.util
from pathlib import Path
from datetime import datetime, timezone
import json
import urllib.error

from src.http.schemas import SubmissionReviewInput


SCRIPT = Path(__file__).parents[1] / "scripts" / "e2e_catalog_824.py"
SPEC = importlib.util.spec_from_file_location("e2e_catalog_824", SCRIPT)
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


def test_ssr_catalog_contains_published_cards_and_excludes_pending(monkeypatch):
    # React's server renderer inserts a comment between adjacent text nodes.
    html = """<main>
      <div class="preview-label" role="status"><span>Найдено событий: <!-- -->2</span></div>
      <div class="event-grid">
        <article class="event-card"><h3>E2E Уфа — Камерный концерт 824 — Редакция</h3></article>
        <article class="event-card"><h3>E2E Уфа — Научная выставка 824</h3></article>
      </div>
    </main>"""
    requested = []

    def fake_req(url, raw=False):
        requested.append(url)
        return 200, html

    monkeypatch.setattr(e2e, "req", fake_req)
    result = e2e.verify_frontend(
        "E2E Уфа — Камерный концерт 824 — Редакция", "2026-12-20"
    )

    assert result.startswith("HTTP 200, SSR event-grid")
    assert len(requested) == 1 and "city=%D0%A3%D1%84%D0%B0" in requested[0]
    assert "E2E Уфа — Семейный фестиваль 824" not in html


def test_ssr_catalog_rejects_pending_event_even_with_valid_card_markup(monkeypatch):
    html = """<div class="preview-label"><span>Найдено событий: <!-- -->2</span></div>
    <div class="event-grid"><article class="event-card">Опубликовано</article>
    <article class="event-card">E2E Уфа — Семейный фестиваль 824</article></div>"""
    monkeypatch.setattr(e2e, "req", lambda url, raw=False: (200, html))

    try:
        e2e.verify_frontend("Опубликовано", "2026-12-20")
    except AssertionError as exc:
        assert "unpublished event" in str(exc)
    else:
        raise AssertionError("SSR verification accepted an unpublished event")


def test_moderation_patch_payload_matches_api_schema():
    payload = {"status": "approved"}
    assert SubmissionReviewInput.model_validate(payload).status == "approved"


def test_docker_log_since_timestamp_has_iso8601_timezone():
    timestamp = e2e.log_since_timestamp(datetime(2026, 11, 15, 0, 36, 35, tzinfo=timezone.utc))
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    assert timestamp.endswith("+00:00")


def test_422_diagnostics_report_payload_fields_without_values_or_secrets(monkeypatch):
    def raise_422(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 422, "invalid", {}, None)

    monkeypatch.setattr(e2e.opener, "open", raise_422)
    try:
        e2e.req("http://example.test/api/v1/admin/submissions/123", "PATCH",
                {"status": "approved", "password": "hidden"})
    except e2e.ScenarioError as exc:
        message = str(exc)
        assert "status (str)" in message and "password (str)" in message
        assert "approved" not in message and "hidden" not in message
    else:
        raise AssertionError("422 response did not produce diagnostics")
