"""Database-backed administrator sessions and request quotas."""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.db.connection import Database


SESSION_SECONDS = 8 * 60 * 60


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str, *, iterations: int = 600_000) -> str:
    """Generate a value suitable for ADMIN_PASSWORD_HASH or MODERATOR_PASSWORD_HASH."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{salt.hex()}:{digest.hex()}"


def valid_password_hash(password_hash: str) -> bool:
    try:
        algorithm, rounds, salt, expected = password_hash.split(":", 3)
        return (algorithm == "pbkdf2_sha256" and 100_000 <= int(rounds) <= 2_000_000
                and len(bytes.fromhex(salt)) >= 16 and len(bytes.fromhex(expected)) == 32)
    except (ValueError, TypeError):
        return False


def _password_matches(password: str, password_hash: str) -> bool:
    if not valid_password_hash(password_hash):
        return False
    _, rounds, salt, expected = password_hash.split(":", 3)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(actual, bytes.fromhex(expected))


class AdminAuth:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def verify_credentials(self, username: str, password: str) -> str | None:
        accounts = (
            (self.settings.admin_username, self.settings.admin_password, self.settings.admin_password_hash, "admin"),
            (self.settings.moderator_username, self.settings.moderator_password,
             self.settings.moderator_password_hash, "moderator"),
        )
        for expected_user, plaintext, password_hash, role in accounts:
            password_ok = (_password_matches(password, password_hash) if password_hash else
                           bool(plaintext) and hmac.compare_digest(password.encode(), plaintext.encode()))
            if expected_user and hmac.compare_digest(username.encode(), expected_user.encode()) and password_ok:
                return role
        return None

    def _credential_hash(self, role: str) -> str:
        credential = ((self.settings.admin_password_hash or self.settings.admin_password) if role == "admin" else
                      (self.settings.moderator_password_hash or self.settings.moderator_password))
        secret = self.settings.admin_secret or self.settings.rate_limit_secret
        return hmac.new(secret.encode(), f"{role}:{credential}".encode(), hashlib.sha256).hexdigest()

    def create_session(self, username: str, role: str) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        self.db.execute(
            """INSERT INTO admin_sessions
               (token_hash, csrf_hash, username, role, credential_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_digest(token), _digest(csrf), username, role, self._credential_hash(role), now.isoformat(),
             (now + timedelta(seconds=SESSION_SECONDS)).isoformat()),
        )
        return token, csrf

    def get_session(self, token: str) -> dict | None:
        if not token or len(token) > 256:
            return None
        session = self.db.fetchone(
            """SELECT username, role, csrf_hash, credential_hash FROM admin_sessions
               WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?""",
            (_digest(token), datetime.now(timezone.utc).isoformat()),
        )
        if not session or not hmac.compare_digest(session["credential_hash"], self._credential_hash(session["role"])):
            return None
        return session

    def revoke(self, token: str) -> None:
        if token:
            self.db.execute(
                "UPDATE admin_sessions SET revoked_at = ? WHERE token_hash = ?",
                (datetime.now(timezone.utc).isoformat(), _digest(token)),
            )

    def check_rate(self, category: str, identity: str, limit: int, window_seconds: int) -> bool:
        key = _digest(f"{category}:{identity}")
        now = int(time.time())
        start = now - now % window_seconds
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO request_limits (key_hash, window_start, attempts) VALUES (?, ?, 1)
                   ON CONFLICT (key_hash) DO UPDATE SET
                       attempts = CASE WHEN request_limits.window_start = ?
                           THEN request_limits.attempts + 1 ELSE 1 END,
                       window_start = ?""",
                (key, start, start, start),
            )
            attempts = self.db.fetchone("SELECT attempts FROM request_limits WHERE key_hash = ?", (key,))["attempts"]
        return attempts <= limit
