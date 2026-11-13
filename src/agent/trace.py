"""Serializable observability record for one agent request."""

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import Field

from src.agent.schemas import ContractModel, ModelRoute, RoutingFeatures, ToolCall
from src.agent.security import PolicyDecision, SecurityEvent, SecurityFlags
from src.agent.decision_router import RouterDecision

if TYPE_CHECKING:
    from src.agent.light_llm import LightDecision
    from src.agent.heavy_llm import HeavyDecision


class AgentTrace(ContractModel):
    trace_id: str = Field(min_length=1)
    request_id: str | None = None
    conversation_id: str | None = None
    routing_features: RoutingFeatures
    model_route: ModelRoute
    model_name: str = Field(min_length=1)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    model_usage: list[dict[str, str | int | None]] = Field(default_factory=list)
    security_flags: SecurityFlags = Field(default_factory=SecurityFlags)
    security_events: list[SecurityEvent] = Field(default_factory=list)
    policy_decision: PolicyDecision | None = None
    reason_code: str | None = None
    router_decision: RouterDecision | None = None
    light_confidence: float | None = Field(default=None, ge=0, le=1)
    light_proposed_tool: str | None = None
    light_escalation_reason: str | None = None
    light_schema_valid: bool | None = None
    tool_result_ids: list[list[str]] = Field(default_factory=list)
    number_of_steps: int = Field(default=0, ge=0)
    repair_attempts: int = Field(default=0, ge=0)
    max_context_tokens: int = Field(default=0, ge=0)
    basis_event_ids: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    selected_models: list[str] = Field(default_factory=list)
    escalated: bool = False
    fallback_reason: str | None = None

    def with_light_decision(self, decision: "LightDecision") -> "AgentTrace":
        """Attach safe Light metadata without storing prompt or raw model output."""
        values = self.model_dump()
        values.update(
            model_route=ModelRoute.HEAVY if decision.requires_heavy else ModelRoute.LIGHT,
            model_name=decision.model_name,
            latency_ms=decision.latency_ms,
            input_tokens=decision.input_tokens,
            output_tokens=decision.output_tokens,
            light_confidence=decision.confidence,
            light_proposed_tool=decision.model_proposed_tool,
            light_escalation_reason=decision.escalation_reason,
            light_schema_valid=decision.schema_valid,
            tool_calls=[decision.proposed_call] if decision.proposed_call else [],
        )
        return AgentTrace.model_validate(values)

    def with_heavy_decision(self, decision: "HeavyDecision") -> "AgentTrace":
        values = self.model_dump()
        values.update(model_route=ModelRoute.HEAVY, model_name=decision.model_name,
                      latency_ms=decision.latency_ms, input_tokens=decision.input_tokens,
                      output_tokens=decision.output_tokens, tool_calls=decision.tool_calls,
                      tool_result_ids=decision.tool_result_ids,
                      result_ids=decision.basis_event_ids,
                      number_of_steps=decision.steps,
                      repair_attempts=decision.repair_attempts,
                      max_context_tokens=decision.max_context_tokens,
                      basis_event_ids=decision.basis_event_ids,
                      stop_reason=decision.stop_reason,
                      security_events=decision.security_events,
                      security_flags=decision.security_flags,
                      policy_decision=decision.policy_decision)
        return AgentTrace.model_validate(values)
