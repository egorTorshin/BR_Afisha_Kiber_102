from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.agent.schemas import (
    AgentRequest, AgentResponse, ConstraintSet, LLMResult, ModelRoute,
    RoutingFeatures, ToolCall, ToolResult,
)
from src.agent.state import DialogueState, DialogueTurn
from src.agent.trace import AgentTrace


def test_dialogue_state_preserves_five_turns_and_active_constraints():
    state = DialogueState(
        conversation_id="conversation-1",
        turns=[DialogueTurn(role="user" if i % 2 == 0 else "assistant", content=str(i))
               for i in range(5)],
        active_constraints=ConstraintSet(city="Уфа", max_price=500),
    )
    restored = DialogueState.model_validate_json(state.model_dump_json())
    assert len(restored.turns) == 5
    assert restored.active_constraints.city == "Уфа"
    assert restored.active_constraints.max_price == 500
    assert len(DialogueState(conversation_id="c", turns=state.turns + state.turns).turns) == 10


@pytest.mark.parametrize("constraints", [
    {"max_price": -1},
    {"max_age_limit": 19},
    {"date_from": "2026-12-01", "date_to": "2026-11-01"},
])
def test_constraints_reject_invalid_values(constraints):
    with pytest.raises(ValidationError):
        ConstraintSet(**constraints)


def test_route_and_tool_payload_validation():
    assert ModelRoute("LIGHT") is ModelRoute.LIGHT
    assert ModelRoute("HEAVY") is ModelRoute.HEAVY
    with pytest.raises(ValueError):
        ModelRoute("OTHER")
    call = ToolCall(name="search_events", arguments={"city": "Уфа", "limit": 5})
    assert ToolCall.model_validate_json(call.model_dump_json()) == call
    with pytest.raises(ValidationError):
        ToolCall(name="search_events", arguments={"invalid": object()})
    with pytest.raises(ValidationError):
        ToolCall(name="search_events", sql="SELECT *")
    assert ToolResult(name="search_events", result_ids=["event-1"]).result_ids == ["event-1"]


def test_request_response_and_model_result_validation():
    assert AgentRequest(message="Концерты", conversation_id="c").message == "Концерты"
    assert AgentResponse(message="Найдено", result_ids=["event-1"]).result_ids == ["event-1"]
    with pytest.raises(ValidationError):
        AgentRequest(message="", conversation_id="c")
    with pytest.raises(ValidationError):
        LLMResult(model_name="light-v1", input_tokens=1, output_tokens=1, cost=0)
    result = LLMResult(model_name="light-v1", tool_calls=[ToolCall(name="search_events")],
                       input_tokens=10, output_tokens=3, cost=Decimal("0.001"))
    assert result.tool_calls[0].name == "search_events"


def test_trace_serializes_route_calls_ids_and_usage():
    trace = AgentTrace(
        trace_id="trace-1",
        routing_features=RoutingFeatures(message_length=12, dialogue_turns=5, requires_tools=True),
        model_route=ModelRoute.HEAVY,
        model_name="heavy-v1",
        tool_calls=[ToolCall(name="search_events", arguments={"city": "Уфа"})],
        result_ids=["event-1"],
        latency_ms=125,
        input_tokens=40,
        output_tokens=15,
        cost=Decimal("0.0025"),
    )
    assert AgentTrace.model_validate_json(trace.model_dump_json()) == trace
    with pytest.raises(ValidationError):
        AgentTrace(**{**trace.model_dump(), "cost": -1})
    with pytest.raises(ValidationError):
        AgentTrace(**{**trace.model_dump(), "latency_ms": -1})
