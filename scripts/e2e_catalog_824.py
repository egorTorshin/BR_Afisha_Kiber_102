#!/usr/bin/env python3
"""Real HTTP/SQL/SSR smoke scenario for the isolated br-afisha-e2e Compose project."""
import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import re
from html.parser import HTMLParser
from html import unescape
from datetime import date, datetime, timedelta, timezone
import uuid

API = os.getenv("E2E_API", "http://127.0.0.1:18000")
WEB = os.getenv("E2E_WEB", "http://127.0.0.1:13000")
COMPOSE = ["docker", "compose", "-p", "br-afisha-e2e", "-f", "docker-compose.yml", "-f", "compose.e2e.yml", "--env-file", ".env.e2e"]
ORIGIN = WEB
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
checks = []
ids = []
RUN_VENUE_FILTER = "E2E"
CURRENT_STEP = "startup"
CHECK_NAMES = [
    "API health", "frontend endpoint", "admin authentication", "admin CSRF cookie",
    "submission creates four pending rows in PostgreSQL",
    "moderation approval publishes three and fourth remains pending", "admin edit persists to PostgreSQL",
    "event cancellation persists", "public search and city/category filters",
    "pagination and unpublished exclusion", "frontend server-rendered catalog contains actual event cards",
]


class ScenarioError(RuntimeError):
    pass


def safe_text(value, limit=500):
    """Keep response/error diagnostics useful without echoing credentials or tokens."""
    value = re.sub(r"(?i)(password|token|secret|csrf)(\s*[=:]\s*|\"\s*:\s*\")([^\s,}\"]+)", r"\1\2[REDACTED]", value)
    value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return " ".join(value.split())[:limit]


def check(name, fn):
    global CURRENT_STEP
    CURRENT_STEP = name
    try:
        detail = fn()
        checks.append((name, "PASS", str(detail or "")))
    except Exception as exc:
        checks.append((name, "FAIL", safe_text(f"{type(exc).__name__}: {exc}")))
        raise


