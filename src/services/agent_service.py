"""Application service shared by HTTP, Telegram and other agent adapters."""

import time
from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal
from typing import Literal

from src.agent.decision_router import DecisionRouter
from src.agent.budget import SqlAgentBudgetRepository, estimate_request_cost
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.light_llm import LightLLMProvider
from src.agent.smart import SmartProvider
from src.agent.structured_deep import StructuredDeepPlanner
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
                 router_factory=None, light_factory=None, heavy_factory=None, smart_factory=None,
                 structured_factory=None) -> None:
        self.database = database
        self.settings = settings
        self.search = SearchService(database)
        self.feedback = FeedbackService(database)
        self.subscriptions = SubscriptionService(database, settings.subscription_retention_days)
        self.dialogue_repo = SqlDialogueStateRepository(database, settings.dialogue_ttl_days)
        self.trace_repo = SqlAgentTraceRepository(database)
        self.budget_repo = SqlAgentBudgetRepository(database, settings.rate_limit_secret)
        self.sessions = AgentSessionRepository(
            database,
            settings.dialogue_ttl_days,
            principal_secret=settings.rate_limit_secret,
        )
        self._last_cleanup = 0.0
        self.router_factory = router_factory or DecisionRouter
        self.light_factory = light_factory or LightLLMProvider
        self.heavy_factory = heavy_factory or HeavyLLMProvider
        self.smart_factory = smart_factory or SmartProvider
        self.structured_factory = structured_factory or StructuredDeepPlanner

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
        budget_source_id: str | None = None,
        request_id: str | None = None,
    ) -> AgentRunResult:
        self.cleanup_expired()
        conversation_id = str(conversation_id).strip()
        if not conversation_id:
            raise ValueError("conversation_id is required")
        principal = user_id or f"agent:{conversation_id}"
        rid = request_id or get_request_id()
        run_settings = self.settings
        budget_blocked = False
        budget_block_reason = None
        reserved = False
        if self.settings.ai_mode != "no-ai":
            estimate = estimate_request_cost(self.settings, message)
            denied_scope = "unpriced" if estimate is None else self.budget_repo.reserve(
                request_id=rid,
                conversation_id=conversation_id,
                user_id=principal,
                amount=estimate,
                dialogue_limit=self.settings.agent_dialogue_cost_limit_usd,
                daily_limit=self.settings.agent_daily_user_cost_limit_usd,
                source_id=budget_source_id,
            )
            if denied_scope:
                budget_blocked = True
                budget_block_reason = denied_scope
                run_settings = replace(self.settings, ai_mode="no-ai")
            else:
                reserved = True
        try:
            registry = create_tool_registry(
                self.search,
                self.subscriptions,
                self.feedback,
                user_id=principal,
            )
            orchestrator = AgentOrchestrator(
                registry,
                self.search,
                self.router_factory(run_settings),
                self.light_factory(run_settings, registry),
                self.heavy_factory(run_settings, registry),
                smart=self.smart_factory(run_settings, registry),
                structured=self.structured_factory(run_settings, registry),
                state_repository=self.dialogue_repo,
                trace_repository=self.trace_repo,
                settings=run_settings,
            )
            response = orchestrator.run(
                AgentRequest(message=message, conversation_id=conversation_id, request_id=rid)
            )
        except Exception:
            if reserved:
                self.budget_repo.release(rid)
            raise
        trace = self.trace_repo.get(response.trace_id)
        if reserved:
            actual_cost = trace.cost if trace and trace.cost is not None else Decimal(0)
            self.budget_repo.settle(rid, actual_cost)
        if budget_blocked:
            notice = ("Выполняю обычный поиск без ИИ.\n" if budget_block_reason == "unpriced"
                      else "Лимит расходов достигнут. Выполняю обычный поиск.\n")
            response.message = notice + response.message
            if trace is not None:
                trace.fallback_reason = ("agent_cost_unavailable" if budget_block_reason == "unpriced"
                                         else "agent_budget_limit_exceeded")
                trace.plan_summary = (trace.plan_summary or "") + "; budget=ordinary_search"
                self.trace_repo.update(conversation_id, trace)
                state = self.dialogue_repo.get(conversation_id)
                if state and state.turns:
                    state.turns[-1].content = response.message
                    self.dialogue_repo.save(state)
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
