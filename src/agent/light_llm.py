"""Isolated Light LLM parser. It proposes one read-only search; it never runs tools."""

import json
import math
import re
import time
from datetime import date
from typing import Literal, Protocol

from pydantic import Field, ValidationError, model_validator

from src.agent.schemas import ConstraintSet, ContractModel, ToolCall
from src.agent.security import PolicyDecision, inspect_input
from src.agent.routing import RoutingFeatureExtractor
from src.agent.state import DialogueState
from src.agent.tools import ToolRegistry
from src.config import Settings
from src.services.normalizer import parse_free_text_query


class ConstraintPatch(ContractModel):
    city: str | None = None
    category: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    time_after: str | None = None
    time_before: str | None = None
    max_duration_min: int | None = None
    max_price: int | None = None
    is_free_only: bool | None = None
    max_age_limit: int | None = None


class LightModelOutput(ContractModel):
    """Minimal parse contract; backend derives complete, validated tool arguments."""

    intent: Literal["search_events", "refine_search", "more_results", "clarify", "complex", "other"]
    constraint_patch: ConstraintPatch = Field(default_factory=ConstraintPatch)
    tool_proposal: Literal["search_events"] | None = None
    clarification: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def coherent(self):
        if self.intent in {"complex", "other"} and self.tool_proposal:
            raise ValueError("complex/other cannot propose a tool")
        if self.intent == "clarify" and (not self.clarification or self.tool_proposal):
            raise ValueError("clarification text required")
        if self.intent in {"search_events", "refine_search", "more_results"} and not self.tool_proposal:
            raise ValueError("search intent requires one proposal")
        return self


class LightDecision(ContractModel):
    requires_heavy: bool
    escalation_reason: str | None = None
    intent: str | None = None
    constraints: ConstraintSet | None = None
    proposed_call: ToolCall | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    model_name: str
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    schema_valid: bool
    model_proposed_tool: str | None = None


class LightTransport(Protocol):
    def parse(self, *, model: str, payload: dict, timeout: float) -> tuple[object, int, int]: ...