def req(url, method="GET", data=None, headers=None, raw=False):
    payload = None if data is None else (data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode())
    h = {"Origin": ORIGIN, **(headers or {})}
    if data is not None and not raw:
        h.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=payload, headers=h, method=method)
    try:
        with opener.open(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, body if raw else (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = safe_text(exc.read().decode("utf-8", "replace"))
        endpoint = urllib.parse.urlsplit(url)
        payload_fields = ""
        if exc.code == 422 and isinstance(data, dict):
            # Report only field names and JSON value types; never echo credentials or submitted values.
            fields = ", ".join(f"{key} ({type(value).__name__})" for key, value in data.items())
            payload_fields = f"; payload fields: {fields or '[none]'}"
        raise ScenarioError(
            f"HTTP failure in step '{CURRENT_STEP}': {method} {endpoint.path} "
            f"returned {exc.code}{payload_fields}; response: {body or '[empty]'}"
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        endpoint = urllib.parse.urlsplit(url)
        raise ScenarioError(
            f"HTTP connection failure in step '{CURRENT_STEP}': {method} {endpoint.scheme}://{endpoint.netloc}{endpoint.path}; "
            f"{safe_text(str(getattr(exc, 'reason', exc)))}"
        ) from None


def api(path, method="GET", data=None, headers=None):
    global CURRENT_STEP
    CURRENT_STEP = f"HTTP {method} {path}"
    return req(API + path, method, data, headers)


def sql(query, operation="SQL query"):
    global CURRENT_STEP
    CURRENT_STEP = f"SQL {operation}"
    try:
        result = subprocess.run(COMPOSE + ["exec", "-T", "db", "psql", "-U", "afisha_e2e", "-d", "afisha_e2e", "-At", "-c", query], text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise ScenarioError(safe_text(result.stderr.strip()) or f"psql exited {result.returncode}")
        return result.stdout.strip()
    except Exception as exc:
        if isinstance(exc, ScenarioError):
            raise ScenarioError(f"{CURRENT_STEP} failed: {exc}") from None
        raise ScenarioError(f"{CURRENT_STEP} failed: {safe_text(f'{type(exc).__name__}: {exc}')}") from None


def compose_logs(service):
    result = subprocess.run(COMPOSE + ["logs", "--since", os.environ["E2E_LOG_SINCE"], service], text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def log_since_timestamp(now=None):
    """Docker Compose accepts RFC3339/ISO-8601 timestamps with an explicit offset."""
    return (now or datetime.now(timezone.utc)).isoformat()


def main():
    global CURRENT_STEP, RUN_VENUE_FILTER
    os.environ["E2E_LOG_SINCE"] = log_since_timestamp()
    CURRENT_STEP = "preflight: required environment"
    missing = [key for key in ("ADMIN_USERNAME", "ADMIN_PASSWORD") if not os.getenv(key)]
    if missing:
        raise ScenarioError(f"Missing required environment variable(s): {', '.join(missing)}. Load .env.e2e before running.")
    for label, url in (("E2E_API", API), ("E2E_WEB", WEB)):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ScenarioError(f"Invalid {label} URL: {url!r}; expected an absolute HTTP(S) URL")
    CURRENT_STEP = "preflight: Python and Docker dependencies"
    if not hasattr(urllib.request, "build_opener"):
        raise ScenarioError("Python urllib dependency is unavailable")
    if subprocess.run(["docker", "compose", "version"], capture_output=True, text=True).returncode:
        raise ScenarioError("Docker Compose v2 CLI is unavailable")
    check("API health", lambda: (lambda r: f"HTTP {r[0]}")(req(API + "/health")))
    check("frontend endpoint", lambda: (lambda r: f"HTTP {r[0]}")(req(WEB + "/", raw=True)))
    check("admin authentication", lambda: login())
    csrf_cookie = next((c for c in jar if c.name == "admin_csrf"), None)
    def validate_csrf_cookie():
        if csrf_cookie is None or not csrf_cookie.value:
            raise ScenarioError("Admin login succeeded, but the admin_csrf cookie is missing or empty; cannot perform authorized writes")
        return "cookie received"
    check("admin CSRF cookie", validate_csrf_cookie)
    csrf = csrf_cookie.value
    write_headers = {"X-CSRF-Token": csrf}

    city = "Уфа"
    # Unique source URLs and titles keep reruns isolated from events already created by a prior run.
    run_id = uuid.uuid4().hex[:10]
    names = [f"E2E Уфа — Камерный концерт 824 {run_id}", f"E2E Уфа — Театральная лаборатория 824 {run_id}",
             f"E2E Уфа — Научная выставка 824 {run_id}", f"E2E Уфа — Семейный фестиваль 824 {run_id}"]
    venues = [f"Дом культуры E2E {run_id}", f"Сцена E2E {run_id}",
              f"Галерея E2E {run_id}", f"Парк E2E {run_id}"]
    RUN_VENUE_FILTER = run_id
    categories = ["концерт", "спектакль", "выставка", "фестиваль"]
    start = date.today() + timedelta(days=35)
    submitted = []
    for i, name in enumerate(names):
        dt = f"{(start + timedelta(days=i)).isoformat()}T18:00:00"
        data = {"title": name, "category": categories[i], "date_start": dt, "city": city,
                "venue": venues[i], "address": f"Уфа, тестовый адрес {i+1}",
                "description": f"Воспроизводимое изолированное E2E мероприятие номер {i+1} для проверки каталога Башкортостана.",
                "source_url": f"https://e2e.invalid/824/{run_id}/{i+1}", "price_min": 100 + i*50,
                "price_max": 500 + i*50, "age_limit": 0 if i == 3 else 6}
        _, result = api("/api/v1/event-submissions", "POST", data)
        submitted.append((result["id"], data))
    check("submission creates four pending rows in PostgreSQL", lambda: verify_submissions(submitted))

    for i, (submission_id, _) in enumerate(submitted):
        if i < 3:
            _, reviewed = api(f"/api/v1/admin/submissions/{submission_id}", "PATCH", {"status": "approved"}, write_headers)
            ids.append(reviewed["event_id"])
        # Fourth remains pending to verify public exclusion.
    check("moderation approval publishes three and fourth remains pending", lambda: verify_moderation(submitted))

    event_id = ids[0]
    _, edited = api(f"/api/v1/admin/events/{event_id}", "PATCH", {"title": names[0] + " — Редакция", "description": "Отредактировано в ходе реального E2E теста каталога."}, write_headers)
    check("admin edit persists to PostgreSQL", lambda: verify_edit(event_id, edited["title"]))
    _, canceled = api(f"/api/v1/admin/events/{ids[1]}/status", "PATCH", {"status": "cancelled"}, write_headers)
    check("event cancellation persists", lambda: verify_status(ids[1], "cancelled"))

    check("public search and city/category filters", lambda: verify_search(names))
    check("pagination and unpublished exclusion", lambda: verify_pages(names, submitted))
    check("frontend server-rendered catalog contains actual event cards", lambda: verify_frontend(names[0] + " — Редакция", start.isoformat()))


def login():
    user, password = os.getenv("ADMIN_USERNAME"), os.getenv("ADMIN_PASSWORD")
    if not user or not password:
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD from .env.e2e in this shell")
    code, _ = api("/api/v1/admin/login", "POST", {"username": user, "password": password})
    if code != 200:
        raise AssertionError(f"login HTTP {code}")
    code, session = api("/api/v1/admin/session")
    if code != 200 or not session.get("ok"):
        raise AssertionError("admin session not established")
    return f"HTTP {code}, role={session['role']}"


def verify_submissions(submitted):
    count = sql("SELECT count(*) FROM event_submissions WHERE id IN (" + ",".join("'"+x[0]+"'" for x in submitted) + ")", "count submitted event_submissions")
    assert count == "4", f"expected 4 app-written rows, got {count}"
    return "4 rows written by public submission API"


def verify_moderation(submitted):
    states = sql("SELECT string_agg(status, ',' ORDER BY status) FROM event_submissions WHERE id IN (" + ",".join("'"+x[0]+"'" for x in submitted) + ")", "check moderation statuses")
    assert states == "approved,approved,approved,pending", states
    assert sql("SELECT count(*) FROM events WHERE id IN (" + ",".join("'"+x+"'" for x in ids) + ") AND status='published'", "count published events") == "3"
    return states


def verify_edit(event_id, title):
    assert title.endswith("— Редакция")
    assert sql("SELECT title FROM events WHERE id='" + event_id + "'", "verify edited event") == title
    return title


def verify_status(event_id, status):
    got = sql("SELECT status FROM events WHERE id='" + event_id + "'", "verify event cancellation")
    assert got == status, got
    return status


def query(**params):
    return api("/api/v1/events?" + urllib.parse.urlencode(params))[1]


def verify_search(names):
    result = query(city="Уфа", venue=RUN_VENUE_FILTER, page_size=20)
    titles = [event["title"] for event in result["events"]]
    assert result["total"] == 2, f"expected edited plus one published, cancellation hidden: {result['total']} {titles}"
    filtered = query(city="Уфа", venue=RUN_VENUE_FILTER, category="концерт", page_size=20)
    assert filtered["total"] == 1 and "Редакция" in filtered["events"][0]["title"]
    searched = query(query_text="Редакция", page_size=20)
    assert searched["total"] >= 1
    return "city=Уфа, category=концерт, text query passed"


def verify_pages(names, submitted):
    allpub = query(city="Уфа", venue=RUN_VENUE_FILTER, page_size=1, page=1)
    assert allpub["total"] == 2 and allpub["pages"] == 2
    page2 = query(city="Уфа", venue=RUN_VENUE_FILTER, page_size=1, page=2)
    assert page2["total"] == 2 and page2["events"][0]["id"] != allpub["events"][0]["id"]
    unseen = [data["title"] for _, data in submitted[3:]]
    serialized = json.dumps(query(city="Уфа", venue=RUN_VENUE_FILTER, page_size=20), ensure_ascii=False)
    assert all(name not in serialized for name in unseen), "pending title appeared in public results"
    assert sql("SELECT count(*) FROM events WHERE id='" + ids[1] + "' AND status='cancelled'", "verify cancelled event") == "1"
    return "2 pages; pending submission absent; cancelled event excluded"


def verify_frontend(title, from_date):
    url = WEB + "/?" + urllib.parse.urlencode({"date_from": from_date, "city": "Уфа", "venue": RUN_VENUE_FILTER, "page_size": "20"}) + "#results"
    code, html = req(url, raw=True)
    assert code == 200 and title in html, f"SSR HTML lacks event title ({len(html)} bytes)"
    parsed = CatalogMarkup(html)
    assert parsed.event_grids and parsed.event_cards, "catalog card markup absent"
    assert parsed.result_count == 2, f"expected 2 catalog results in SSR HTML, got {parsed.result_count!r}"
    assert "E2E Уфа — Семейный фестиваль 824" not in html, "unpublished event appeared in SSR catalog"
    return f"HTTP {code}, SSR event-grid contains {title}"


class CatalogMarkup(HTMLParser):
    """Read rendered catalog structure while tolerating React's SSR text markers."""
    def __init__(self, html):
        super().__init__()
        self.event_grids = 0
        self.event_cards = 0
        self._in_count = False
        self._count_text = []
        self.result_count = None
        self.feed(html)
        text = unescape("".join(self._count_text))
        match = re.search(r"Найдено событий:\s*(\d+)", text)
        if match:
            self.result_count = int(match.group(1))

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if tag == "div" and "event-grid" in classes:
            self.event_grids += 1
        if tag == "article" and "event-card" in classes:
            self.event_cards += 1
        if tag == "div" and "preview-label" in classes:
            self._in_count = True

    def handle_endtag(self, tag):
        if tag == "div" and self._in_count:
            self._in_count = False

    def handle_data(self, data):
        if self._in_count:
            self._count_text.append(data)


def verify_logs():
    import re
    errors = []
    for svc in ("api", "frontend"):
        try:
            log = compose_logs(svc)
        except Exception as exc:
            errors.append(f"{svc}: log collection failed: {safe_text(f'{type(exc).__name__}: {exc}')}")
            continue
        for line in log.splitlines():
            if re.search(r"\b(ERROR|CRITICAL|Traceback|UnhandledPromiseRejection)\b", line, re.I):
                errors.append(f"{svc}: {line[:300]}")
    assert not errors, "\n".join(errors[:10])
    return "no matching new error lines"


if __name__ == "__main__":
    exit_code = 0
    os.environ["E2E_LOG_SINCE"] = log_since_timestamp()
    try:
        main()
    except Exception as exc:
        exit_code = 1
        existing_fail = any(status == "FAIL" for _, status, _ in checks)
        if not existing_fail:
            checks.append((CURRENT_STEP, "FAIL", safe_text(f"{type(exc).__name__}: {exc}")))
        print(f"\nERROR at step '{CURRENT_STEP}': {safe_text(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
    finally:
        try:
            check("API and frontend logs have no new error/traceback", verify_logs)
        except Exception:
            exit_code = 1
    completed = {name for name, _, _ in checks}
    for name in CHECK_NAMES:
        if name not in completed:
            checks.append((name, "SKIP", "not run because an earlier step failed"))
    for name, status, detail in checks:
        print(f"{status:4} {name}" + (f" — {detail}" if detail else ""))
    counts = {status: sum(1 for _, result, _ in checks if result == status) for status in ("PASS", "FAIL", "SKIP")}
    print(f"\nИтог: PASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']}; проверок: {len(checks)}")
    if exit_code or counts["FAIL"]:
        print("E2E сценарий завершился с ошибкой.")
        sys.exit(1)
    if counts["SKIP"]:
        print("E2E сценарий выполнен не полностью.")
        sys.exit(1)
    print("Все E2E проверки пройдены.")
