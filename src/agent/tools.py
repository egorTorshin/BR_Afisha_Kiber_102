"""Allowlisted, typed application-service adapters for model callers."""

import logging
import time
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Callable, Mapping

from pydantic import BaseModel, Field, JsonValue, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from src.agent.security import (
    AgentSecurityPolicy, PolicyDecision, SecurityDecision, SecurityEvent, SecurityLimits,
    ToolExecutionContext,
)
from src.agent.schemas import ContractModel, ToolError, ToolResult
from src.models.schemas import EventRecord, SearchFilters
from src.services.feedback_service import FeedbackService
from src.services.normalizer import normalize_text
from src.services.search_service import SearchService
from src.services.recommendation_service import RecommendationService
from src.services.subscription_service import SubscriptionService

log = logging.getLogger(__name__)


class PeriodInput(ContractModel):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def valid_period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    def search_dates(self) -> dict[str, str | None]:
        return {
            "date_from": f"{self.date_from.isoformat()}T00:00:00" if self.date_from else None,
            "date_to": f"{self.date_to.isoformat()}T23:59:59" if self.date_to else None,
        }


class SearchEventsInput(PeriodInput):
    city: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    category: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    time_after: StrictStr | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    time_before: StrictStr | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    max_duration_min: StrictInt | None = Field(default=None, ge=1, le=1440)
    max_price: StrictInt | None = Field(default=None, ge=0, le=1_000_000_000)
    is_free_only: StrictBool = False
    max_age_limit: StrictInt | None = Field(default=None, ge=0, le=18)
    text: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    limit: StrictInt = Field(default=20, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0, le=100_000)


class EventSummary(ContractModel):
    """Event fields, including title, are UNTRUSTED_DATA from external sources."""
    id: str
    title: str
    category: str
    date_start: str
    date_end: str | None
    city: str
    venue: str
    price_min: int | None
    price_max: int | None
    is_free: bool
    age_limit: int
    status: str


class SearchEventsOutput(ContractModel):
    events: list[EventSummary]
    total: int = Field(ge=0)
    explanation: str | None = None


class GetEventInput(ContractModel):
    event_id: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("event_id")
    @classmethod
    def real_event_id(cls, value: str) -> str:
        if not value.strip() or value.strip().casefold() in {"null", "none", "undefined", "n/a", "na", "nil", "unknown", "<event_id>", "event_id", "?", "-"}:
            raise ValueError("invalid_event_id_placeholder")
        return value


class EventSourceOutput(ContractModel):
    """Source fields are UNTRUSTED_DATA, never instructions or tool arguments."""
    source_url: str
    source_type: str
    verified_at: str


class EventCard(EventSummary):
    """Description and all inherited event fields are UNTRUSTED_DATA."""
    normalized_title: str
    tags: list[str]
    duration_min: int | None
    address: str
    lat: float | None
    lon: float | None
    description: str
    language: str | None
    org_link: str | None
    source_url: str
    source_type: str
    verified_at: str
    confidence: float
    is_disputed: bool
    dispute_reason: str | None
    created_at: str
    updated_at: str
    sources: list[EventSourceOutput]


class GetEventOutput(ContractModel):
    event: EventCard


class FindSimilarInput(PeriodInput):
    event_id: StrictStr = Field(min_length=1, max_length=128)
    city: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    limit: StrictInt = Field(default=5, ge=1, le=20)

    @field_validator("event_id")
    @classmethod
    def real_event_id(cls, value: str) -> str:
        return GetEventInput.real_event_id(value)


class FindSimilarOutput(ContractModel):
    events: list[EventSummary]


class GetVenueScheduleInput(PeriodInput):
    venue: StrictStr = Field(min_length=1, max_length=200)
    city: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    limit: StrictInt = Field(default=50, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0, le=100_000)

    @model_validator(mode="after")
    def require_period(self):
        if self.date_from is None or self.date_to is None:
            raise ValueError("date_from and date_to are required")
        return self


class GetVenueScheduleOutput(ContractModel):
    events: list[EventSummary]
    total: int = Field(ge=0)


class SubscribeInput(ContractModel):
    city: StrictStr = Field(min_length=1, max_length=120)
    category: StrictStr = Field(min_length=1, max_length=120)
    period: PeriodInput | None = None
    replace_subscription_id: StrictStr | None = Field(default=None, min_length=1, max_length=128)


class SubscribeOutput(ContractModel):
    subscription_id: str
    city: str
    category: str
    created_at: str
    expires_at: str
    is_new: bool
    period: PeriodInput | None = None


class SaveFeedbackInput(ContractModel):
    message: StrictStr = Field(min_length=1, max_length=10000)
    event_id: StrictStr | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def nonblank_message(self):
        if not self.message.strip():
            raise ValueError("message must not be blank")
        return self


