import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.services.import_service import ImportService
from tests.test_backend import db, empty_db, event


@pytest.fixture
def catalog(db):
    service = ImportService(db)
    records = {}
    for name, changes in {
        'free': dict(title='Семейная музыка', price_min=0, date_start='2026-11-20T10:00:00', age_limit=0),
        'paid': dict(title='Вечер музыки', price_min=300, date_start='2026-11-21T18:00:00', age_limit=12),
        'unknown': dict(title='Новая выставка', category='выставка', city='Салават', venue='Музей', date_start='2026-11-22T12:00:00'),
        'draft': dict(title='Скрытый концерт', status='draft', city='Скрытый город', venue='Скрытая площадка'),
        'cancelled': dict(title='Отменённый концерт', status='cancelled'),
    }.items():
        _, records[name] = service.create_event_manual(event(**changes))
    return records


@pytest.fixture
def client(db, catalog):
    settings = Settings(cors_origins=['https://client.example'], public_cache_max_age=25)
    with TestClient(create_app(db, settings), raise_server_exceptions=False) as client:
        yield client


def test_routes_and_visibility(client, catalog):
    assert client.get('/health').json() == {'status': 'ok'}
    for path in ('/events', '/api/v1/events'):
        response = client.get(path)
        assert response.status_code == 200
        assert {e['id'] for e in response.json()['events']} == {catalog[k].id for k in ('free', 'paid', 'unknown')}
    for prefix in ('/events/', '/api/v1/events/'):
        assert client.get(prefix + catalog['draft'].id).status_code == 404
        response = client.get(prefix + catalog['cancelled'].id)
        assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    assert client.get('/api/v1/cities').json() == [{'city': 'Уфа', 'cnt': 2}, {'city': 'Салават', 'cnt': 1}]
    assert client.get('/api/v1/categories', params={'city': 'Салават'}).json() == [{'category': 'выставка', 'cnt': 1}]
    venues = client.get('/api/v1/venues', params={'city': 'Салават'}).json()
    assert venues == [{'city': 'Салават', 'name': 'Музей', 'address': 'ул. Ленина, 1'}]


@pytest.mark.parametrize('params,keys', [
    ({'query_text': 'семейная'}, ['free']),
    ({'city': 'Салават'}, ['unknown']),
    ({'category': 'выставка'}, ['unknown']),
    ({'venue': 'Музей'}, ['unknown']),
    ({'date_from': '2026-11-21', 'date_to': '2026-11-21'}, ['paid']),
    ({'max_price': 300}, ['free', 'paid']),
    ({'is_free_only': True}, ['free']),
    ({'max_age_limit': 0}, ['free', 'unknown']),
    ({'city': 'Уфа', 'max_age_limit': 0, 'max_price': 0}, ['free']),
    ({'date_from': '2026-11-21T13:00:00Z', 'date_to': '2026-11-21T13:00:00Z'}, ['paid']),
])
def test_filters(client, catalog, params, keys):
    response = client.get('/api/v1/events', params=params)
    assert response.status_code == 200, response.text
    assert [e['id'] for e in response.json()['events']] == [catalog[k].id for k in keys]


@pytest.mark.parametrize('sort,keys', [('date_asc', ['free','paid','unknown']),
    ('date_desc', ['unknown','paid','free']), ('price_asc', ['free','paid','unknown'])])
def test_sort_and_pages(client, catalog, sort, keys):
    for page in (1, 2):
        body = client.get('/api/v1/events', params={'sort': sort, 'page_size': 2, 'page': page}).json()
        assert (body['total'], body['pages'], body['page'], body['page_size']) == (3, 2, page, 2)
        assert [e['id'] for e in body['events']] == [catalog[k].id for k in keys[(page-1)*2:page*2]]
    assert client.get('/api/v1/events?page=3&page_size=2').json()['events'] == []
    empty = client.get('/api/v1/events?query_text=несуществующий').json()
    assert empty['total'] == empty['pages'] == 0
    legacy = client.get('/events?limit=1&offset=1&sort=date_desc').json()
    assert legacy['total'] == 3 and legacy['events'][0]['id'] == catalog['paid'].id


