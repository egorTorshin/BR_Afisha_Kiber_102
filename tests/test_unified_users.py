import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.services.import_service import ImportService
from src.services.user_service import UserService
from tests.test_backend import db, empty_db, event


def signed(telegram_id, token):
    data = {'id': telegram_id, 'auth_date': int(time.time()), 'first_name': 'Тест'}
    check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
    data['hash'] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


def test_telegram_login_shared_history_and_role_isolation(db):
    _, item = ImportService(db).create_event_manual(event())
    service = UserService(db)
    bot_user = service.telegram_user('123', 'Из бота')
    service.record_view(bot_user['id'], item.id, 'telegram')
    settings = Settings(telegram_bot_token='test-token', admin_cookie_secure=False)
    with TestClient(create_app(db, settings)) as client:
        assert client.post('/api/v1/auth/telegram', json={**signed(123, 'bad-token')}).status_code == 401
        login = client.post('/api/v1/auth/telegram', json=signed(123, 'test-token'))
        assert login.status_code == 200
        assert login.json()['id'] == bot_user['id']
        csrf = client.cookies.get('user_csrf')
        assert client.post(f'/api/v1/users/me/history/{item.id}', headers={'X-CSRF-Token': csrf}).status_code == 201
        history = client.get('/api/v1/users/me/history?page_size=1').json()
        assert history['total'] == 2 and len(history['items']) == 1
        assert client.get('/api/v1/users').status_code == 403
        assert client.get('/api/v1/admin/events').status_code == 403
        assert client.post('/api/v1/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 200
        assert client.get('/api/v1/users/me/history').status_code == 401
        assert client.post('/api/v1/auth/telegram', json=signed(123, 'test-token')).json()['id'] == bot_user['id']


def test_admin_role_can_manage_users_and_moderator_cannot(db):
    settings = Settings(telegram_bot_token='test-token', admin_cookie_secure=False, admin_user_ids=['1'])
    with TestClient(create_app(db, settings)) as client:
        admin = client.post('/api/v1/auth/telegram', json=signed(1, 'test-token')).json()
        assert admin['role'] == 'admin'
        client.cookies.clear()
        target = client.post('/api/v1/auth/telegram', json=signed(2, 'test-token')).json()
        assert client.get('/api/v1/users').status_code == 403
        client.cookies.clear()
        client.post('/api/v1/auth/telegram', json=signed(1, 'test-token'))
        csrf = client.cookies.get('user_csrf')
        assert client.patch(f"/api/v1/users/{target['id']}/role", json={'role': 'moderator'},
                            headers={'X-CSRF-Token': csrf}).status_code == 200
        client.cookies.clear()
        assert client.post('/api/v1/auth/telegram', json=signed(2, 'test-token')).json()['role'] == 'moderator'
        assert client.get('/api/v1/admin/events').status_code == 200
        assert client.get('/api/v1/users').status_code == 403