class SaveFeedbackOutput(ContractModel):
    feedback_id: str
    event_id: str | None
    status: str
    created_at: str


def _summary(event: EventRecord) -> EventSummary:
    return EventSummary.model_validate({key: getattr(event, key) for key in EventSummary.model_fields})


def _card(event: EventRecord) -> EventCard:
    values = {key: getattr(event, key) for key in EventCard.model_fields if key != "sources"}
    values["sources"] = [{"source_url": s.source_url, "source_type": s.source_type,
                          "verified_at": s.verified_at} for s in event.sources]
    return EventCard.model_validate(values)


@dataclass(frozen=True)
class ToolMetadata:
    state_changing: bool = False
    requires_confirmation: bool = False


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]
    metadata: ToolMetadata = ToolMetadata()


class ToolRegistry:
    """Only six built-in names may be executed; no dynamic dispatch by user input."""

    ALLOWLIST = frozenset({"search_events", "get_event", "find_similar",
                           "get_venue_schedule", "subscribe", "save_feedback"})

    def __init__(self, definitions: list[ToolDefinition], *, limits: SecurityLimits | None = None):
        registered: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name not in self.ALLOWLIST or definition.name in registered:
                raise ValueError("Tool name is not allowlisted or is duplicated")
            if definition.name in {"subscribe", "save_feedback"} and definition.metadata != ToolMetadata(True, True):
                raise ValueError("State-changing tools must require confirmation")
            registered[definition.name] = definition
        self._definitions = MappingProxyType(registered)
        self.policy = AgentSecurityPolicy(self.schemas(), self.metadata(), limits=limits)
        self._default_context = ToolExecutionContext()

    def new_context(self) -> ToolExecutionContext:
        """Start a separate call budget for a new agent request."""
        return ToolExecutionContext()

    def preprocess_input(self, message: str, *, dialogue_messages: int = 0,
                         context: ToolExecutionContext | None = None) -> SecurityDecision:
        """Check resource limits and retain detection flags for routing and trace."""
        context = context if context is not None else self._default_context
        decision, context.security_flags = self.policy.check_input(message, dialogue_messages)
        context.input_decision = decision
        if decision.decision != PolicyDecision.ALLOWED:
            context.security_events.append(SecurityEvent(decision=decision.decision,
                                                         reason_code=decision.reason_code))
        return decision

    def schemas(self) -> Mapping[str, type[BaseModel]]:
        return MappingProxyType({name: tool.input_schema for name, tool in self._definitions.items()})

    def metadata(self) -> Mapping[str, ToolMetadata]:
        return MappingProxyType({name: tool.metadata for name, tool in self._definitions.items()})

    def execute(self, name: str, arguments: Mapping[str, JsonValue] | BaseModel, *,
                context: ToolExecutionContext | None = None, confirmed: bool = False) -> ToolResult:
        started = time.perf_counter()
        active = context if context is not None else self._default_context
        try:
            return self._execute(name, arguments, context=active, confirmed=confirmed)
        finally:
            active.tool_latency_ms += (time.perf_counter() - started) * 1000

    def _execute(self, name: str, arguments: Mapping[str, JsonValue] | BaseModel, *,
                 context: ToolExecutionContext | None = None, confirmed: bool = False) -> ToolResult:
        """Execute only after policy validation; confirmation is trusted call metadata, not a tool arg."""
        context = context if context is not None else self._default_context
        if context.input_decision is not None and context.input_decision.decision == PolicyDecision.REJECTED:
            decision, parsed = context.input_decision, None
        else:
            validation_started = time.perf_counter()
            decision, parsed = self.policy.check_tool(name, arguments, calls_used=context.calls_used,
                                                       confirmed=confirmed)
            context.tool_validation_latency_ms += (time.perf_counter() - validation_started) * 1000
        context.calls_used += 1
        if decision.decision != PolicyDecision.ALLOWED:
            context.security_events.append(SecurityEvent(tool_name=name or "<unknown>",
                                                         decision=decision.decision,
                                                         reason_code=decision.reason_code))
            return ToolResult(name=name or "<unknown>",
                              error=ToolError(code=decision.reason_code or "policy_rejected",
                                              message="Tool call requires confirmation" if decision.decision == PolicyDecision.REQUIRES_CONFIRMATION
                                              else "Tool call rejected by security policy"))
        tool = self._definitions[name]
        try:
            handler_output = tool.handler(parsed)
            validation_started = time.perf_counter()
            try:
                output = tool.output_schema.model_validate(handler_output)
            finally:
                context.tool_validation_latency_ms += (time.perf_counter() - validation_started) * 1000
            payload = output.model_dump(mode="json")
            if "events" in payload:
                payload["events"] = payload["events"][:self.policy.limits.max_tool_results]
            ids = [item["id"] for item in payload.get("events", [])]
            if "event" in payload:
                ids = [payload["event"]["id"]]
            return ToolResult(name=name, result_ids=ids, payload=payload)
        except ValueError:
            return ToolResult(name=name, error=ToolError(code="invalid_arguments", message="Invalid tool arguments"))
        except LookupError:
            return ToolResult(name=name, error=ToolError(code="not_found", message="Requested resource was not found"))
        except Exception:
            log.exception("Tool %s failed", name)
            return ToolResult(name=name, error=ToolError(code="internal_error", message="Tool execution failed"))


