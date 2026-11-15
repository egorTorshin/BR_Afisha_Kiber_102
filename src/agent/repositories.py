"""Storage boundaries for dialogue context and sanitized agent telemetry."""

import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import MutableMapping, Protocol

from src.agent.state import DialogueState
from src.agent.trace import AgentTrace
from src.db.connection import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DialogueStateRepository(Protocol):
    def get(self, conversation_id: str) -> DialogueState | None: ...
    def save(self, state: DialogueState) -> None: ...
    def claim_action(self, conversation_id: str, action_key: str) -> bool: ...
    def cleanup_expired(self) -> int: ...


class AgentTraceRepository(Protocol):
    def get(self, request_id: str) -> AgentTrace | None: ...
    def save(self, conversation_id: str, trace: AgentTrace) -> None: ...


class MemoryDialogueStateRepository:
    def __init__(self, states: MutableMapping[str, DialogueState] | None = None):
        self.states = states if states is not None else {}
        self.claimed_actions: set[tuple[str, str]] = set()

    def get(self, conversation_id):
        return self.states.get(conversation_id)

    def save(self, state):
        self.states[state.conversation_id] = state

    def claim_action(self, conversation_id, action_key):
        identity = (conversation_id, action_key)
        if identity in self.claimed_actions:
            return False
        self.claimed_actions.add(identity)
        return True

    def cleanup_expired(self):
        return 0


class MemoryAgentTraceRepository:
    def __init__(self, traces: MutableMapping[str, AgentTrace] | None = None):
        self.traces = traces if traces is not None else {}

    def get(self, request_id):
        return self.traces.get(request_id)

    def save(self, conversation_id, trace):
        self.traces[trace.trace_id] = trace


class SqlDialogueStateRepository:
    def __init__(self, db: Database, ttl_days: int = 30):
        self.db = db
        self.ttl = timedelta(days=ttl_days)

    def get(self, conversation_id):
        row = self.db.fetchone("SELECT state_json FROM agent_dialogue_states WHERE conversation_id = ? AND expires_at > ?",
                               (conversation_id, utc_now().isoformat()))
        return DialogueState.model_validate_json(row["state_json"]) if row else None

    def save(self, state):
        now = utc_now().isoformat()
        expires = (utc_now() + self.ttl).isoformat()
        # The model retains at least five recent turns; bound stored personal text.
        state.turns = state.turns[-20:]
        payload = state.model_dump_json()
        with self.db.transaction():
            self.db.execute("INSERT INTO agent_dialogue_states (conversation_id, state_json, created_at, updated_at, expires_at) "
                            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
                            "state_json = excluded.state_json, updated_at = excluded.updated_at, expires_at = excluded.expires_at",
                            (state.conversation_id, payload, now, now, expires))

    def claim_action(self, conversation_id, action_key):
        now = utc_now()
        with self.db.transaction():
            return self.db.execute(
                "INSERT INTO agent_action_receipts "
                "(conversation_id, action_key, created_at, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(conversation_id, action_key) DO NOTHING",
                (conversation_id, action_key, now.isoformat(), (now + self.ttl).isoformat()),
            ) == 1

    def cleanup_expired(self):
        now = utc_now().isoformat()
        with self.db.transaction():
            states = self.db.execute("DELETE FROM agent_dialogue_states WHERE expires_at <= ?", (now,))
            self.db.execute("DELETE FROM agent_action_receipts WHERE expires_at <= ?", (now,))
        return states


class SqlAgentTraceRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, request_id):
        row = self.db.fetchone("SELECT trace_json FROM agent_traces WHERE request_id = ?", (request_id,))
        return AgentTrace.model_validate_json(row["trace_json"]) if row else None

    def save(self, conversation_id, trace):
        self.db.execute("INSERT INTO agent_traces (request_id, conversation_id, trace_json, created_at) VALUES (?, ?, ?, ?)",
                        (trace.trace_id, conversation_id, trace.model_dump_json(), utc_now().isoformat()))

    def update(self, conversation_id, trace):
        self.db.execute(
            "UPDATE agent_traces SET conversation_id = ?, trace_json = ? WHERE request_id = ?",
            (conversation_id, trace.model_dump_json(), trace.trace_id),
        )


class AgentSessionRepository:
    """Server-side anonymous browser sessions; raw bearer tokens never reach the database."""

    def __init__(self, db: Database, ttl_days: int = 30, principal_secret: str = ""):
        self.db = db
        self.ttl = timedelta(days=ttl_days)
        self.principal_secret = principal_secret

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def resolve(self, token: str) -> str | None:
        if not token or len(token) > 256:
            return None
        row = self.db.fetchone(
            "SELECT conversation_id FROM agent_sessions WHERE token_hash = ? AND expires_at > ?",
            (self._digest(token), utc_now().isoformat()),
        )
        return row["conversation_id"] if row else None

    def create(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        conversation_id = secrets.token_hex(16)
        now = utc_now()
        self.db.execute(
            "INSERT INTO agent_sessions (token_hash, conversation_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (self._digest(token), conversation_id, now.isoformat(), (now + self.ttl).isoformat()),
        )
        return token, conversation_id

    def _principal_token(self, principal: str) -> str:
        """Derive a stable opaque session token without storing a raw user identifier."""
        if not self.principal_secret:
            raise ValueError("principal_secret is required for persistent principal sessions")
        normalized = str(principal).strip()[:256]
        return hmac.new(
            self.principal_secret.encode("utf-8"),
            ("agent-principal:" + normalized).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def get_or_create_principal(self, principal: str) -> tuple[str, str]:
        """Return a stable server-side conversation for a trusted external principal."""
        token = self._principal_token(principal)
        conversation_id = self.resolve(token)
        if conversation_id:
            self.touch(token)
            return token, conversation_id
        conversation_id = secrets.token_hex(16)
        now = utc_now()
        self.db.execute(
            "INSERT INTO agent_sessions (token_hash, conversation_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (self._digest(token), conversation_id, now.isoformat(), (now + self.ttl).isoformat()),
        )
        return token, conversation_id

    def touch(self, token: str) -> None:
        self.db.execute("UPDATE agent_sessions SET expires_at = ? WHERE token_hash = ?",
                        ((utc_now() + self.ttl).isoformat(), self._digest(token)))

    def cleanup_expired(self) -> int:
        return self.db.execute("DELETE FROM agent_sessions WHERE expires_at <= ?", (utc_now().isoformat(),))

