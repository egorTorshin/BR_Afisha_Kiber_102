"""Public REST adapter. Run: uvicorn src.api:create_app --factory."""
from contextlib import asynccontextmanager
from typing import Annotated, Optional, Literal
from datetime import date, datetime
import base64, hashlib, hmac, secrets, time, json
import logging
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from src.config import load_settings, Settings
from src.utils.logger import get_logger, log_event, new_request_id, get_request_id, request_id_ctx
from src.db.connection import Database
from src.db.migrations import apply_migrations, seed_dictionaries
from src.models.schemas import EventRecord, FeedbackRecord, SearchFilters
from src.services.feedback_service import FeedbackService
from src.services.search_service import SearchService
from src.services.import_service import ImportService


class FeedbackInput(BaseModel):
    event_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=10000)


class EventPage(BaseModel):
    events: list[EventRecord]
    total: int
    explanation: Optional[str] = None


class EventFilters(BaseModel):
    query_text: Optional[str] = None
    city: Optional[str] = None
    venue: Optional[str] = None
    category: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    time_after: Optional[str] = None
    time_before: Optional[str] = None
    max_duration_min: Optional[int] = Field(default=None, ge=1)
    max_price: Optional[int] = Field(default=None, ge=0)
    is_free_only: bool = False
    max_age_limit: Optional[int] = Field(default=None, ge=0, le=18)
    language: Optional[str] = None
    tag: Optional[str] = None
    sort: Literal["date_asc", "date_desc", "price_asc"] = "date_asc"

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_date(cls, value, info):
        if value is None:
            return value
        try:
            if len(value) == 10:
                day = date.fromisoformat(value)
                return day.isoformat() + ("T23:59:59" if info.field_name == "date_to" else "T00:00:00")
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo("Asia/Yekaterinburg")).replace(tzinfo=None)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            raise ValueError("Укажите дату ISO 8601: YYYY-MM-DD или дату со временем") from None

    @field_validator("time_after", "time_before")
    @classmethod
    def validate_time(cls, value):
        if value is not None:
            try:
                parsed = datetime.strptime(value, "%H:%M")
                if parsed.strftime("%H:%M") != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("Время должно иметь формат HH:MM") from None
        return value

    @model_validator(mode="after")
    def validate_period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Начало периода должно быть не позже окончания")
        if self.time_after and self.time_before and self.time_after > self.time_before:
            raise ValueError("Начало интервала времени должно быть не позже окончания")
        return self


