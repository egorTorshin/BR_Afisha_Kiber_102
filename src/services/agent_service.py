"""Application service shared by HTTP, Telegram and other agent adapters."""

import time
from dataclasses import dataclass
from typing import Literal

from src.agent.decision_router import DecisionRouter
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.light_llm import LightLLMProvider
from src.agent.orchestrator import AgentOrchestrator
from src.agent.repositories import (
    AgentSessionRepository,
    SqlAgentTraceRepository,
    SqlDialogueStateRepository,
)
from src.agent.schemas import AgentRequest, AgentResponse
from src.agent.tools import create_tool_registry
from src.config import Settings
from src.db.connection import Database
from src.services.feedback_service import FeedbackService
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.utils.logger import get_request_id


AgentStatus = Literal["ok", "clarification", "confirmation_required", "no_data", "fallback", "error"]


@dataclass(frozen=True)
class AgentRunResult:
    response: AgentResponse
    status: AgentStatus
    conversation_id: str
    request_id: str


def response_status(stop: str, fallback_reason: str | None) -> AgentStatus:
    if stop in {"clarification", "confirmation_required", "no_data"}:
        return stop  # type: ignore[return-value]
    if fallback_reason:
        return "fallback"
    if stop == "cannot_satisfy":
        return "no_data"
    if stop in {"completed", "confirmed_action", "confirmation_cancelled", "already_executed", "fallback_search"}:
        return "ok"
    return "error"


class AgentService:
    """Owns the agent pipeline and persistence, leaving transport concerns to adapters."""

    def __init__(self, database: Database, settings: Settings, *,
                 router_factory=None, light_factory=None, heavy_factory=None) -> None:
        self.database = database
        self.settings = settings
        self.search = SearchService(database)
        self.feedback = FeedbackService(database)
        self.subscriptions = SubscriptionService(database, settings.subscription_retention_days)
        self.dialogue_repo = SqlDialogueStateRepository(database, settings.dialogue_ttl_days)
        self.trace_repo = SqlAgentTraceRepository(database)
        self.sessions = AgentSessionRepository(
            database,
            settings.dialogue_ttl_days,
            principal_secret=settings.rate_limit_secret,
        )
        self._last_cleanup = 0.0
        self.router_factory = router_factory or DecisionRouter
        self.light_factory = light_factory or LightLLMProvider
        self.heavy_factory = heavy_factory or HeavyLLMProvider

    def cleanup_expired(self) -> None:
        if time.monotonic() - self._last_cleanup < 3600:
            return
        self.dialogue_repo.cleanup_expired()
        self.sessions.cleanup_expired()
        self._last_cleanup = time.monotonic()

    def create_browser_session(self) -> tuple[str, str]:
        return self.sessions.create()

    def resolve_session(self, token: str) -> str | None:
        return self.sessions.resolve(token)

    def touch_session(self, token: str) -> None:
        self.sessions.touch(token)

    def get_or_create_principal(self, principal: str) -> tuple[str, str]:
        return self.sessions.get_or_create_principal(principal)

    def get_trace(self, request_id: str):
        return self.trace_repo.get(request_id)

    def chat(
        self,
        message: str,
        conversation_id: str,
        *,
        user_id: str | None = None,
        request_id: str | None = None,
    ) -> AgentRunResult:
        self.cleanup_expired()
        conversation_id = str(conversation_id).strip()
        if not conversation_id:
            raise ValueError("conversation_id is required")
        principal = user_id or f"agent:{conversation_id}"
        registry = create_tool_registry(
            self.search,
            self.subscriptions,
            self.feedback,
            user_id=principal,
        )
        orchestrator = AgentOrchestrator(
            registry,
            self.search,
            self.router_factory(self.settings),
            self.light_factory(self.settings, registry),
            self.heavy_factory(self.settings, registry),
            state_repository=self.dialogue_repo,
            trace_repository=self.trace_repo,
            settings=self.settings,
        )
        rid = request_id or get_request_id()
        response = orchestrator.run(
            AgentRequest(message=message, conversation_id=conversation_id, request_id=rid)
        )
        trace = self.trace_repo.get(response.trace_id)
        status = response_status(
            trace.stop_reason if trace else "internal_error",
            trace.fallback_reason if trace else None,
        )
        return AgentRunResult(
            response=response,
            status=status,
            conversation_id=conversation_id,
            request_id=response.trace_id,
        )
