"""Transport contracts between the agent, model router, and typed tools."""

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConstraintSet(ContractModel):
    """Active search preferences; conversion to SearchFilters belongs to a future tool."""

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


class AgentResponse(ContractModel):
    message: str = Field(min_length=1)
    result_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None


class RoutingFeatures(ContractModel):
    message_length: int = Field(ge=0)
    dialogue_turns: int = Field(ge=0)
    requires_tools: bool = False
    requires_multi_step: bool = False


class ModelRoute(str, Enum):
    LIGHT = "LIGHT"
    HEAVY = "HEAVY"


class ToolCall(ContractModel):
    """JSON arguments are checked against the named tool's Pydantic input schema."""

    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(ContractModel):
    name: str = Field(min_length=1)
    result_ids: list[str] = Field(default_factory=list)
    payload: JsonValue | None = None


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
