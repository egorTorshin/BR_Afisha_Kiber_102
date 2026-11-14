"""HTTP and database integration for the persistent agent boundary."""

import hashlib

from fastapi.testclient import TestClient

import src.http.agent as agent_api_module
from src.agent.decision_router import RouterDecision
from src.agent.repositories import SqlAgentTraceRepository, SqlDialogueStateRepository
from src.agent.schemas import ConstraintSet, ModelRoute
from src.api import create_app
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from tests.test_agent_orchestrator import Light, Transport, call, light
from tests.test_backend import MIGRATIONS, event


def router(route=ModelRoute.LIGHT, fail=False):
    class ScriptedRouter:
        def decide(self, features):
            return RouterDecision(
                router_payload=features.to_router_payload(),
                route=route,
                reason_code="router_failure" if fail else "simple_search",
                latency_ms=0,
                router_error="timeout" if fail else None,
                fallback=fail,
            )

    return ScriptedRouter()


def setup_db(tmp_path):
    db = Database(str(tmp_path / "agent.db"))
    apply_migrations(db, MIGRATIONS)
    ImportService(db).create_event_manual(event())
    return db


def settings(**changes):
    values = {
        "admin_username": "admin",
        "admin_password": "pass",
        "moderator_username": "moderator",
        "moderator_password": "moderator-pass",
        "admin_cookie_secure": False,
        "ai_mode": "local-ai",
        "rate_limit_secret": "test-rate-limit-secret-with-32-characters",
        "model_prices": {"light-test": {"input": "1", "output": "2"}},
    }
    values.update(changes)
    return Settings(**values)