@pytest.mark.parametrize('params', [
    {'date_from':'bad'}, {'date_to':'2026-02-30'}, {'date_from':'2026-12-01','date_to':'2026-11-01'},
    {'time_after':'25:00'}, {'time_before':'9:00'}, {'sort':'invalid'},
    {'page':0}, {'page_size':101}, {'max_price':-1}, {'max_age_limit':19},
])
def test_validation_errors(client, params):
    response = client.get('/api/v1/events', params=params)
    assert response.status_code == 422
    body = response.json()
    assert body['error']['code'] == 'HTTP_422'
    assert body['error']['details'] and body['error']['message']
    assert body['request_id'] == response.headers['x-request-id']
    assert response.headers['cache-control'] == 'no-store'


def test_errors_cors_cache_request_id(client, db, monkeypatch):
    headers = {'Origin':'https://client.example'}
    successful = client.get('/api/v1/events', headers=headers)
    assert successful.headers['cache-control'] == 'public, max-age=25, must-revalidate'
    assert successful.headers['access-control-allow-origin'] == headers['Origin']
    assert 'X-Request-ID' in successful.headers['access-control-expose-headers']
    preflight = client.options('/api/v1/events', headers={**headers, 'Access-Control-Request-Method':'GET'})
    assert preflight.status_code == 200
    assert preflight.headers['x-request-id'].startswith('req-')
    assert 'access-control-allow-origin' not in client.get('/health', headers={'Origin':'https://evil.example'}).headers
    for path in ('/missing', '/api/v1/events/missing'):
        response = client.get(path, headers=headers)
        assert response.status_code == 404
        assert response.json()['error']['code'] == 'HTTP_404'
        assert response.json()['request_id'] == response.headers['x-request-id']
        assert response.headers['cache-control'] == 'no-store'
    def fail(*args, **kwargs):
        raise RuntimeError('private database secret')
    monkeypatch.setattr(db, 'fetchone', fail)
    response = client.get('/health', headers=headers)
    assert response.status_code == 500
    assert response.json()['error']['message'] == 'Внутренняя ошибка сервера'
    assert 'secret' not in response.text
    assert response.json()['request_id'] == response.headers['x-request-id']
    assert response.headers['x-request-id'] != successful.headers['x-request-id']
    assert response.headers['access-control-allow-origin'] == headers['Origin']


def test_openapi_and_docs(client):
    schema = client.get('/openapi.json').json()
    assert schema['openapi'] == '3.1.0'
    assert client.get('/docs').status_code == 200
    paths = schema['paths']
    assert {'/health', '/api/v1/events', '/api/v1/events/{event_id}', '/api/v1/cities', '/api/v1/categories', '/api/v1/venues'} <= paths.keys()
    assert paths['/api/v1/events']['get']['responses']['422']['content']['application/json']['schema']['$ref'].endswith('/ErrorResponse')
    assert {'page','page_size','sort'} <= {p['name'] for p in paths['/api/v1/events']['get']['parameters']}


def test_versioned_feedback_and_subscriptions(client, catalog):
    feedback = client.post('/api/v1/feedback', json={'event_id': catalog['free'].id, 'message': 'Проверьте дату'})
    assert feedback.status_code == 201
    assert feedback.json()['event_id'] == catalog['free'].id
    payload = {'subscriber_id': 'browser-test', 'city': 'Уфа', 'category': 'концерт'}
    created = client.post('/api/v1/subscriptions', json=payload)
    assert created.status_code == 201
    sub_id = created.json()['id']
    listed = client.get('/api/v1/subscriptions', params={'subscriber_id': 'browser-test'})
    assert listed.status_code == 200 and [s['id'] for s in listed.json()] == [sub_id]
    invalid = client.post('/api/v1/subscriptions', json={**payload, 'period': {
        'date_from': '2026-12-02', 'date_to': '2026-12-01'}})
    assert invalid.status_code == 422
    updated = client.post('/api/v1/subscriptions', json={**payload, 'period': {
        'date_from': '2026-11-20', 'date_to': '2026-11-30'}})
    assert updated.status_code == 201 and not updated.json()['is_new']
    assert updated.json()['period_from'] == '2026-11-20'
    assert updated.json()['expires_at'] != updated.json()['period_to']
    assert client.delete(f'/api/v1/subscriptions/{sub_id}', params={'subscriber_id': 'someone-else'}).status_code == 404
    assert client.delete(f'/api/v1/subscriptions/{sub_id}', params={'subscriber_id': 'browser-test'}).json() == {'deleted': 1}
    client.post('/api/v1/subscriptions', json=payload)
    assert client.delete('/api/v1/subscriptions', params={'subscriber_id': 'browser-test'}).json() == {'deleted': 1}
    assert client.get('/api/v1/subscriptions', params={'subscriber_id': 'browser-test'}).json() == []


