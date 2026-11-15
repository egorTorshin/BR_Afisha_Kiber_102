"""Isolated source-contract checks for the separately rendered iframe widget."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EMBED = (ROOT / "frontend/src/app/embed/page.tsx").read_text()
BUILDER = (ROOT / "frontend/src/app/developers/page.tsx").read_text()
PROXY = (ROOT / "frontend/src/proxy.ts").read_text()
CSS = (ROOT / "frontend/src/app/embed/widget.module.css").read_text()
PUBLIC_API = (ROOT / "src/http/public.py").read_text()
README = (ROOT / "README.md").read_text()


def test_documented_builder_emits_iframe_with_all_supported_parameters():
    assert "На `/developers` можно настроить iframe-виджет" in README
    assert '<iframe src="https://ваш-домен.ru${src}"' in BUILDER
    assert 'url.set("city"' in BUILDER
    assert 'url.set("category"' in BUILDER
    assert 'url.set("period", period)' in BUILDER
    assert 'url.set("height", height)' in BUILDER
    assert '<option value="week">' in BUILDER
    assert '<option value="month">' in BUILDER
    assert '<option value="all">' in BUILDER


def test_widget_defaults_validate_and_bound_parameters():
    assert 'one(params.period) || "week"' in EMBED
    assert 'one(params.height) || "480"' in EMBED
    assert '"week", "month", "all"' in EMBED
    assert "Number(rawHeight) < 240" in EMBED
    assert "Number(rawHeight) > 1000" in EMBED
    assert r"/[<>\u0000-\u001f]/" in EMBED
    assert "text.length > 100" in EMBED


def test_widget_queries_the_same_public_catalog_as_rest_api():
    assert "api.events(config.filters)" in EMBED
    assert 'request<EventPage>(`/api/v1/events${query(filters)}`)' in (
        ROOT / "frontend/src/lib/api.ts"
    ).read_text()
    assert '@router.get("/api/v1/events"' in PUBLIC_API
    for key in ("city", "category", "date_from", "date_to", "page", "page_size", "sort"):
        assert f"{key}" in EMBED


def test_iframe_has_scoped_styles_and_explicit_frame_policy():
    assert 'import styles from "./widget.module.css"' in EMBED
    assert ".widget{" in CSS
    assert 'frame-ancestors ${embeddable ? "*" ' in PROXY
    assert 'if (!embeddable) response.headers.set("X-Frame-Options", "DENY")' in PROXY


def test_public_widget_does_not_require_browser_session_or_third_party_cookie():
    assert 'credentials: "same-origin"' in (
        ROOT / "frontend/src/lib/api.ts"
    ).read_text()
    assert "api.events(config.filters)" in EMBED
    route = PUBLIC_API.split('@router.get("/api/v1/events"', 1)[1].split("@router.get(", 1)[0]
    assert "users.session" not in route


def test_widget_escapes_event_content_and_handles_empty_or_failed_backend():
    assert "{cardTitle(event.title)}" in EMBED
    assert "{event.city} · {event.venue}" in EMBED
    assert 'target="_blank" rel="noreferrer"' in EMBED
    assert "Афиша временно недоступна" in EMBED
    assert "Событий пока нет" in EMBED


def test_widget_and_generated_embed_example_are_responsive():
    assert "width:100%" in CSS
    assert "overflow-wrap:anywhere" in CSS
    assert 'width="100%" height="${height}"' in BUILDER
    assert "max-width:100%" in BUILDER
    assert "width:100%" in BUILDER


def test_invalid_height_and_period_are_rendered_as_widget_errors():
    assert "period должен быть week, month или all" in EMBED
    assert "height должен быть от 240 до 1000 пикселей" in EMBED
    assert 'role="alert"' in EMBED
