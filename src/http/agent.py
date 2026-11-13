"""HTTP boundary for persistent, grounded agent conversations."""

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.agent.decision_router import DecisionRouter
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.light_llm import LightLLMProvider
from src.agent.orchestrator import AgentOrchestrator
from src.agent.repositories import (AgentSessionRepository, SqlAgentTraceRepository,
                                    SqlDialogueStateRepository)
from src.agent.schemas import AgentEventCard, AgentRequest
from src.agent.tools import create_tool_registry
from src.config import Settings
from src.db.connection import Database
from src.http.security import check_browser_origin
from src.services.admin_auth import AdminAuth
from src.services.feedback_service import FeedbackService
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.utils.logger import get_request_id


class AgentChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)


class AgentChatOutput(BaseModel):
    message: str
    status: Literal["ok", "clarification", "confirmation_required", "no_data", "fallback", "error"]
    event_cards: list[AgentEventCard]
    conversation_id: str
    request_id: str


def _response_status(stop: str, fallback_reason: str | None) -> str:
    if stop in {"clarification", "confirmation_required", "no_data"}:
        return stop
    if fallback_reason:
        return "fallback"
    if stop == "cannot_satisfy":
        return "no_data"
    if stop in {"completed", "confirmed_action", "confirmation_cancelled", "already_executed",
                "fallback_search"}:
        return "ok"
    return "error"


def create_agent_router(database: Database, settings: Settings, auth: AdminAuth) -> APIRouter:
    router = APIRouter()
    search = SearchService(database)
    feedback = FeedbackService(database)
    subscriptions = SubscriptionService(database, settings.subscription_retention_days)
    dialogue_repo = SqlDialogueStateRepository(database, settings.dialogue_ttl_days)
    trace_repo = SqlAgentTraceRepository(database)
    sessions = AgentSessionRepository(database, settings.dialogue_ttl_days)
    last_cleanup = time.monotonic()

    @router.post("/api/v1/agent/chat", response_model=AgentChatOutput)
    def agent_chat(body: AgentChatInput, request: Request, response: Response):
        nonlocal last_cleanup
        check_browser_origin(request, settings)
        if time.monotonic() - last_cleanup >= 3600:
            dialogue_repo.cleanup_expired()
            sessions.cleanup_expired()
            last_cleanup = time.monotonic()

        token = request.cookies.get("agent_session", "")
        conversation_id = sessions.resolve(token)
        if conversation_id:
            if body.conversation_id and body.conversation_id != conversation_id:
                raise HTTPException(403, "Диалог недоступен")
        else:
            if body.conversation_id:
                raise HTTPException(403, "Диалог недоступен")
            token, conversation_id = sessions.create()

        registry = create_tool_registry(search, subscriptions, feedback,
                                        user_id=f"agent:{conversation_id}")
        agent = AgentOrchestrator(
            registry, search, DecisionRouter(settings),
            LightLLMProvider(settings, registry), HeavyLLMProvider(settings, registry),
            state_repository=dialogue_repo, trace_repository=trace_repo, settings=settings,
        )
        result = agent.run(AgentRequest(message=body.message, conversation_id=conversation_id,
                                        request_id=get_request_id()))
        trace = trace_repo.get(result.trace_id)
        stop = trace.stop_reason if trace else "internal_error"
        fallback_reason = trace.fallback_reason if trace else None
        response.set_cookie(
            "agent_session", token, httponly=True, samesite="lax",
            secure=settings.admin_cookie_secure, max_age=settings.dialogue_ttl_days * 86400, path="/",
        )
        sessions.touch(token)
        return AgentChatOutput(
            message=result.message, status=_response_status(stop, fallback_reason),
            event_cards=result.event_cards, conversation_id=conversation_id,
            request_id=result.trace_id,
        )

    @router.get("/api/v1/admin/agent/traces/{request_id}")
    def agent_trace(request_id: str, request: Request):
        session = auth.get_session(request.cookies.get("admin_session", ""))
        if session is None:
            raise HTTPException(401, "Требуется авторизация")
        if session["role"] != "admin":
            raise HTTPException(403, "Недостаточно прав")
        trace = trace_repo.get(request_id)
        if trace is None:
            raise HTTPException(404, "Трассировка не найдена")
        return trace.model_dump(mode="json")

    return router
