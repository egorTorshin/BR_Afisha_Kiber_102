"""Serializable conversation context; history has no five-turn ceiling."""

from typing import Literal

from pydantic import Field

from src.agent.schemas import ConstraintSet, ContractModel, ToolCall


class DialogueTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class DialogueState(ContractModel):
    conversation_id: str = Field(min_length=1)
    turns: list[DialogueTurn] = Field(default_factory=list)
    active_constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    has_previous_results: bool = False
    next_search_offset: int = Field(default=0, ge=0)
    last_result_ids: list[str] = Field(default_factory=list)
    shown_event_ids: list[str] = Field(default_factory=list)
    pending_clarification: str | None = None
    pending_confirmation: ToolCall | None = None
    executed_action_keys: list[str] = Field(default_factory=list)
