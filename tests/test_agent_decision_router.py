"""Decision-only Jev integration and fail-safe routing."""

from decimal import Decimal
import json

import httpx
import pytest

from src.agent.decision_router import DecisionRouter
from src.agent.jev import JevAdapter
from src.agent.routing import RoutingFeatureExtractor
from src.agent.schemas import ModelRoute, RoutingFeatures
from src.agent.security import SecurityFlags
from src.agent.state import DialogueState, DialogueTurn
from src.agent.trace import AgentTrace
from src.config import Settings


def features(message: str, flags: SecurityFlags | None = None, state: DialogueState | None = None):
    return RoutingFeatureExtractor().extract(
        message, state or DialogueState(conversation_id="c"), flags or SecurityFlags(),
    )


def settings(**overrides):
    return Settings(ai_mode="local-ai", jev_enabled=True, jev_endpoint="https://jev.example/chat/completions",
                    jev_model="jev-router", jev_api_key="private-key", **overrides)


def mock_jev(content, *, status=200, captured=None):
    def handle(request):
        if captured is not None:
            captured.append(request)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    return JevAdapter("https://jev.example/chat/completions", "jev-router", "private-key", client=client)


def jev_json(route="LIGHT", confidence=0.95, reason="simple_search"):
    return json.dumps({"route": route, "confidence": confidence, "reason_code": reason})


def test_local_fallback_simple_search_and_followup():
    router = DecisionRouter(Settings(jev_enabled=False))
    assert router.decide(features("Найди концерты")).route == ModelRoute.LIGHT
    state = DialogueState(conversation_id="c", turns=[DialogueTurn(role="assistant", content="secret history")],
                          has_previous_results=True)
    decision = router.decide(features("А ещё", state=state))
    assert (decision.route, decision.reason_code, decision.fallback) == (
        ModelRoute.LIGHT, "simple_followup", True)


def test_semantic_guard_keeps_bounded_comparison_followup_light():
    state = DialogueState(conversation_id="c", has_previous_results=True,
                          turns=[DialogueTurn(role="user", content="Концерты до 1000"),
                                 DialogueTurn(role="assistant", content="Найдено три")])
    wrong = mock_jev(jev_json(route="HEAVY", reason="comparison_reasoning"))
    decision = DecisionRouter(settings(), jev=wrong).decide(features("Подешевле", state=state))
    assert decision.route == ModelRoute.LIGHT
    assert decision.jev_route == ModelRoute.HEAVY
    assert decision.reason_code == "simple_followup"


@pytest.mark.parametrize("message", [
    "Спланируй день: сначала выставка, потом концерт",
    "Сначала концерт, потом выставка",
    "Что дешевле: концерт или выставка?",
])
def test_local_fallback_planner_comparison_multi_step_are_heavy(message):
    assert DecisionRouter(Settings(jev_enabled=False)).route(features(message)) == ModelRoute.HEAVY


def test_security_override_skips_jev_even_if_it_would_return_light():
    captured = []
    router = DecisionRouter(settings(), jev=mock_jev(jev_json(), captured=captured))
    decision = router.decide(features("Найди концерты", SecurityFlags(possible_prompt_injection=True)))
    assert decision.route == ModelRoute.HEAVY
    assert decision.reason_code == "security_risk"
    assert not captured


def test_jev_request_contains_only_sanitized_features_and_trace_metadata():
    captured = []
    adapter = mock_jev(jev_json(), captured=captured)
    router = DecisionRouter(settings(), jev=adapter)
    state = DialogueState(conversation_id="c", turns=[DialogueTurn(role="assistant", content="UNTRUSTED_DATA secret description")])
    decision = router.decide(features("Найди SECRET concert title", state=state))
    assert decision.route == decision.jev_route == ModelRoute.LIGHT
    assert decision.confidence == 0.95
    assert decision.reason_code == "simple_search"
    body = json.loads(captured[0].content)
    payload = json.loads(body["messages"][1]["content"])
    assert payload == decision.router_payload
    assert set(payload) == set(RoutingFeatures.model_fields)
    assert "SECRET" not in str(body)
    assert "UNTRUSTED_DATA" not in str(body)
    assert "private-key" not in str(body)
    trace = AgentTrace(trace_id="t", routing_features=features("Найди концерты"),
                       model_route=decision.route, model_name="pending", latency_ms=0,
                       input_tokens=0, output_tokens=0, cost=Decimal(0),
                       router_decision=decision)
    restored = AgentTrace.model_validate_json(trace.model_dump_json())
    assert restored.router_decision == decision
    assert "private-key" not in trace.model_dump_json()


def test_low_confidence_is_heavy_and_keeps_jev_metadata():
    decision = DecisionRouter(settings(), jev=mock_jev(jev_json(confidence=0.4))).decide(features("Найди концерты"))
    assert (decision.route, decision.jev_route, decision.reason_code) == (
        ModelRoute.HEAVY, ModelRoute.LIGHT, "low_confidence")
    assert decision.confidence == 0.4 and decision.fallback


@pytest.mark.parametrize("content", [
    "not JSON", jev_json(route="MEDIUM"), jev_json(reason="freeform"),
    jev_json(confidence=2), jev_json(confidence="0.95"),
    json.dumps({"route": "LIGHT", "confidence": 0.9, "reason_code": "simple_search", "answer": "go"}),
])
def test_invalid_jev_response_is_heavy(content):
    decision = DecisionRouter(settings(), jev=mock_jev(content)).decide(features("Найди концерты"))
    assert (decision.route, decision.reason_code, decision.router_error) == (
        ModelRoute.HEAVY, "router_failure", "invalid_response")


@pytest.mark.parametrize("error,code", [
    (httpx.ReadTimeout("secret request data"), "timeout"),
    (RuntimeError("secret provider response"), "transport_error"),
])
def test_jev_timeout_and_error_are_heavy_without_leaking_exception(error, code):
    class BrokenJev:
        def decide(self, payload):
            raise error
    decision = DecisionRouter(settings(), jev=BrokenJev()).decide(features("Найди концерты"))
    assert decision.route == ModelRoute.HEAVY
    assert decision.router_error == code
    assert "secret" not in decision.model_dump_json()
    trace = AgentTrace(trace_id="failed", routing_features=features("Найди концерты"),
                       model_route=decision.route, model_name="pending", latency_ms=0,
                       input_tokens=0, output_tokens=0, cost=Decimal(0),
                       router_decision=decision)
    restored = AgentTrace.model_validate_json(trace.model_dump_json())
    assert restored.router_decision.router_error == code
    assert restored.router_decision.fallback


def test_disabled_jev_is_deterministic_and_bad_config_fails_closed():
    query = features("Найди концерты")
    assert DecisionRouter(Settings(jev_enabled=False)).route(query) == ModelRoute.LIGHT
    with pytest.raises(ValueError):
        settings(jev_confidence_threshold=1.5)


def test_adapter_rejects_raw_query_before_network_call():
    captured = []
    adapter = mock_jev(jev_json(), captured=captured)
    with pytest.raises(ValueError):
        adapter.decide({"raw_query": "Найди концерты"})
    assert not captured