class EventQuery(EventFilters):
    limit: int = Field(default=5, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class VersionedEventQuery(EventFilters):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class VersionedEventPage(EventPage):
    page: int
    page_size: int
    pages: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
    detail: str  # Compatibility with clients reading the old HTTPException detail.


class CityItem(BaseModel):
    city: str
    cnt: int


class CategoryItem(BaseModel):
    category: str
    cnt: int


class VenueItem(BaseModel):
    city: str
    name: str
    address: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_app(db: Optional[Database] = None, settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or (load_settings() if db is None else Settings())
    owns_database = db is None
    database = db if db is not None else Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        if owns_database:
            apply_migrations(database, settings.migrations_dir)
            seed_dictionaries(database, settings.data_dir / "dictionaries.json")
            seed_file = settings.data_dir / "seed_events.json"
            if seed_file.exists():
                ImportService(database).import_from_file(seed_file, actor="startup_seed")
        try:
            yield
        finally:
            if owns_database:
                database.close()

    app = FastAPI(title="Афиша культуры Башкортостана", lifespan=lifespan,
                  responses={code: {"model": ErrorResponse} for code in (404, 422, 500)})
    logger = get_logger("afisha_rb.api", settings.log_level)

    def error_response(status, message, details=None, headers=None):
        return JSONResponse(status_code=status, headers=headers, content={
            "error": {"code": f"HTTP_{status}", "message": message, "details": details or []},
            "request_id": get_request_id(), "detail": message,
        })

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        message = {404: "Ресурс не найден", 405: "Метод не поддерживается",
                   422: "Некорректные параметры запроса", 500: "Внутренняя ошибка сервера"}.get(
                       exc.status_code, "Ошибка запроса")
        if isinstance(exc, HTTPException) and exc.status_code < 500:
            message = str(exc.detail)
        return error_response(exc.status_code, message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"field": ".".join(map(str, e["loc"])), "code": e["type"],
                    "message": "Некорректное значение параметра"} for e in exc.errors()]
        return error_response(422, "Проверьте параметры запроса", details)

    @app.middleware("http")
    async def failure_boundary(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            log_event(logger, logging.ERROR, "Ошибка HTTP-запроса", exception_type=type(exc).__name__)
            return error_response(500, "Внутренняя ошибка сервера")

    # CORS wraps the failure boundary; request context wraps CORS, including preflight.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
                       expose_headers=["X-Request-ID", "Cache-Control"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        token = request_id_ctx.set("system")
        rid = new_request_id()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                log_event(logger, logging.ERROR, "Ошибка HTTP-запроса", exception_type=type(exc).__name__)
                response = error_response(500, "Внутренняя ошибка сервера")
            response.headers["X-Request-ID"] = rid
            cacheable = request.method == "GET" and response.status_code == 200 and (
                request.url.path == "/events" or request.url.path.startswith("/events/")
                or request.url.path.startswith("/api/v1/"))
            response.headers["Cache-Control"] = (
                f"public, max-age={settings.public_cache_max_age}, must-revalidate" if cacheable else "no-store")
            log_event(logger, logging.INFO, "HTTP-запрос обработан", method=request.method,
                      status_code=response.status_code)
            return response
        finally:
            request_id_ctx.reset(token)

    search = SearchService(database)
    feedback = FeedbackService(database)
    importer = ImportService(database)
    login_attempts = {}

    def admin_cookie(user: str) -> str:
        payload = f"{user}:{int(time.time())}"
        signature = hmac.new(settings.admin_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

    def require_admin(request: Request, write: bool = False):
        if not settings.admin_secret:
            raise HTTPException(503, "Админка не настроена")
        try:
            raw = base64.urlsafe_b64decode(request.cookies.get("admin_session", "")).decode()
            user, stamp, signature = raw.rsplit(":", 2)
            payload = f"{user}:{stamp}"
            valid = (user == settings.admin_username and time.time() - int(stamp) < 28800 and
                     hmac.compare_digest(signature, hmac.new(settings.admin_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()))
        except Exception:
            valid = False
        if not valid:
            raise HTTPException(401, "Требуется авторизация")
        if write and (not request.cookies.get("admin_csrf") or not hmac.compare_digest(
                request.cookies.get("admin_csrf", ""), request.headers.get("x-csrf-token", ""))):
            raise HTTPException(403, "Проверка CSRF не пройдена")
        return user

    @app.get("/api/v1/admin/session")
    def admin_session(request: Request):
        return {"ok": True, "user": require_admin(request)}

    @app.post("/api/v1/admin/login")
    def admin_login(request: Request, response: Response, body: dict):
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = [t for t in login_attempts.get(ip, []) if now - t < 600]
        login_attempts[ip] = attempts
        if len(attempts) >= 8:
            raise HTTPException(429, "Слишком много попыток входа")
        good = (settings.admin_password and settings.admin_secret and
                hmac.compare_digest(str(body.get("username", "")), settings.admin_username) and
                hmac.compare_digest(str(body.get("password", "")), settings.admin_password))
        if not good:
            login_attempts[ip].append(now)
            raise HTTPException(401, "Неверный логин или пароль")
        csrf = secrets.token_urlsafe(24)
        response.set_cookie("admin_session", admin_cookie(settings.admin_username), httponly=True, samesite="strict", secure=settings.admin_cookie_secure, max_age=28800, path="/")
        response.set_cookie("admin_csrf", csrf, httponly=False, samesite="strict", secure=settings.admin_cookie_secure, max_age=28800, path="/")
        return {"ok": True}

    @app.post("/api/v1/admin/logout")
    def admin_logout(request: Request, response: Response):
        require_admin(request, write=True)
        response.delete_cookie("admin_session", path="/"); response.delete_cookie("admin_csrf", path="/")
        return {"ok": True}

    @app.get("/api/v1/admin/events")
    def admin_events(request: Request, q: str = "", status: str = "", page: int = 1):
        require_admin(request)
        where, params = [], []
        if q:
            where.append("(title LIKE ? OR city LIKE ? OR venue LIKE ?)"); params.extend([f"%{q}%"] * 3)
        if status:
            where.append("status = ?"); params.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        total = database.fetchone("SELECT COUNT(*) n FROM events" + clause, params)["n"]
        rows = database.fetchall("SELECT * FROM events" + clause + " ORDER BY updated_at DESC LIMIT 30 OFFSET ?", (*params, max(0, page-1)*30))
        return {"events": rows, "total": total, "page": page}

    @app.patch("/api/v1/admin/events/{event_id}")
    def admin_update_event(event_id: str, request: Request, body: dict):
        actor = require_admin(request, True)
        try: return importer.update_event_fields(event_id, body, actor=f"admin:{actor}")
        except LookupError as exc: raise HTTPException(404, str(exc)) from exc
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/v1/admin/events/{event_id}/status")
    def admin_event_status(event_id: str, request: Request, body: dict):
        actor = require_admin(request, True)
        try: return importer.update_event_status(event_id, body.get("status", ""), actor=f"admin:{actor}")
        except LookupError as exc: raise HTTPException(404, str(exc)) from exc
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/admin/moderation")
    def admin_moderation(request: Request):
        require_admin(request)
        return database.fetchall("SELECT * FROM events WHERE is_disputed = 1 AND merged_into IS NULL ORDER BY updated_at DESC LIMIT 100")

    @app.get("/api/v1/admin/audit")
    def admin_audit(request: Request):
        require_admin(request)
        rows = database.fetchall("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200")
        for row in rows: row["changes"] = json.loads(row.pop("changes_json"))
        return rows

    @app.post("/api/v1/admin/import")
    async def admin_import(request: Request, filename: str = Query(...)):
        actor = require_admin(request, True)
        if not filename.lower().endswith((".csv", ".json")) or len(filename) > 200:
            raise HTTPException(422, "Поддерживаются CSV и JSON")
        try: return importer.import_from_content((await request.body()).decode("utf-8-sig"), filename, "." + filename.rsplit(".", 1)[-1].lower(), actor=f"admin:{actor}")
        except Exception as exc: raise HTTPException(422, f"Не удалось прочитать импорт: {exc}") from exc

    @app.get("/api/v1/admin/feedback")
    def admin_feedback(request: Request, status: Optional[str] = None):
        require_admin(request)
        try: return feedback.list_requests(status, limit=100)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/v1/admin/feedback/{feedback_id}")
    def admin_feedback_status(feedback_id: str, request: Request, body: dict):
        actor = require_admin(request, True)
        try: return feedback.update_status(feedback_id, body.get("status", ""), actor=f"admin:{actor}")
        except LookupError as exc: raise HTTPException(404, str(exc)) from exc
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @app.get("/events", response_model=EventPage)
    def list_events(query: Annotated[EventQuery, Query()]):
        events, total, explanation = search.search(SearchFilters(**query.model_dump(exclude={"sort"})), sort=query.sort)
        return EventPage(events=events, total=total, explanation=explanation)

    @app.get("/api/v1/events", response_model=VersionedEventPage)
    def list_events_v1(query: Annotated[VersionedEventQuery, Query()]):
        filters = SearchFilters(**query.model_dump(exclude={"page", "page_size", "sort", "limit", "offset"}),
                                limit=query.page_size, offset=(query.page - 1) * query.page_size)
        events, total, explanation = search.search(filters, sort=query.sort)
        return VersionedEventPage(events=events, total=total, explanation=explanation,
                                  page=query.page, page_size=query.page_size,
                                  pages=(total + query.page_size - 1) // query.page_size)

    @app.get("/health", response_model=HealthResponse)
    def health():
        database.fetchone("SELECT 1 AS ready")
        return HealthResponse(status="ok")

    @app.get("/api/v1/cities", response_model=list[CityItem])
    def cities():
        return search.list_cities_with_counts()

    @app.get("/api/v1/categories", response_model=list[CategoryItem])
    def categories(city: Optional[str] = None):
        return search.list_categories_with_counts(city)

    @app.get("/api/v1/venues", response_model=list[VenueItem])
    def venues(city: Optional[str] = None):
        return search.list_public_venues(city)

    @app.get("/api/v1/events/{event_id}", response_model=EventRecord)
    @app.get("/events/{event_id}", response_model=EventRecord)
    def get_event(event_id: str):
        result = search.get_event_by_id(event_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Событие не найдено")
        return result

    @app.post("/feedback", response_model=FeedbackRecord, status_code=201)
    def submit_feedback(data: FeedbackInput):
        event_id = data.event_id
        if event_id is not None:
            event = search.get_event_by_id(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Событие не найдено")
            event_id = event.id
        try:
            return feedback.submit(data.message, event_id, actor="api:anonymous")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