class OpenAILightTransport:
    """SDK Structured Outputs transport with no tools or conversation persistence."""

    def __init__(self, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI()
        self.client = client

    def parse(self, *, model: str, payload: dict, timeout: float) -> tuple[object, int, int]:
        response = self.client.with_options(timeout=timeout).responses.parse(
            model=model,
            input=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            text_format=LightModelOutput,
            store=False,
        )
        usage = response.usage
        if response.status != "completed" or response.output_parsed is None:
            raise ValueError("incomplete or refused structured output")
        return response.output_parsed, usage.input_tokens if usage else 0, usage.output_tokens if usage else 0


_PROMPT = """Extract one simple cultural-event request as JSON. Return only intent,
constraint_patch, optional tool_proposal, and optional clarification. Patch only explicit changes.
For a simple search/refinement/page set tool_proposal=search_events. The backend merges constraints
and builds arguments. Resolve explicit relative dates from current_date; otherwise do not add dates.
'Подешевле' needs a lower concrete max_price when current max_price exists, else clarification.
'В другой день' needs clarification. Comparison or multi-step work is complex with no proposal."""


class LightLLMProvider:
    def __init__(self, settings: Settings, registry: ToolRegistry, *, transport: LightTransport | None = None):
        self.settings = settings
        self.registry = registry
        self.transport = transport

    def parse(self, message: str, state: DialogueState, *, today: date | None = None) -> LightDecision:
        start = time.perf_counter()
        model = self.settings.light_model
        tokens = (0, 0)
        confidence = None
        intent = None
        schema_valid = False
        model_proposed_tool = None

        def result(reason: str | None = None, *, constraints=None, call=None,
                   question=None) -> LightDecision:
            return LightDecision(
                requires_heavy=reason is not None, escalation_reason=reason,
                intent=intent, constraints=constraints, proposed_call=call,
                needs_clarification=question is not None, clarification_question=question,
                confidence=confidence, model_name=model,
                latency_ms=max(0, round((time.perf_counter() - start) * 1000)),
                input_tokens=tokens[0], output_tokens=tokens[1], schema_valid=schema_valid,
                model_proposed_tool=model_proposed_tool,
            )

        if not message.strip():
            return result("empty_input")
        if (not model or (self.settings.ai_mode in {"local-ai", "native-ai"} and
                          not self.settings.local_llm_base_url and self.transport is None)
                or not math.isfinite(self.settings.light_confidence_threshold)
                or not 0 <= self.settings.light_confidence_threshold <= 1
                or not math.isfinite(self.settings.light_timeout_seconds)
                or self.settings.light_timeout_seconds <= 0):
            return result("configuration_error")
        context = self.registry.new_context()
        input_decision = self.registry.preprocess_input(message, dialogue_messages=len(state.turns), context=context)
        if input_decision.decision != PolicyDecision.ALLOWED:
            return result(input_decision.reason_code or "input_rejected")
        if any(vars(context.security_flags).values()):
            return result("security_risk")
        if "UNTRUSTED_DATA" in message:
            return result("untrusted_data")
        for value in (state.active_constraints.city, state.active_constraints.category):
            if value and any(vars(inspect_input(value)).values()):
                return result("security_risk")
        features = RoutingFeatureExtractor().extract(message, state, context.security_flags)
        if features.requires_multi_step:
            return result("requires_multi_step")
        if features.intent in {"subscribe", "save_feedback", "planner", "get_event",
                               "find_similar", "get_venue_schedule"}:
            return result("unsupported_intent")
        explicit_uncertainty = re.search(
            r"\b(?:не\s+знаю|не\s+уверен\w*|помоги\s+выбрать|без\s+разницы)\b",
            message.casefold(),
        ) is not None

        # No turns, conversation id, results, credentials, or registry internals cross this boundary.
        payload = {
            "message": re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]",
                              re.sub(r"(?<!\d)(?:\+7|8)[\s(\d)-]{10,16}(?!\d)", "[phone]", message)),
            "current_date": (today or self.settings.get_reference_date()).isoformat(),
            "active_constraints": state.active_constraints.model_dump(mode="json", exclude_none=True),
            "has_previous_results": state.has_previous_results,
            "next_search_offset": state.next_search_offset,
        }
        if self.transport is not None:
            transport = self.transport
        elif self.settings.local_llm_base_url:
            from src.agent.local_transport import LocalLightTransport
            transport = LocalLightTransport(self.settings.local_llm_base_url,
                                            self.settings.light_max_tokens)
        else:
            transport = OpenAILightTransport()
        output = None
        last_raw = None
        repair_error = "schema_validation: return only the minimal JSON schema"
        for attempt in range(2):
            try:
                request_payload = payload if attempt == 0 else {
                    **payload, "repair_error": repair_error,
                }
                raw, input_tokens, output_tokens = transport.parse(
                    model=model, payload=request_payload, timeout=self.settings.light_timeout_seconds,
                )
                last_raw = raw
                tokens = (tokens[0] + input_tokens, tokens[1] + output_tokens)
                output = LightModelOutput.model_validate(raw)
                schema_valid = True
                break
            except (ValidationError, ValueError, TypeError) as exc:
                if attempt:
                    break
                if isinstance(exc, ValidationError):
                    issue = exc.errors()[0]
                    detail = f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}"
                else:
                    detail = str(exc).splitlines()[0]
                repair_error = "schema_validation: " + detail[:120]
            except Exception:
                return result("model_error")
        if output is None:
            safe_invalid_search = (last_raw is None or (
                isinstance(last_raw, dict) and
                last_raw.get("intent") in {"search_events", "refine_search", "more_results"} and
                last_raw.get("tool_proposal") in {None, "search_events"}
            ))
            if not safe_invalid_search:
                return result("invalid_schema")
            if explicit_uncertainty:
                output = LightModelOutput(intent="clarify",
                    clarification="Уточните, пожалуйста, недостающее условие поиска")
            elif features.intent == "search_events":
                output = LightModelOutput(
                    intent="refine_search" if state.active_constraints.model_dump(exclude_none=True)
                    else "search_events",
                    tool_proposal="search_events",
                )
            else:
                return result("invalid_schema")
        confidence, intent = 1.0, output.intent
        model_proposed_tool = output.tool_proposal
        if (not features.requires_temporal_reasoning and
                (output.constraint_patch.date_from or output.constraint_patch.date_to)):
            return result("ambiguous_output")
        if intent in {"complex", "other"}:
            return result("requires_multi_step" if intent == "complex" else "unsupported_intent")
        if output.clarification and (intent == "clarify" or explicit_uncertainty):
            return result(question=output.clarification)
        if intent == "clarify" or not output.tool_proposal:
            return result("incomplete_output")
        if output.tool_proposal not in self.registry.schemas():
            return result("unknown_tool")
        if intent == "more_results" and not state.has_previous_results:
            return result("missing_previous_results")
        try:
            patch = output.constraint_patch.model_dump(exclude_none=True)
            deterministic = parse_free_text_query(message, ref_date=today or self.settings.get_reference_date(),
                                                  db=getattr(self.registry, "db", None))
            deterministic_patch = {
                "city": deterministic.city,
                "category": deterministic.category,
                "date_from": deterministic.date_from[:10] if deterministic.date_from else None,
                "date_to": deterministic.date_to[:10] if deterministic.date_to else None,
                "time_after": deterministic.time_after,
                "time_before": deterministic.time_before,
                "max_duration_min": deterministic.max_duration_min,
                "max_price": deterministic.max_price,
                "is_free_only": True if deterministic.is_free_only else None,
                "max_age_limit": deterministic.max_age_limit,
            }
            # Existing deterministic normalization is authoritative for dimensions it recognizes;
            # the small model handles only the remaining patch dimensions.
            for key, value in deterministic_patch.items():
                if value is not None and key not in patch:
                    if key == "max_price" and deterministic.is_free_only:
                        continue
                    patch[key] = value
            merged = {**state.active_constraints.model_dump(), **patch}
            constraints = ConstraintSet.model_validate(merged)
            if message.casefold().strip() == "подешевле" and (
                state.active_constraints.max_price is None or
                constraints.max_price is None or
                constraints.max_price >= state.active_constraints.max_price
            ):
                return result("ambiguous_output")
            args = constraints.model_dump(mode="json", exclude_none=True)
            offset = state.next_search_offset if intent == "more_results" else 0
            if intent == "more_results" and offset == 0:
                return result("missing_previous_results")
            if intent == "more_results" and patch:
                return result("ambiguous_output")
            args["offset"] = offset
            decision, parsed = self.registry.policy.check_tool(
                output.tool_proposal, args, calls_used=0, confirmed=False,
            )
            if decision.decision != PolicyDecision.ALLOWED or parsed is None:
                return result(decision.reason_code or "tool_rejected")
            call = ToolCall(name=output.tool_proposal, arguments=parsed.model_dump(mode="json"))
            return result(constraints=constraints, call=call)
        except (ValidationError, ValueError, TypeError):
            return result("invalid_arguments")