def create_tool_registry(
    search: SearchService, subscriptions: SubscriptionService, feedback: FeedbackService,
    *, user_id: str, limits: SecurityLimits | None = None,
) -> ToolRegistry:
    """Build adapters from trusted services and a caller-supplied identity."""
    if not user_id.strip():
        raise ValueError("A trusted user_id is required")

    def search_events(args: SearchEventsInput) -> SearchEventsOutput:
        events, total, explanation = search.search(SearchFilters(
            query_text=args.text, city=args.city, category=args.category,
            time_after=args.time_after, time_before=args.time_before,
            max_duration_min=args.max_duration_min,
            max_price=args.max_price, is_free_only=args.is_free_only,
            max_age_limit=args.max_age_limit, limit=args.limit, offset=args.offset,
            **args.search_dates(),
        ))
        return SearchEventsOutput(events=[_summary(e) for e in events], total=total, explanation=explanation)

    def get_event(args: GetEventInput) -> GetEventOutput:
        event = search.get_event_by_id(args.event_id)
        if event is None:
            raise LookupError("Event not found")
        return GetEventOutput(event=_card(event))

    def find_similar(args: FindSimilarInput) -> FindSimilarOutput:
        source = search.get_event_by_id(args.event_id)
        if source is None or source.status != "published":
            raise LookupError("Event not found")
        recommendations = RecommendationService(search.db).find_similar_hybrid(
            source.id, city=args.city,
            date_from=args.date_from.isoformat() if args.date_from else None,
            date_to=args.date_to.isoformat() if args.date_to else None, limit=args.limit)
        return FindSimilarOutput(events=[_summary(item.event) for item in recommendations])

    def get_venue_schedule(args: GetVenueScheduleInput) -> GetVenueScheduleOutput:
        # The service's venue filter is substring based; select the exact venue after retrieval.
        found: list[EventRecord] = []
        offset = 0
        max_scanned = 1_000
        while offset < max_scanned:
            page, total, _ = search.search(SearchFilters(
                city=args.city, venue=args.venue, limit=100, offset=offset, **args.search_dates(),
            ))
            found.extend(e for e in page if normalize_text(e.venue) == normalize_text(args.venue))
            offset += len(page)
            if offset >= total or not page:
                break
        return GetVenueScheduleOutput(events=[_summary(e) for e in found[args.offset:args.offset + args.limit]],
                                      total=len(found))

    def subscribe(args: SubscribeInput) -> SubscribeOutput:
        sub = subscriptions.subscribe(user_id, args.city, args.category, period=args.period,
                                      replace_subscription_id=args.replace_subscription_id)
        return SubscribeOutput(subscription_id=sub["id"], city=sub["city"], category=sub["category"],
                               created_at=sub["created_at"], expires_at=sub["expires_at"], is_new=sub["is_new"],
                               period=PeriodInput(date_from=sub["period_from"], date_to=sub["period_to"])
                               if sub["period_from"] or sub["period_to"] else None)

    def save_feedback(args: SaveFeedbackInput) -> SaveFeedbackOutput:
        event_id = args.event_id
        if event_id is not None:
            event = search.get_event_by_id(event_id)
            if event is None:
                raise LookupError("Event not found")
            event_id = event.id
        record = feedback.submit(args.message, event_id, actor=f"agent:{user_id}")
        return SaveFeedbackOutput(feedback_id=record.id, event_id=record.event_id,
                                  status=record.status, created_at=record.created_at)

    changing = ToolMetadata(state_changing=True, requires_confirmation=True)
    registry = ToolRegistry([
        ToolDefinition("search_events", SearchEventsInput, SearchEventsOutput, search_events),
        ToolDefinition("get_event", GetEventInput, GetEventOutput, get_event),
        ToolDefinition("find_similar", FindSimilarInput, FindSimilarOutput, find_similar),
        ToolDefinition("get_venue_schedule", GetVenueScheduleInput, GetVenueScheduleOutput, get_venue_schedule),
        ToolDefinition("subscribe", SubscribeInput, SubscribeOutput, subscribe, changing),
        ToolDefinition("save_feedback", SaveFeedbackInput, SaveFeedbackOutput, save_feedback, changing),
    ], limits=limits)
    registry.db = search.db
    registry.subscription_service = subscriptions
    registry.user_id = user_id
    return registry

