"""Dependency boundaries; implementations and tool registration come later."""

from typing import Mapping, Protocol, Sequence

from pydantic import BaseModel

from src.agent.schemas import (
    AgentRequest, AgentResponse, LLMResult, ModelRoute, RoutingFeatures, ToolResult,
)
from src.agent.state import DialogueState, DialogueTurn


class LLMProvider(Protocol):
    """Receives dialogue and a route only; never a database or executable SQL."""

    def generate(
        self, turns: Sequence[DialogueTurn], route: ModelRoute,
        tools: Mapping[str, type[BaseModel]],
    ) -> LLMResult: ...


class DecisionRouter(Protocol):
    def route(self, features: RoutingFeatures) -> ModelRoute: ...


class ToolRegistry(Protocol):
    """Only boundary for tool execution; arguments match the named input_schema."""

    def schemas(self) -> Mapping[str, type[BaseModel]]: ...

    def execute(self, name: str, arguments: BaseModel) -> ToolResult: ...


class AgentOrchestrator(Protocol):
    def run(self, request: AgentRequest, state: DialogueState) -> AgentResponse: ...
