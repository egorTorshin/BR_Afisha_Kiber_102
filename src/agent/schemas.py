"""Transport contracts between the agent, model router, and typed tools."""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConstraintSet(ContractModel):
    """Active search preferences shared by routing and typed tools."""

    city: str | None = None
    category: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    max_price: int | None = Field(default=None, ge=0)
    is_free_only: bool = False
    max_age_limit: int | None = Field(default=None, ge=0, le=18)

    @model_validator(mode="after")
    def validate_dates(self) -> "ConstraintSet":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class AgentRequest(ContractModel):
    message: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    request_id: str | None = None


class AgentResponse(ContractModel):
    message: str = Field(min_length=1)
    result_ids: list[str] = Field(default_factory=list)
    event_cards: list["AgentEventCard"] = Field(default_factory=list)
    trace_id: str | None = None


class AgentEventCard(ContractModel):
    id: str
    title: str
    category: str
    date_start: str
    date_end: str | None = None
    city: str
    venue: str
    address: str
    price_min: int | None = None
    price_max: int | None = None
    is_free: bool
    age_limit: int
    source_url: str
    org_link: str | None = None
    ticket_url: str | None = None


class RoutingFeatures(ContractModel):
    """Only scalar routing signals; never contains message or external content."""

    intent: Literal[
        "search_events", "get_event", "find_similar", "get_venue_schedule",
        "subscribe", "save_feedback", "planner", "other",
    ] = "other"
    constraint_count: int = Field(default=0, ge=0)
    message_length: int = Field(default=0, ge=0)
    dialogue_turns: int = Field(default=0, ge=0)
    requires_tools: bool = False
    requires_multi_step: bool = False
    requires_comparison: bool = False
    requires_temporal_reasoning: bool = False
    requires_geo_reasoning: bool = False
    requires_dialogue_context: bool = False
    requires_clarification: bool = False
    dialogue_depth: int = Field(default=0, ge=0)
    has_previous_results: bool = False
    possible_prompt_injection: bool = False
    possible_jailbreak: bool = False
    possible_prompt_leak_attempt: bool = False
    possible_sql_injection: bool = False

    def to_router_payload(self) -> dict[str, str | int | bool]:
        """Explicit allowlist for a Jev request, without UNTRUSTED_DATA."""
        names = (
            "intent", "constraint_count", "message_length", "dialogue_turns",
            "requires_tools", "requires_multi_step", "requires_comparison",
            "requires_temporal_reasoning", "requires_geo_reasoning",
            "requires_dialogue_context", "requires_clarification", "dialogue_depth",
            "has_previous_results", "possible_prompt_injection", "possible_jailbreak",
            "possible_prompt_leak_attempt", "possible_sql_injection",
        )
        return {name: getattr(self, name) for name in names}


class ModelRoute(str, Enum):
    LIGHT = "LIGHT"
    HEAVY = "HEAVY"


class ToolCall(ContractModel):
    """JSON arguments are checked against the named tool's Pydantic input schema."""

    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(ContractModel):
    """Payload is UNTRUSTED_DATA, including event titles, descriptions and sources."""

    name: str = Field(min_length=1)
    result_ids: list[str] = Field(default_factory=list)
    payload: JsonValue | None = None
    error: "ToolError | None" = None
    data_trust: Literal["UNTRUSTED_DATA"] = "UNTRUSTED_DATA"


class ToolError(ContractModel):
    code: str
    message: str


class LLMResult(ContractModel):
    """A provider can return text or requests for registered tools."""

    model_name: str = Field(min_length=1)
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_output(self) -> "LLMResult":
        if not self.content and not self.tool_calls:
            raise ValueError("LLM output must contain content or tool calls")
        return self
