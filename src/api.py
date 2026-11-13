"""Public REST adapter. Run: uvicorn src.api:create_app --factory."""
from contextlib import asynccontextmanager
from typing import Optional
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import load_settings, Settings
from src.utils.logger import get_logger, log_event, new_request_id, get_request_id, request_id_ctx
from src.db.connection import Database
from src.services.admin_auth import AdminAuth
from src.http.schemas import ErrorResponse
from src.http.security import VISITOR_COOKIE, check_browser_origin, visitor_identity


def create_app(db: Optional[Database] = None, settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or (load_settings() if db is None else Settings())
    owns_database = db is None
    database = db if db is not None else Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if owns_database:
                database.close()

    app = FastAPI(title="Афиша культуры Башкортостана", lifespan=lifespan,
                  responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 413, 422, 429, 500, 503)})
    logger = get_logger("afisha_rb.api", settings.log_level)
    auth = AdminAuth(database, settings)

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
        visitor_cookie = None
        try:
            try:
                path = request.url.path
                if path.startswith("/api/v1/admin/"):
                    check_browser_origin(request, settings)
                if request.method in {"POST", "PATCH", "PUT"}:
                    body_limit = settings.max_import_bytes if path == "/api/v1/admin/import" else 64 * 1024
                    declared = request.headers.get("content-length")
                    if declared and int(declared) > body_limit:
                        raise HTTPException(413, "Размер запроса превышает допустимый")
                if request.method in {"POST", "PATCH", "PUT"}:
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > body_limit:
                            raise HTTPException(413, "Размер запроса превышает допустимый")
                    request._body = bytes(body)
                quotas = []
                if request.method in {"POST", "PATCH", "PUT"}:
                    client_ip = request.client.host if request.client else "unknown"
                    visitor, visitor_cookie = visitor_identity(request, settings.rate_limit_secret)
                    if path == "/api/v1/admin/login":
                        try:
                            username = str(json.loads(request._body).get("username", "")).strip().casefold()[:100]
                        except (ValueError, TypeError, AttributeError):
                            username = ""
                        quotas = [("login:visitor", visitor, 12, 600),
                                  ("login:ip", client_ip, 200, 600)]
                        if username:
                            quotas.append(("login:account", username, 8, 600))
                    elif path == "/feedback":
                        quotas = [("feedback:visitor", visitor, 10, 60),
                                  ("feedback:ip", client_ip, 200, 60)]
                    elif path.startswith("/api/v1/admin/"):
                        identity = request.cookies.get("admin_session") or visitor
                        limit = 2 if path == "/api/v1/admin/import" else 30
                        quotas = [(f"admin-write:{path}", identity, limit, 60),
                                  ("admin-write:ip", client_ip, 300, 60)]
                for category, identity, limit, window in quotas:
                    if not auth.check_rate(category, identity, limit, window):
                        raise HTTPException(429, "Слишком много запросов", headers={"Retry-After": str(window)})
                response = await call_next(request)
            except HTTPException as exc:
                response = error_response(exc.status_code, str(exc.detail), headers=exc.headers)
            except ValueError:
                response = error_response(400, "Некорректные заголовки запроса")
            except Exception as exc:
                log_event(logger, logging.ERROR, "Ошибка HTTP-запроса", exception_type=type(exc).__name__)
                response = error_response(500, "Внутренняя ошибка сервера")
            response.headers["X-Request-ID"] = rid
            if visitor_cookie and request.method in {"POST", "PATCH", "PUT"}:
                response.set_cookie(VISITOR_COOKIE, visitor_cookie, httponly=True, samesite="lax",
                                    secure=settings.web_origin.startswith("https://") or request.url.scheme == "https",
                                    max_age=30 * 24 * 60 * 60, path="/")
            public_paths = {"/events", "/api/v1/events", "/api/v1/cities",
                            "/api/v1/categories", "/api/v1/venues"}
            cacheable = (request.method == "GET" and response.status_code == 200
                         and not request.cookies and (path in public_paths
                         or path.startswith("/events/") or path.startswith("/api/v1/events/")))
            response.headers["Cache-Control"] = (
                f"public, max-age={settings.public_cache_max_age}, must-revalidate" if cacheable else "no-store")
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["X-Frame-Options"] = "DENY"
            log_event(logger, logging.INFO, "HTTP-запрос обработан", method=request.method,
                      status_code=response.status_code)
            return response
        finally:
            request_id_ctx.reset(token)

    from src.http.admin import create_admin_router
    from src.http.public import create_public_router
    app.include_router(create_admin_router(database, settings, auth))
    app.include_router(create_public_router(database))
    return app
