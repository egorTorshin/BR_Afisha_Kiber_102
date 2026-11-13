"""Wire-level local inference and outage smoke checks without downloading weights."""

import json

import httpx
from fastapi.testclient import TestClient

from src.agent.local_transport import LocalHeavyTransport, LocalLightTransport
from src.agent.decision_router import DecisionRouter
from src.agent.router_service import create_app as create_router_app
from src.agent.schemas import RoutingFeatures
from src.api import create_app
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from tests.test_backend import MIGRATIONS, event


def test_router_health_light_heavy_and_safe_input():
    seen = []

    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        seen.append(payload)
        heavy = payload["requires_multi_step"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "route": "HEAVY" if heavy else "LIGHT", "confidence": 0.95,
            "reason_code": "multi_step_planning" if heavy else "simple_search",
        })}}]})

    upstream = httpx.Client(transport=httpx.MockTransport(handle))
    with TestClient(create_router_app(upstream)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        simple = RoutingFeatures(intent="search_events").to_router_payload()
        complex_ = RoutingFeatures(intent="planner", requires_multi_step=True).to_router_payload()
        assert client.post("/decide", json=simple).json()["route"] == "LIGHT"
        assert client.post("/decide", json=complex_).json()["route"] == "HEAVY"
        assert client.post("/decide", json={**simple, "message": "private"}).status_code == 422
    assert len(seen) == 2 and all(set(row) == set(RoutingFeatures.model_fields) for row in seen)


def test_light_structured_and_heavy_tool_turn():
    seen = []
    light = {"intent": "search_events", "constraint_patch": {},
             "tool_proposal": "search_events", "clarification": None}

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        if body["model"] == "light":
            content = json.dumps(light)
            message = {"content": content}
        else:
            message = {"tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "search_events", "arguments": "{}"}}], "content": None}
        return httpx.Response(200, json={"choices": [{"message": message}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    result, input_tokens, output_tokens = LocalLightTransport("http://llm/v1", client=http).parse(
        model="light", payload={"message": "концерты"}, timeout=2)
    assert result.tool_proposal == "search_events" and (input_tokens, output_tokens) == (10, 20)
    from src.agent.tools import SearchEventsInput
    heavy = LocalHeavyTransport("http://llm/v1", client=http).step(
        model="heavy", messages=[{"role": "user", "content": "Найди события"}],
        schemas={"search_events": SearchEventsInput}, timeout=2)
    assert heavy.calls[0].name == "search_events" and heavy.calls[0].arguments == {}
    assert seen[0]["max_tokens"] == 384 and seen[1]["max_tokens"] == 512
    followup = [{"role": "user", "content": "Найди события"}, *heavy.output_items,
                {"type": "function_call_output", "call_id": "c1", "output": "UNTRUSTED_DATA"}]
    LocalHeavyTransport("http://llm/v1", client=http).step(
        model="heavy", messages=followup, schemas={"search_events": SearchEventsInput}, timeout=2)
    assert seen[2]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "UNTRUSTED_DATA"}


def test_no_ai_and_local_model_outage_return_http_200(tmp_path):
    class ForbiddenJev:
        def decide(self, payload):
            raise AssertionError("no-ai must never contact Jev")

    assert DecisionRouter(Settings(ai_mode="no-ai", jev_enabled=True),
                          jev=ForbiddenJev()).decide(RoutingFeatures(intent="search_events")).fallback
    db = Database(str(tmp_path / "smoke.db"))
    apply_migrations(db, MIGRATIONS)
    ImportService(db).create_event_manual(event())
    for mode in ("no-ai", "local-ai"):
        settings = Settings(ai_mode=mode, jev_enabled=False,
                            local_llm_base_url="http://127.0.0.1:1/v1",
                            light_timeout_seconds=0.2, heavy_timeout_seconds=0.2,
                            light_model="qwen2.5-3b-local", heavy_model="qwen2.5-3b-local")
        with TestClient(create_app(db, settings), raise_server_exceptions=False) as client:
            response = client.post("/api/v1/agent/chat", json={"message": "Найди концерты в Уфе"})
            assert response.status_code == 200
            assert response.json()["status"] == "fallback"
            assert response.json()["event_cards"]
    db.close()

