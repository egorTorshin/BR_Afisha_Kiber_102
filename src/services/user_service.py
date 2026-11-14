"""Shared identity, session and visit storage for web and Telegram."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.db.connection import Database


def now():
    return datetime.now(timezone.utc).isoformat()


class UserService:
    def __init__(self, db: Database):
        self.db = db

    def telegram_user(self, telegram_id: str, name: str | None = None, *, create: bool = True):
        telegram_id = str(telegram_id)
        with self.db.transaction():
            row = self.db.fetchone("""SELECT u.* FROM users u JOIN user_identities i ON i.user_id=u.id
                                      WHERE i.provider='telegram' AND i.provider_id=?""", (telegram_id,))
            if row or not create:
                return row
            user_id = str(uuid4())
            self.db.execute('INSERT INTO users (id, display_name, created_at) VALUES (?, ?, ?)',
                            (user_id, name, now()))
            self.db.execute('INSERT INTO user_identities (provider, provider_id, user_id) VALUES (?, ?, ?)',
                            ('telegram', telegram_id, user_id))
            return self.db.fetchone('SELECT * FROM users WHERE id=?', (user_id,))

    def create_session(self, user_id: str):
        token = secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)',
                        (hashlib.sha256(token.encode()).hexdigest(), user_id,
                         (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()))
        return token

    def session(self, token: str):
        if not token or len(token) > 256:
            return None
        return self.db.fetchone('''SELECT u.* FROM users u JOIN user_sessions s ON s.user_id=u.id
                                   WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?''',
                                (hashlib.sha256(token.encode()).hexdigest(), now()))

    def logout(self, token: str):
        if token:
            self.db.execute('UPDATE user_sessions SET revoked_at=? WHERE token_hash=?',
                            (now(), hashlib.sha256(token.encode()).hexdigest()))

    def record_view(self, user_id: str, event_id: str, channel: str):
        self.db.execute('INSERT INTO event_views (id, user_id, event_id, channel, viewed_at) VALUES (?, ?, ?, ?, ?)',
                        (str(uuid4()), user_id, event_id, channel, now()))

    def history(self, user_id: str, page: int, page_size: int):
        total = self.db.fetchone('SELECT COUNT(*) AS n FROM event_views WHERE user_id=?', (user_id,))['n']
        rows = self.db.fetchall('''SELECT v.id, v.event_id, v.channel, v.viewed_at, e.title, e.city
                                   FROM event_views v JOIN events e ON e.id=v.event_id
                                   WHERE v.user_id=? ORDER BY v.viewed_at DESC, v.id DESC LIMIT ? OFFSET ?''',
                                (user_id, page_size, (page - 1) * page_size))
        return {'items': rows, 'total': total, 'page': page, 'page_size': page_size}
