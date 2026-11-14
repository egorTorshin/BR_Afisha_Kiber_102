"""Deterministic agent boundary. Only trusted application code may grant confirmation.

Model output and tool results are data, never authority to change policy or execute tools.
Detection flags are telemetry; they do not authorize or block a tool call.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from pydantic import BaseModel, Field, ValidationError

from src.agent.schemas import ContractModel


class SecurityLimits(ContractModel):
    max_input_chars: int = Field(default=4000, ge=1)
    max_tool_calls: int = Field(default=5, ge=1)
    max_dialogue_messages: int = Field(default=40, ge=1)
    max_tool_results: int = Field(default=100, ge=1)


class PolicyDecision(str, Enum):
    ALLOWED = "allowed"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REJECTED = "rejected"


class SecurityDecision(ContractModel):
    decision: PolicyDecision
    reason_code: str | None = None


class SecurityFlags(ContractModel):
    possible_prompt_injection: bool = False
    possible_jailbreak: bool = False
    possible_prompt_leak_attempt: bool = False
    possible_sql_injection: bool = False


class SecurityEvent(ContractModel):
    tool_name: str | None = None
    decision: PolicyDecision
    reason_code: str | None = None


@dataclass
class ToolExecutionContext:
    """One context per agent request; keep it outside model-visible arguments."""

    calls_used: int = 0
    tool_latency_ms: float = 0.0
    tool_validation_latency_ms: float = 0.0
    input_decision: SecurityDecision | None = None
    security_flags: SecurityFlags = field(default_factory=SecurityFlags)
    security_events: list[SecurityEvent] = field(default_factory=list)


_PATTERNS: Mapping[str, tuple[re.Pattern[str], ...]] = {
    "possible_prompt_injection": (
        re.compile(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
        re.compile(r"(?:игнорируй|забудь)\s+(?:все\s+)?(?:предыдущие|прошлые)\s+инструкции", re.I),
        re.compile(r"\[(?:system|developer)\]|<\|(?:system|developer)\|>", re.I),
    ),
    "possible_jailbreak": (
        re.compile(r"\b(?:DAN|jailbreak)\b|developer\s+mode", re.I),
        re.compile(r"(?:обойди|отключи)\s+(?:все\s+)?(?:правила|ограничения|защиту)", re.I),
    ),
    "possible_prompt_leak_attempt": (
        re.compile(r"(?:reveal|print|show|repeat)\s+(?:your\s+)?(?:system|developer)\s+(?:prompt|instructions)", re.I),
        re.compile(r"(?:покажи|раскрой|выведи)\s+(?:свой\s+)?(?:системный\s+промпт|системные\s+инструкции)", re.I),
    ),
    "possible_sql_injection": (
        re.compile(r"(?:'|\")\s*(?:OR|AND)\s+\d+\s*=\s*\d+|\bUNION\s+(?:ALL\s+)?SELECT\b", re.I),
        re.compile(r";\s*(?:DROP|DELETE|UPDATE|INSERT|ALTER)\s+(?:TABLE\s+|FROM\s+|INTO\s+)?\w+", re.I),
    ),
}


def inspect_input(message: str) -> SecurityFlags:
    """Flag suspicious text without changing or blocking the user's query."""
    return SecurityFlags(**{
        name: any(pattern.search(message) for pattern in patterns)
        for name, patterns in _PATTERNS.items()
    })


class AgentSecurityPolicy:
    def __init__(self, schemas: Mapping[str, type[BaseModel]], metadata: Mapping[str, object],
                 *, limits: SecurityLimits | None = None):
        self.schemas = dict(schemas)
        self.metadata = dict(metadata)
        self.limits = limits or SecurityLimits()

    def check_input(self, message: str, dialogue_messages: int = 0) -> tuple[SecurityDecision, SecurityFlags]:
        flags = inspect_input(message)
        if len(message) > self.limits.max_input_chars:
            return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="input_too_long"), flags
        if dialogue_messages > self.limits.max_dialogue_messages:
            return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="dialogue_limit_exceeded"), flags
        return SecurityDecision(decision=PolicyDecision.ALLOWED), flags

    def check_tool(self, name: str, arguments: object, *, calls_used: int,
                   confirmed: bool = False) -> tuple[SecurityDecision, BaseModel | None]:
        if calls_used >= self.limits.max_tool_calls:
            return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="tool_call_limit_exceeded"), None
        schema = self.schemas.get(name)
        metadata = self.metadata.get(name)
        if schema is None or metadata is None:
            return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="unknown_tool"), None
        if not isinstance(arguments, Mapping) and not isinstance(arguments, BaseModel):
            return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="invalid_arguments"), None
        try:
            values = arguments.model_dump() if isinstance(arguments, BaseModel) else dict(arguments)
            parsed = schema.model_validate(values)
        except (ValidationError, ValueError, TypeError):
            return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="invalid_arguments"), None
        if getattr(metadata, "state_changing", False):
            if not getattr(metadata, "requires_confirmation", False):
                return SecurityDecision(decision=PolicyDecision.REJECTED, reason_code="invalid_tool_metadata"), None
            if not confirmed:
                return SecurityDecision(decision=PolicyDecision.REQUIRES_CONFIRMATION,
                                        reason_code="confirmation_required"), None
        return SecurityDecision(decision=PolicyDecision.ALLOWED), parsed

