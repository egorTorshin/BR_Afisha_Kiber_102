"""Serializable conversation context; history has no five-turn ceiling."""

from typing import Literal

from pydantic import Field

from src.agent.schemas import ConstraintSet, ContractModel


class DialogueTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class DialogueState(ContractModel):
    conversation_id: str = Field(min_length=1)
    turns: list[DialogueTurn] = Field(default_factory=list)
    active_constraints: ConstraintSet = Field(default_factory=ConstraintSet)
