"""HTTP boundary for persistent, grounded agent conversations."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.agent.schemas import AgentEventCard
from src.agent.decision_router import DecisionRouter
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.light_llm import LightLLMProvider
from src.config import Settings
from src.db.connection import Database
from src.http.security import check_browser_origin
from src.services.admin_auth import AdminAuth
from src.services.agent_service import AgentService
from src.services.user_service import UserService
from src.services.speech_service import MAX_AUDIO_BYTES, SpeechUnavailable, transcribe_audio


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


def create_agent_router(database: Database, settings: Settings, auth: AdminAuth,
                        agent_service: AgentService | None = None) -> APIRouter:
    router = APIRouter()
    service = agent_service or AgentService(
        database,
        settings,
        router_factory=DecisionRouter,
        light_factory=LightLLMProvider,
        heavy_factory=HeavyLLMProvider,
    )
    users = UserService(database)

    @router.post("/api/v1/agent/chat", response_model=AgentChatOutput)
    def agent_chat(body: AgentChatInput, request: Request, response: Response):
        check_browser_origin(request, settings)

        token = request.cookies.get("agent_session", "")
        conversation_id = service.resolve_session(token)
        if conversation_id:
            if body.conversation_id and body.conversation_id != conversation_id:
                raise HTTPException(403, "Диалог недоступен")
        else:
            if body.conversation_id:
                raise HTTPException(403, "Диалог недоступен")
            token, conversation_id = service.create_browser_session()

        result = service.chat(body.message, conversation_id, user_id=f"agent:{conversation_id}")
        response.set_cookie(
            "agent_session", token, httponly=True, samesite="lax",
            secure=settings.admin_cookie_secure, max_age=settings.dialogue_ttl_days * 86400, path="/",
        )
        service.touch_session(token)
        return AgentChatOutput(
            message=result.response.message, status=result.status,
            event_cards=result.response.event_cards, conversation_id=conversation_id,
            request_id=result.request_id,
        )

    @router.post("/api/v1/speech/transcribe")
    async def speech_transcribe(request: Request):
        check_browser_origin(request, settings)
        audio = await request.body()
        if len(audio) > min(MAX_AUDIO_BYTES, max(1, int(settings.speech_max_bytes))):
            raise HTTPException(413, "Аудиозапись превышает допустимый размер")
        filename = request.headers.get("x-audio-filename", "recording.webm")[:120]
        try:
            return {"text": await transcribe_audio(audio, filename, request.headers.get("content-type", "audio/webm"), settings)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except SpeechUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/api/v1/admin/agent/traces/{request_id}")
    def agent_trace(request_id: str, request: Request):
        session = auth.get_session(request.cookies.get("admin_session", ""))
        user = users.session(request.cookies.get("user_session", ""))
        if session is None and user is None:
            raise HTTPException(401, "Требуется авторизация")
        if not ((session and session["role"] == "admin") or (user and user["role"] == "admin")):
            raise HTTPException(403, "Недостаточно прав")
        trace = service.get_trace(request_id)
        if trace is None:
            raise HTTPException(404, "Трассировка не найдена")
        return trace.model_dump(mode="json")

    return router
