"""Telegram Login Widget verification and user-scoped API."""
import hashlib
import hmac
import time
import secrets
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, Query
from pydantic import BaseModel

from src.config import Settings
from src.db.connection import Database
from src.http.security import check_browser_origin
from src.services.user_service import UserService


class TelegramLogin(BaseModel):
    id: int
    auth_date: int
    hash: str
    first_name: str = ''
    last_name: str = ''
    username: str = ''
    photo_url: str = ''


def create_user_router(db: Database, settings: Settings, admin_auth=None):
    router = APIRouter()
    service = UserService(db)

    def current(request: Request):
        user = service.session(request.cookies.get('user_session', ''))
        if not user:
            raise HTTPException(401, 'Требуется авторизация')
        return user

    def require_platform_admin(request: Request):
        user = service.session(request.cookies.get('user_session', ''))
        legacy = admin_auth.get_session(request.cookies.get('admin_session', '')) if admin_auth else None
        if not ((user and user['role'] == 'admin') or (legacy and legacy['role'] == 'admin')):
            raise HTTPException(403, 'Недостаточно прав')
        return user, legacy

    @router.post('/api/v1/auth/telegram')
    def telegram_login(body: TelegramLogin, request: Request, response: Response):
        check_browser_origin(request, settings)
        if not settings.telegram_bot_token:
            raise HTTPException(503, 'Вход через Telegram не настроен')
        if body.auth_date > time.time() + 60 or time.time() - body.auth_date > 300:
            raise HTTPException(401, 'Срок подтверждения Telegram истёк')
        data = body.model_dump(exclude_defaults=True)
        supplied = data.pop('hash')
        check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        key = hashlib.sha256(settings.telegram_bot_token.encode()).digest()
        digest = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, supplied):
            raise HTTPException(401, 'Недействительная подпись Telegram')
        user = service.telegram_user(str(body.id), ' '.join(filter(None, (body.first_name, body.last_name))) or None)
        if str(body.id) in settings.admin_user_ids and user['role'] != 'admin':
            db.execute('UPDATE users SET role=? WHERE id=?', ('admin', user['id']))
            user['role'] = 'admin'
        token = service.create_session(user['id'])
        response.set_cookie('user_session', token, httponly=True, secure=settings.admin_cookie_secure,
                            samesite='lax', max_age=7 * 86400, path='/')
        response.set_cookie('user_csrf', secrets.token_urlsafe(32), secure=settings.admin_cookie_secure,
                            samesite='lax', max_age=7 * 86400, path='/')
        return {'id': user['id'], 'role': user['role'], 'display_name': user['display_name']}

    @router.post('/api/v1/auth/logout')
    def logout(request: Request, response: Response):
        check_browser_origin(request, settings)
        current(request)
        if not (request.cookies.get('user_csrf') and
                hmac.compare_digest(request.cookies['user_csrf'], request.headers.get('x-csrf-token', ''))):
            raise HTTPException(403, 'Проверка CSRF не пройдена')
        service.logout(request.cookies.get('user_session', ''))
        response.delete_cookie('user_session', path='/')
        response.delete_cookie('user_csrf', path='/')
        return {'ok': True}

    @router.get('/api/v1/auth/session')
    @router.get('/api/v1/users/me')
    def me(request: Request):
        user = current(request)
        return {'id': user['id'], 'role': user['role'], 'display_name': user['display_name']}

    @router.get('/api/v1/users/me/history')
    def history(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return service.history(current(request)['id'], page, page_size)

    @router.get('/api/v1/users')
    def list_users(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        require_platform_admin(request)
        total = db.fetchone('SELECT COUNT(*) AS n FROM users')['n']
        rows = db.fetchall('SELECT id, role, display_name, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?',
                           (page_size, (page - 1) * page_size))
        return {'items': rows, 'total': total, 'page': page, 'page_size': page_size}

    class RoleChange(BaseModel):
        role: str

    @router.patch('/api/v1/users/{user_id}/role')
    def change_role(user_id: str, body: RoleChange, request: Request):
        check_browser_origin(request, settings)
        actor, legacy = require_platform_admin(request)
        if body.role not in ('user', 'moderator', 'admin') or (actor and actor['id'] == user_id):
            raise HTTPException(422, 'Недопустимое изменение роли')
        csrf = request.cookies.get('user_csrf' if actor else 'admin_csrf', '')
        if not csrf or not hmac.compare_digest(csrf, request.headers.get('x-csrf-token', '')):
            raise HTTPException(403, 'Проверка CSRF не пройдена')
        previous = db.fetchone('SELECT role FROM users WHERE id=?', (user_id,))
        if not previous:
            raise HTTPException(404, 'Пользователь не найден')
        if previous['role'] != body.role:
            with db.transaction():
                db.execute('UPDATE users SET role=? WHERE id=?', (body.role, user_id))
                db.execute('''INSERT INTO audit_log (id, actor, action, entity_type, entity_id, changes_json, created_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?)''',
                           (str(uuid4()), f"admin:{actor['id'] if actor else legacy['username']}",
                            'change_user_role', 'user', user_id,
                            json.dumps({'role': {'old': previous['role'], 'new': body.role}}),
                            datetime.now(timezone.utc).isoformat()))
        return {'ok': True}

    @router.post('/api/v1/users/me/history/{event_id}', status_code=201)
    def record(event_id: str, request: Request):
        check_browser_origin(request, settings)
        if not (request.cookies.get('user_csrf') and
                hmac.compare_digest(request.cookies['user_csrf'], request.headers.get('x-csrf-token', ''))):
            raise HTTPException(403, 'Проверка CSRF не пройдена')
        from src.services.search_service import SearchService
        event = SearchService(db).get_event_by_id(event_id)
        if event is None:
            raise HTTPException(404, 'Событие не найдено')
        service.record_view(current(request)['id'], event.id, 'web')
        return {'ok': True}

    return router