def test_chat_persists_state_trace_and_server_side_session(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    decisions = Light([
        light(args={"city": "Уфа"}, constraints=ConstraintSet(city="Уфа")),
        light(args={"city": "Уфа", "offset": 1}, constraints=ConstraintSet(city="Уфа")),
    ])
    monkeypatch.setattr(agent_api_module, "DecisionRouter", lambda _: router())
    monkeypatch.setattr(agent_api_module, "LightLLMProvider", lambda *_: decisions)
    monkeypatch.setattr(agent_api_module, "HeavyLLMProvider", lambda *_: object())

    with TestClient(create_app(db, settings()), base_url="http://testserver") as client:
        first = client.post("/api/v1/agent/chat", json={"message": "Концерты в Уфе"})
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "ok" and len(body["event_cards"]) == 1
        assert set(body) == {"message", "status", "event_cards", "conversation_id", "request_id"}
        assert first.headers["x-request-id"] == body["request_id"]
        assert "httponly" in first.headers["set-cookie"].lower()
        cookie = client.cookies.get("agent_session")

        row = db.fetchone("SELECT token_hash FROM agent_sessions WHERE conversation_id = ?", (body["conversation_id"],))
        assert row["token_hash"] == hashlib.sha256(cookie.encode()).hexdigest()
        assert cookie not in row["token_hash"]

        second = client.post(
            "/api/v1/agent/chat",
            json={"message": "А ещё", "conversation_id": body["conversation_id"]},
        )
        assert second.status_code == 200
        assert second.json()["conversation_id"] == body["conversation_id"]
        assert client.post(
            "/api/v1/agent/chat", json={"message": "Тест", "conversation_id": "other"}
        ).status_code == 403

        state = SqlDialogueStateRepository(db).get(body["conversation_id"])
        assert state.active_constraints.city == "Уфа" and len(state.turns) == 4
        assert state.last_result_ids and state.shown_event_ids
        trace = SqlAgentTraceRepository(db).get(body["request_id"])
        assert trace.conversation_id == body["conversation_id"]
        assert trace.cost is not None
        assert trace.tool_calls[0].arguments == {"city": "Уфа"}
        assert trace.user_query is None
        raw = db.fetchone(
            "SELECT trace_json FROM agent_traces WHERE request_id = ?", (body["request_id"],)
        )["trace_json"]
        assert "Концерты в Уфе" not in raw
        assert cookie not in raw

        assert client.get(f"/api/v1/admin/agent/traces/{body['request_id']}").status_code == 401
        moderator_login = client.post(
            "/api/v1/admin/login", json={"username": "moderator", "password": "moderator-pass"}
        )
        assert moderator_login.status_code == 200
        assert client.get(f"/api/v1/admin/agent/traces/{body['request_id']}").status_code == 403
        client.cookies.clear()
        login = client.post("/api/v1/admin/login", json={"username": "admin", "password": "pass"})
        assert login.status_code == 200
        admin = client.get(f"/api/v1/admin/agent/traces/{body['request_id']}")
        assert admin.status_code == 200 and admin.json()["request_id"] == body["request_id"]
        assert admin.headers["cache-control"] == "no-store"

    assert SqlDialogueStateRepository(Database(str(tmp_path / "agent.db"))).get(body["conversation_id"]).turns
    next_decisions = Light([light(args={"city": "Уфа"}, constraints=ConstraintSet(city="Уфа"))])
    monkeypatch.setattr(agent_api_module, "LightLLMProvider", lambda *_: next_decisions)
    with TestClient(create_app(Database(str(tmp_path / "agent.db")), settings())) as client:
        client.cookies.set("agent_session", cookie)
        resumed = client.post(
            "/api/v1/agent/chat",
            json={"message": "Продолжим", "conversation_id": body["conversation_id"]},
        )
        assert resumed.status_code == 200 and resumed.json()["conversation_id"] == body["conversation_id"]
        assert len(SqlDialogueStateRepository(db).get(body["conversation_id"]).turns) == 6
    db.close()


def test_confirmation_is_exact_persistent_and_idempotent(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    monkeypatch.setattr(agent_api_module, "DecisionRouter", lambda _: router(ModelRoute.HEAVY))
    from src.agent.heavy_llm import HeavyLLMProvider

    monkeypatch.setattr(
        agent_api_module,
        "HeavyLLMProvider",
        lambda cfg, registry: HeavyLLMProvider(
            cfg, registry, transport=Transport([call("subscribe", {"city": "Уфа", "category": "концерт"})])
        ),
    )
    monkeypatch.setattr(agent_api_module, "LightLLMProvider", lambda *_: object())
    with TestClient(create_app(db, settings())) as client:
        pending = client.post(
            "/api/v1/agent/chat", json={"message": "Подпиши на концерты SENSITIVE_TOKEN_123"}
        )
        assert pending.status_code == 200 and pending.json()["status"] == "confirmation_required"
        conversation_id = pending.json()["conversation_id"]
        raw_trace = db.fetchone(
            "SELECT trace_json FROM agent_traces WHERE request_id = ?", (pending.json()["request_id"],)
        )["trace_json"]
        assert "SENSITIVE_TOKEN_123" not in raw_trace

        assert db.fetchone("SELECT COUNT(*) n FROM subscriptions")["n"] == 0

    # A fresh process can resume only with the same browser bearer cookie.
    with TestClient(create_app(db, settings())) as client:
        client.cookies.set("agent_session", pending.cookies.get("agent_session"))
        confirmed = client.post(
            "/api/v1/agent/chat", json={"message": "Да", "conversation_id": conversation_id}
        )
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "ok"
        assert SqlDialogueStateRepository(db).get(conversation_id).pending_confirmation is None
        assert db.fetchone("SELECT COUNT(*) n FROM subscriptions")["n"] == 1
        repeated = client.post(
            "/api/v1/agent/chat", json={"message": "Да", "conversation_id": conversation_id}
        )
        assert repeated.status_code == 200
        assert db.fetchone("SELECT COUNT(*) n FROM subscriptions")["n"] == 1
    db.close()


def test_foreign_origin_is_rejected_and_cleanup_preserves_trace(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    monkeypatch.setattr(agent_api_module, "DecisionRouter", lambda _: router(fail=True))
    cfg = settings(web_origin="https://app.example", cors_origins=["https://app.example"])
    with TestClient(create_app(db, cfg)) as client:
        denied = client.post(
            "/api/v1/agent/chat",
            headers={"Origin": "https://evil.example"},
            json={"message": "Найди концерты"},
        )
        assert denied.status_code == 403
        body = client.post("/api/v1/agent/chat", json={"message": "Найди концерты"}).json()
        assert body["status"] == "fallback"

    db.execute(
        "UPDATE agent_dialogue_states SET expires_at = ? WHERE conversation_id = ?",
        ("2000-01-01T00:00:00+00:00", body["conversation_id"]),
    )
    assert SqlDialogueStateRepository(db).cleanup_expired() == 1
    assert SqlAgentTraceRepository(db).get(body["request_id"]) is not None
    db.close()


def test_action_claim_is_atomic_and_expires_with_dialogue(tmp_path):
    db = setup_db(tmp_path)
    repository = SqlDialogueStateRepository(db)
    assert repository.claim_action("conversation", "same-action") is True
    assert repository.claim_action("conversation", "same-action") is False
    db.execute(
        "UPDATE agent_action_receipts SET expires_at = ? WHERE conversation_id = ?",
        ("2000-01-01T00:00:00+00:00", "conversation"),
    )
    repository.cleanup_expired()
    assert db.fetchone(
        "SELECT action_key FROM agent_action_receipts WHERE conversation_id = ?", ("conversation",)
    ) is None
    db.close()
