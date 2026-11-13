"""Serializable observability record for one agent request."""

from decimal import Decimal

from pydantic import Field

from src.agent.schemas import ContractModel, ModelRoute, RoutingFeatures, ToolCall


class AgentTrace(ContractModel):
    trace_id: str = Field(min_length=1)
    routing_features: RoutingFeatures
    model_route: ModelRoute
    model_name: str = Field(min_length=1)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal = Field(ge=0, allow_inf_nan=False)
