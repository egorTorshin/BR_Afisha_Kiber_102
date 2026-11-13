"""Administrative HTTP routes and authorization dependencies."""
import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.config import Settings
from src.db.connection import Database
from src.services.admin_auth import AdminAuth, SESSION_SECONDS
from src.services.admin_catalog_service import AdminCatalogService
from src.services.feedback_service import FeedbackService
from src.services.import_service import ImportService
from src.http.schemas import (AdminEvent, AdminEventPage, AdminEventPatchInput, AuditEntry,
                              EventStatusInput, FeedbackStatusInput, LoginInput)
from src.http.security import check_browser_origin
from src.http.errors import run_service
from src.models.schemas import FeedbackRecord


def create_admin_router(database: Database, settings: Settings, auth: AdminAuth) -> APIRouter:
    router = APIRouter()
    catalog = AdminCatalogService(database)
    feedback = FeedbackService(database)
    importer = ImportService(database, max_import_bytes=settings.max_import_bytes,
                             max_import_rows=settings.max_import_rows)

    def require_admin(request: Request, write: bool = False, *, roles: tuple[str, ...] = ("admin", "moderator")):
        if not (settings.admin_password or settings.admin_password_hash):
            raise HTTPException(503, "Админка не настроена")
        session = auth.get_session(request.cookies.get("admin_session", ""))
        if session is None:
            raise HTTPException(401, "Требуется авторизация")
        if session["role"] not in roles:
            raise HTTPException(403, "Недостаточно прав")
        if write:
            check_browser_origin(request, settings)
        csrf_cookie = request.cookies.get("admin_csrf", "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if write and (not csrf_cookie or not csrf_header or
                      not hmac.compare_digest(csrf_cookie, csrf_header) or
                      not hmac.compare_digest(hashlib.sha256(csrf_cookie.encode()).hexdigest(), session["csrf_hash"])):
            raise HTTPException(403, "Проверка CSRF не пройдена")
        return session

    def admin_read(request: Request):
        return require_admin(request)

    def admin_write(request: Request):
        return require_admin(request, True)

    def admin_only_read(request: Request):
        return require_admin(request, roles=("admin",))

    def admin_only_write(request: Request):
        return require_admin(request, True, roles=("admin",))

    @router.get("/api/v1/admin/session")
    def admin_session(session: dict = Depends(admin_read)):
        return {"ok": True, "user": session["username"], "role": session["role"]}

    @router.post("/api/v1/admin/login")
    def admin_login(request: Request, response: Response, body: LoginInput):
        check_browser_origin(request, settings)
        role = auth.verify_credentials(body.username, body.password)
        if role is None:
            raise HTTPException(401, "Неверный логин или пароль")
        token, csrf = auth.create_session(body.username, role)
        response.set_cookie("admin_session", token, httponly=True, samesite="strict", secure=settings.admin_cookie_secure, max_age=SESSION_SECONDS, path="/")
        response.set_cookie("admin_csrf", csrf, httponly=False, samesite="strict", secure=settings.admin_cookie_secure, max_age=SESSION_SECONDS, path="/")
        return {"ok": True}

    @router.post("/api/v1/admin/logout")
    def admin_logout(request: Request, response: Response, session: dict = Depends(admin_write)):
        auth.revoke(request.cookies.get("admin_session", ""))
        response.delete_cookie("admin_session", path="/"); response.delete_cookie("admin_csrf", path="/")
        return {"ok": True}

    @router.get("/api/v1/admin/events", response_model=AdminEventPage)
    def admin_events(q: str = Query(default="", max_length=200),
                     status: str = Query(default="", max_length=30), page: int = Query(default=1, ge=1, le=5000),
                     session: dict = Depends(admin_read)):
        return catalog.list_events(q, status, page)

    @router.patch("/api/v1/admin/events/{event_id}", response_model=AdminEvent)
    def admin_update_event(event_id: str, body: AdminEventPatchInput, session: dict = Depends(admin_write)):
        return run_service(importer.update_event_fields, event_id, body.model_dump(exclude_unset=True),
                           actor=f"admin:{session['username']}")

    @router.patch("/api/v1/admin/events/{event_id}/status", response_model=AdminEvent)
    def admin_event_status(event_id: str, body: EventStatusInput, session: dict = Depends(admin_write)):
        return run_service(importer.update_event_status, event_id, body.status,
                           actor=f"admin:{session['username']}")

    @router.get("/api/v1/admin/moderation", response_model=list[AdminEvent])
    def admin_moderation(session: dict = Depends(admin_read)):
        return catalog.disputed_events()

    @router.get("/api/v1/admin/audit", response_model=list[AuditEntry])
    def admin_audit(session: dict = Depends(admin_only_read)):
        return catalog.recent_audit()

    @router.post("/api/v1/admin/import")
    async def admin_import(request: Request, filename: str = Query(...), session: dict = Depends(admin_only_write)):
        if not filename.lower().endswith((".csv", ".json")) or len(filename) > 200 or "/" in filename or "\\" in filename:
            raise HTTPException(422, "Поддерживаются CSV и JSON")
        try:
            return importer.import_from_content((await request.body()).decode("utf-8-sig"), filename,
                                                "." + filename.rsplit(".", 1)[-1].lower(),
                                                actor=f"admin:{session['username']}", max_rows=settings.max_import_rows)
        except (UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, "Не удалось прочитать импорт") from exc

    @router.get("/api/v1/admin/feedback", response_model=list[FeedbackRecord])
    def admin_feedback(status: Optional[str] = None, session: dict = Depends(admin_read)):
        return run_service(feedback.list_requests, status, limit=100)

    @router.patch("/api/v1/admin/feedback/{feedback_id}", response_model=FeedbackRecord)
    def admin_feedback_status(feedback_id: str, body: FeedbackStatusInput, session: dict = Depends(admin_write)):
        return run_service(feedback.update_status, feedback_id, body.status,
                           actor=f"admin:{session['username']}")

    return router
