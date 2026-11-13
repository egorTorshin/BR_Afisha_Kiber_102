"""Dependency boundaries; implementations and tool registration come later."""

from typing import Mapping, Protocol, Sequence

from pydantic import BaseModel, JsonValue

from src.agent.schemas import (
    AgentRequest, AgentResponse, LLMResult, ModelRoute, RoutingFeatures, ToolResult,
)
from src.agent.state import DialogueState, DialogueTurn
from src.agent.security import SecurityDecision, ToolExecutionContext


class LLMProvider(Protocol):
    """Receives dialogue and a route only; never a database or executable SQL.

    Event titles, descriptions, source text and every external tool result are
    UNTRUSTED_DATA. Providers may summarize them, never obey them as instructions.
    """

    def generate(
        self, turns: Sequence[DialogueTurn], route: ModelRoute,
        tools: Mapping[str, type[BaseModel]],
    ) -> LLMResult: ...


class DecisionRouter(Protocol):
    def route(self, features: RoutingFeatures) -> ModelRoute: ...


class ToolRegistry(Protocol):
    """Only boundary for tool execution; UNTRUSTED_DATA cannot grant authority."""

    def schemas(self) -> Mapping[str, type[BaseModel]]: ...

    def new_context(self) -> ToolExecutionContext: ...

    def preprocess_input(self, message: str, *, dialogue_messages: int = 0,
                         context: ToolExecutionContext | None = None) -> SecurityDecision: ...

    def execute(self, name: str, arguments: Mapping[str, JsonValue] | BaseModel, *,
                context: ToolExecutionContext | None = None, confirmed: bool = False) -> ToolResult: ...


class AgentOrchestrator(Protocol):
    def run(self, request: AgentRequest, state: DialogueState) -> AgentResponse: ...