def test_price_sort_is_independent_of_date(client, db, catalog):
    db.execute('UPDATE events SET date_start = ? WHERE id = ?', ('2026-11-18T10:00:00', catalog['unknown'].id))
    db.execute('UPDATE events SET date_start = ? WHERE id = ?', ('2026-11-19T10:00:00', catalog['paid'].id))
    body = client.get('/api/v1/events?sort=price_asc').json()
    assert [e['id'] for e in body['events']] == [catalog[k].id for k in ('free', 'paid', 'unknown')]
    assert body['events'][-1]['price_min'] is None


def test_request_id_is_used_by_existing_logger(client, monkeypatch):
    from src.utils.logger import get_request_id
    seen = []
    def capture(*args, **kwargs):
        seen.append(get_request_id())
    monkeypatch.setattr('src.api.log_event', capture)
    first = client.get('/health')
    second = client.get('/health')
    assert seen == [first.headers['x-request-id'], second.headers['x-request-id']]
    assert len(set(seen)) == 2


def test_admin_auth_cookies_csrf_and_moderation_cannot_be_bypassed(db, catalog):
    settings = Settings(admin_username='reviewer', admin_password='test-password', admin_cookie_secure=True)
    with TestClient(create_app(db, settings), base_url='https://testserver') as client:
        protected = ('/api/v1/admin/session', '/api/v1/admin/events',
                     '/api/v1/admin/moderation', '/api/v1/admin/audit',
                     '/api/v1/admin/feedback')
        for path in protected:
            assert client.get(path).status_code == 401
        assert client.patch(f"/api/v1/admin/events/{catalog['free'].id}",
                            json={'status': 'draft'}).status_code == 401

        login = client.post('/api/v1/admin/login', json={'username': 'reviewer', 'password': 'test-password'})
        assert login.status_code == 200
        cookies = {cookie.name: cookie for cookie in client.cookies.jar}
        assert cookies['admin_session'].has_nonstandard_attr('HttpOnly')
        assert cookies['admin_session'].secure and cookies['admin_session'].has_nonstandard_attr('SameSite')
        assert cookies['admin_csrf'].secure

        event_id = catalog['free'].id
        bypass = client.patch(f'/api/v1/admin/events/{event_id}', json={'status': 'draft'},
                              headers={'X-CSRF-Token': cookies['admin_csrf'].value})
        assert bypass.status_code == 422
        unchanged = db.fetchone('SELECT status, status_locked FROM events WHERE id = ?', (event_id,))
        assert (unchanged['status'], unchanged['status_locked']) == ('published', 0)

        no_csrf = client.patch(f'/api/v1/admin/events/{event_id}', json={'title': 'Новое название'})
        assert no_csrf.status_code == 403
        moderated = client.patch(f'/api/v1/admin/events/{event_id}/status', json={'status': 'draft'},
                                 headers={'X-CSRF-Token': cookies['admin_csrf'].value})
        assert moderated.status_code == 200
        saved = db.fetchone('SELECT status, status_locked FROM events WHERE id = ?', (event_id,))
        assert (saved['status'], saved['status_locked']) == ('draft', 1)
        assert client.post('/api/v1/admin/logout').status_code == 403
        assert client.post('/api/v1/admin/logout', headers={'X-CSRF-Token': cookies['admin_csrf'].value}).status_code == 200
        assert client.get('/api/v1/admin/session').status_code == 401


def test_admin_cookies_support_local_http_without_disabling_production_security(db):
    settings = Settings(admin_username='reviewer', admin_password='test-password', admin_cookie_secure=False)
    with TestClient(create_app(db, settings), base_url='http://testserver') as client:
        login = client.post('/api/v1/admin/login', json={'username': 'reviewer', 'password': 'test-password'})
        assert login.status_code == 200
        cookies = {cookie.name: cookie for cookie in client.cookies.jar}
        assert not cookies['admin_session'].secure
        assert cookies['admin_session'].has_nonstandard_attr('HttpOnly')
        assert not cookies['admin_csrf'].secure
        assert client.get('/api/v1/admin/session').status_code == 200
