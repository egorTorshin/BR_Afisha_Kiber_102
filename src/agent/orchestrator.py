"""Request-scoped agent pipeline. Model output never supplies event facts or authority."""

import json
import re
import hashlib
import time
import uuid
from typing import MutableMapping

from src.agent.decision_router import DecisionRouter
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.light_llm import LightLLMProvider
from src.agent.routing import RoutingFeatureExtractor
from src.agent.schemas import AgentEventCard, AgentRequest, AgentResponse, ConstraintSet, ModelRoute, ToolCall
from src.agent.security import PolicyDecision
from src.agent.state import DialogueState, DialogueTurn
from src.agent.tools import ToolRegistry
from src.agent.trace import AgentTrace
from src.agent.repositories import (DialogueStateRepository, AgentTraceRepository,
                                    MemoryDialogueStateRepository, MemoryAgentTraceRepository)
from src.config import Settings
from src.services.normalizer import parse_free_text_query
from src.services.search_service import SearchService
from src.services.recommendation_service import RecommendationService
from src.utils.metrics import AI_CALLS, AI_LATENCY, AI_TOKENS, AI_COST, AI_FALLBACKS
from src.utils.logger import scrub_pii


_YES = {"да", "подтверждаю", "подтвердить", "да, подтверждаю"}
_NO = {"нет", "отмена", "отменить"}


def _search_explanation(constraints: ConstraintSet | None) -> str:
    if constraints is None:
        return "Подобрал события из опубликованного каталога."
    details = []
    for key, label in (("city", "город"), ("category", "категория"),
                       ("date_from", "период с"), ("date_to", "период до"),
                       ("time_after", "после"), ("time_before", "до"),
                       ("max_duration_min", "длительность до, мин"),
                       ("max_price", "бюджет до"), ("max_age_limit", "возраст до")):
        value = getattr(constraints, key, None)
        if value is not None:
            details.append(f"{label} {value}")
    if constraints.is_free_only:
        details.append("только бесплатные")
    return "Подобрал события с учётом условий: " + ", ".join(details) + "." if details else "Подобрал события из опубликованного каталога."


def _confirmation_message(call: ToolCall) -> str:
    if call.name == "subscribe":
        city = str(call.arguments.get("city", ""))[:120]
        category = str(call.arguments.get("category", ""))[:120]
        return f"Подтвердите подписку: {city}, {category}. Ответьте «да» или «нет»."
    if call.name == "save_feedback":
        return "Подтвердите отправку отзыва модератору: ответьте «да» или «нет»."
    return "Подтвердите действие: ответьте «да» или «нет»."


def _planner_numbers(message: str) -> tuple[int, int, int | None, int]:
    """Extract total company budget, party size, stop count and travel cap."""
    text = message.casefold().replace("ё", "е")
    party = 2 if re.search(r"\b(?:на\s+двоих|вдвоем|двое|2\s+человек)\b", text) else (
        3 if re.search(r"\b(?:на\s+троих|втроем|трое|3\s+человек)\b", text) else 1)
    count_match = re.search(r"\b([2-4])\s*(?:событи\w*|мероприяти\w*)\b", text)
    word_match = re.search(r"\b(два|две|три|четыре)\s+(?:событи\w*|мероприяти\w*)\b", text)
    count = int(count_match.group(1)) if count_match else {"два": 2, "две": 2, "три": 3, "четыре": 4}.get(
        word_match.group(1), 3) if word_match else 3
    budget_match = re.search(r"\b(?:до\s+|бюджет\s*)?(\d{2,7})\s*(?:руб\w*\b|₽)", text)
    budget = int(budget_match.group(1)) if budget_match else None
    travel_match = re.search(r"\b(?:переезд\w*\s+до|до|не\s+более)\s+(\d{1,3})\s*мин\w*\s*(?:переезд\w*)?\b", text)
    travel = int(travel_match.group(1)) if travel_match else 25
    return party, count, budget, travel


class AgentOrchestrator:
    def __init__(self, registry: ToolRegistry, search: SearchService,
                 router: DecisionRouter, light: LightLLMProvider, heavy: HeavyLLMProvider,
                 *, states: MutableMapping[str, DialogueState] | None = None,
                 traces: MutableMapping[str, AgentTrace] | None = None,
                 state_repository: DialogueStateRepository | None = None,
                 trace_repository: AgentTraceRepository | None = None,
                 settings: Settings | None = None):
        self.registry, self.search = registry, search
        self.router, self.light, self.heavy = router, light, heavy
        self.state_repository = state_repository or MemoryDialogueStateRepository(states)
        self.trace_repository = trace_repository or MemoryAgentTraceRepository(traces)
        self.states = self.state_repository.states if isinstance(self.state_repository, MemoryDialogueStateRepository) else None
        self.traces = self.trace_repository.traces if isinstance(self.trace_repository, MemoryAgentTraceRepository) else None
        self.settings = settings or Settings()

    def run(self, request: AgentRequest, state: DialogueState | None = None) -> AgentResponse:
        started = time.perf_counter()
        request_id = request.request_id or uuid.uuid4().hex
        state = state if state is not None else (self.state_repository.get(request.conversation_id)
            or DialogueState(conversation_id=request.conversation_id))
        if state.conversation_id != request.conversation_id:
            raise ValueError("conversation_id mismatch")
        context = self.registry.new_context()
        checked = self.registry.preprocess_input(request.message, dialogue_messages=len(state.turns), context=context)
        features = RoutingFeatureExtractor().extract(request.message, state, context.security_flags)
        trace = AgentTrace(trace_id=request_id, request_id=request_id, routing_features=features,
                           conversation_id=request.conversation_id,
                           model_route=ModelRoute.LIGHT, model_name="none", latency_ms=0,
                           input_tokens=0, output_tokens=0, cost=0,
                           security_flags=context.security_flags)
        calls: list[ToolCall] = []
        tool_ids: list[list[str]] = []
        basis: list[str] = []
        selected: list[str] = []
        usage: list[dict] = []
        fallback_reason = None
        stop = "completed"
        message = "Не удалось обработать запрос. Попробуйте уточнить его."
        cards: list[AgentEventCard] = []
        constraints: ConstraintSet | None = None
        search_call: ToolCall | None = None
        last_search_explanation: str | None = None
        heavy_result_kind: str | None = None
        planner_handled = False
        normalized = request.message.strip().casefold().replace("ё", "е")

        def execute(call: ToolCall, *, confirmed: bool = False):
            result = self.registry.execute(call.name, call.arguments, context=context, confirmed=confirmed)
            nonlocal last_search_explanation
            if call.name == "search_events" and isinstance(result.payload, dict):
                last_search_explanation = result.payload.get("explanation")
            calls.append(call)
            tool_ids.append(result.result_ids)
            return result

        try:
            if checked.decision != PolicyDecision.ALLOWED:
                stop = checked.reason_code or "input_rejected"
                message = "Запрос слишком длинный или превышен лимит диалога. Сократите его."
            elif state.pending_confirmation and normalized in _YES:
                call = state.pending_confirmation
                key = hashlib.sha256(json.dumps(call.model_dump(mode="json"), sort_keys=True,
                                             ensure_ascii=False).encode()).hexdigest()
                state.pending_confirmation = None
                if (key in state.executed_action_keys or
                        not self.state_repository.claim_action(state.conversation_id, key)):
                    stop, message = "already_executed", "Это действие уже выполнено."
                else:
                    result = execute(call, confirmed=True)
                    if result.error:
                        stop, message = result.error.code, "Не удалось выполнить подтверждённое действие."
                    else:
                        state.executed_action_keys.append(key)
                        state.executed_action_keys[:] = state.executed_action_keys[-100:]
                        message = "Действие выполнено."
                        stop = "confirmed_action"
            elif state.pending_confirmation and normalized in _NO:
                state.pending_confirmation = None
                stop, message = "confirmation_cancelled", "Действие отменено."
            elif features.intent == "planner" and not any(context.security_flags.model_dump().values()):
                planner_handled = True
                selected.append("deterministic_planner")
                parsed = parse_free_text_query(request.message,
                    ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                party_size, target_count, total_budget, max_travel = _planner_numbers(request.message)
                plan = RecommendationService(self.search.db).build_weekend_plan(
                    city=parsed.city, date_from=parsed.date_from, date_to=parsed.date_to,
                    max_budget_total=total_budget, party_size=party_size,
                    max_age_limit=parsed.max_age_limit, max_travel_min=max_travel,
                    target_count=target_count, ref_date=self.settings.get_reference_date())
                search_args: dict = {"limit": 100, "offset": 0}
                if parsed.city:
                    search_args["city"] = parsed.city
                if parsed.date_from:
                    search_args["date_from"] = parsed.date_from[:10]
                if parsed.date_to:
                    search_args["date_to"] = parsed.date_to[:10]
                if parsed.max_age_limit is not None:
                    search_args["max_age_limit"] = parsed.max_age_limit
                search_call = ToolCall(name="search_events", arguments=search_args)
                execute(search_call)
                observed_now = {event_id for group in tool_ids for event_id in group}
                for item in plan.stops:
                    if item.event.id not in observed_now:
                        execute(ToolCall(name="get_event", arguments={"event_id": item.event.id}))
                        observed_now = {event_id for group in tool_ids for event_id in group}
                confirmed_stops = [item for item in plan.stops if item.event.id in observed_now]
                basis = [item.event.id for item in confirmed_stops]
                if confirmed_stops:
                    lines = [f"Маршрут для {party_size} чел.:"]
                    for number, item in enumerate(confirmed_stops, start=1):
                        if item.travel_from_previous_min:
                            lines.append(f"Переезд: {item.travel_from_previous_min} мин")
                        lines.append(f"{number}. {item.start_time:%d.%m %H:%M}–{item.end_time:%d.%m %H:%M} "
                                     f"{item.event.title} — {item.event.city}, {item.event.venue} "
                                     f"({item.party_price} ₽)")
                    lines.append(f"Итого: {sum(item.party_price for item in confirmed_stops)} ₽ "
                                 f"на компанию; переезды: {sum(item.travel_from_previous_min for item in confirmed_stops)} мин.")
                else:
                    lines = ["Маршрут по заданным условиям пока не найден."]
                if plan.conflict_explanation:
                    lines.append(plan.conflict_explanation)
                if len(confirmed_stops) < len(plan.stops):
                    lines.append("Часть событий маршрута не удалось подтвердить в каталоге.")
                message = "\n".join(lines)
                stop = "completed" if confirmed_stops else "no_data"
            else:
                if state.pending_confirmation:
                    state.pending_confirmation = None
                decision = self.router.decide(features)
                trace.router_decision = decision
                trace.model_route = decision.route
                if self.settings.ai_mode == "no-ai":
                    fallback_reason = "no_ai_mode"
                elif decision.router_error or (decision.fallback and decision.reason_code == "router_failure"):
                    fallback_reason = "jev_unavailable"
                elif decision.route == ModelRoute.LIGHT:
                    light = self.light.parse(request.message, state)
                    selected.append(light.model_name)
                    trace.light_confidence = light.confidence
                    trace.light_proposed_tool = light.model_proposed_tool
                    trace.light_schema_valid = light.schema_valid
                    trace.light_escalation_reason = light.escalation_reason
                    trace.input_tokens += light.input_tokens
                    trace.output_tokens += light.output_tokens
                    usage.append({"model_name": light.model_name, "input_tokens": light.input_tokens,
                                  "output_tokens": light.output_tokens})
                    if light.needs_clarification:
                        state.pending_clarification = light.clarification_question
                        stop, message = "clarification", light.clarification_question or "Уточните запрос."
                    elif light.requires_heavy:
                        trace.escalated = True
                        trace.model_route = ModelRoute.HEAVY
                        if light.escalation_reason in {"model_error", "configuration_error"}:
                            fallback_reason = "light_unavailable"
                    elif light.proposed_call:
                        constraints = light.constraints
                        search_call = light.proposed_call
                        result = execute(search_call)
                        if result.error:
                            stop, message = result.error.code, "Не удалось выполнить поиск."
                        else:
                            basis = result.result_ids
                    else:
                        fallback_reason = "light_incomplete"
                if trace.model_route == ModelRoute.HEAVY and fallback_reason is None and stop != "clarification":
                    heavy = self.heavy.run(request.message, state, context=context, stop_on_confirmation=True)
                    heavy_result_kind = heavy.result.kind
                    last_search_explanation = heavy.last_search_explanation
                    selected.append(heavy.model_name)
                    trace.model_name = heavy.model_name
                    trace.input_tokens += heavy.input_tokens
                    trace.output_tokens += heavy.output_tokens
                    usage.append({"model_name": heavy.model_name, "input_tokens": heavy.input_tokens,
                                  "output_tokens": heavy.output_tokens})
                    trace.number_of_steps = heavy.steps
                    trace.repair_attempts = heavy.repair_attempts
                    trace.max_context_tokens = heavy.max_context_tokens
                    calls.extend(heavy.tool_calls)
                    tool_ids.extend(heavy.tool_result_ids)
                    for previous_call in reversed(heavy.tool_calls):
                        if previous_call.name == "search_events":
                            search_call = previous_call
                            try:
                                constraints = ConstraintSet.model_validate({
                                    key: value for key, value in previous_call.arguments.items()
                                    if key in ConstraintSet.model_fields})
                            except ValueError:
                                pass
                            break
                    if heavy.pending_confirmation:
                        key = hashlib.sha256(json.dumps(heavy.pending_confirmation.model_dump(mode="json"),
                                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                        if key in state.executed_action_keys:
                            stop, message = "already_executed", "Это действие уже выполнено."
                        else:
                            state.pending_confirmation = heavy.pending_confirmation
                            stop, message = "confirmation_required", _confirmation_message(heavy.pending_confirmation)
                    elif heavy.result.kind == "clarification":
                        state.pending_clarification = heavy.result.clarification_question
                        stop, message = "clarification", heavy.result.clarification_question or "Уточните запрос."
                    elif heavy.result.kind == "recommendation":
                        basis = heavy.basis_event_ids
                        message = heavy.result.short_explanation
                    elif heavy.result.kind == "no_data":
                        stop = "no_data"
                        message = " ".join(filter(None, [heavy.result.short_explanation,
                                                         heavy.result.constraint_relaxation,
                                                         last_search_explanation]))
                    elif heavy.result.kind == "cannot_satisfy":
                        stop = "cannot_satisfy"
                        message = " ".join(filter(None, [heavy.result.short_explanation,
                                                         heavy.result.constraint_relaxation,
                                                         last_search_explanation]))
                    else:
                        fallback_reason = heavy.stop_reason
                if fallback_reason and stop not in {"clarification", "confirmation_required"}:
                    if (features.intent == "other" and not any(context.security_flags.model_dump().values())
                            and re.search(r"не знаю|не уверен|без разницы|куда-нибудь", normalized)):
                        stop, message = "clarification", "Уточните, пожалуйста, город, дату или интересующий вид мероприятия."
                    elif features.intent == "search_events" and not any(context.security_flags.model_dump().values()):
                        try:
                            filters = parse_free_text_query(request.message,
                                ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                            merged = state.active_constraints.model_dump(mode="json", exclude_none=True)
                            for key in ConstraintSet.model_fields:
                                value = getattr(filters, key, None)
                                if value is not None and value is not False:
                                    merged[key] = value[:10] if key in {"date_from", "date_to"} else value
                            constraints = ConstraintSet.model_validate(merged)
                            args = constraints.model_dump(mode="json", exclude_none=True)
                            if getattr(filters, "query_text", None):
                                args["text"] = filters.query_text[:500]
                            args["offset"] = state.next_search_offset if normalized in {"а еще", "а ещё"} else 0
                            search_call = ToolCall(name="search_events", arguments=args)
                            result = execute(search_call)
                            if result.error is None:
                                basis = result.result_ids
                                stop = "fallback_search"
                            else:
                                stop = result.error.code
                        except (ValueError, TypeError):
                            stop = "fallback_unavailable"
                    else:
                        stop = "model_unavailable"

            # Only IDs returned by successful tools in this request may be rendered.
            observed = {event_id for group in tool_ids for event_id in group}
            for event_id in dict.fromkeys(basis):
                if event_id not in observed:
                    continue
                event = self.search.get_event_by_id(event_id)
                if event is None or event.id != event_id:
                    continue
                cards.append(AgentEventCard(
                    id=event.id, title=event.title, category=event.category,
                    date_start=event.date_start, date_end=event.date_end,
                    city=event.city, venue=event.venue, address=event.address,
                    price_min=event.price_min, price_max=event.price_max,
                    is_free=event.is_free, age_limit=event.age_limit,
                    source_url=event.source_url, org_link=event.org_link))
            cards = cards[:5]
            if basis and not cards:
                stop, message = "no_data", "Данные о найденных событиях недоступны."
            elif search_call and not cards and stop == "completed":
                stop, message = "no_data", last_search_explanation or "По вашему запросу событий не найдено. Попробуйте расширить период или убрать фильтр города."
            elif not cards and stop in {"fallback_search", "no_data"} and heavy_result_kind is None and not planner_handled:
                message = last_search_explanation or "По вашему запросу событий не найдено. Попробуйте расширить период или убрать фильтр города."
            elif cards:
                if heavy_result_kind != "recommendation" and not planner_handled:
                    message = _search_explanation(constraints)
                stop = "completed" if stop == "completed" else stop
                state.last_result_ids = [card.id for card in cards]
                state.shown_event_ids = list(dict.fromkeys([*state.shown_event_ids, *state.last_result_ids]))
                state.has_previous_results = True
                if search_call:
                    state.next_search_offset = int(search_call.arguments.get("offset", 0)) + len(cards)
            if constraints is not None and stop in {"completed", "fallback_search", "no_data",
                                                    "cannot_satisfy"}:
                state.active_constraints = constraints
            if stop not in {"clarification", "confirmation_required"}:
                state.pending_clarification = None
        except Exception:
            stop, message = "internal_error", "Сервис временно недоступен. Попробуйте позже."
        finally:
            stored_message = (request.message if checked.decision == PolicyDecision.ALLOWED else
                              f"[отклонённый запрос: {checked.reason_code or 'policy'}]")
            state.turns.extend([DialogueTurn(role="user", content=stored_message),
                                DialogueTurn(role="assistant", content=message)])
            # Keep enough history for follow-ups while respecting the policy limit.
            state.turns[:] = state.turns[-min(20, self.registry.policy.limits.max_dialogue_messages):]
            self.state_repository.save(state)
            trace.model_name = selected[-1] if selected else ("deterministic_search" if fallback_reason else "none")
            trace.selected_models = selected
            trace.model_usage = usage
            trace.cost = self.settings.model_cost(usage)
            trace.user_query = scrub_pii(request.message)[:500] if checked.decision == PolicyDecision.ALLOWED else None
            trace.plan_summary = f"{('fallback' if fallback_reason else trace.model_route.value)} intent={features.intent}; tools={','.join(call.name for call in calls) or 'none'}"
            safe_args = {"city", "category", "date_from", "date_to", "venue", "event_id",
                         "time_after", "time_before", "max_duration_min", "limit", "offset", "max_price",
                         "max_age_limit", "is_free_only"}
            trace.tool_calls = [ToolCall(name=call.name, arguments={
                key: (scrub_pii(str(value))[:200] if key in {"text", "message"} else value)
                for key, value in call.arguments.items() if key in safe_args or key in {"text", "message"}
            }) for call in calls]
            trace.tool_result_ids = tool_ids
            trace.result_ids = [card.id for card in cards]
            trace.basis_event_ids = basis
            trace.security_flags = context.security_flags
            trace.security_events = context.security_events
            trace.policy_decision = context.security_events[-1].decision if context.security_events else checked.decision
            trace.stop_reason = stop
            trace.fallback_reason = fallback_reason
            trace.latency_ms = max(0, round((time.perf_counter() - started) * 1000))
            if trace.router_decision is not None:
                router_component = "jev" if self.settings.jev_enabled and self.settings.ai_mode != "no-ai" else "regex"
                AI_CALLS.labels(router_component,
                                "fallback" if trace.router_decision.fallback else "success").inc()
                AI_LATENCY.labels(router_component).observe(getattr(trace.router_decision, "latency_ms", 0) / 1000)
            if usage:
                for index, _item in enumerate(usage):
                    model_component = "heavy" if (trace.escalated and index == len(usage) - 1) else "light"
                    AI_CALLS.labels(model_component, "error" if stop == "internal_error" else "success").inc()
                model_component = "heavy" if trace.model_route == ModelRoute.HEAVY else "light"
                AI_LATENCY.labels(model_component).observe(trace.latency_ms / 1000)
            for item in usage:
                model = str(item.get("model_name", "unknown"))[:64]
                AI_TOKENS.labels(model, "input").inc(max(0, int(item.get("input_tokens", 0))))
                AI_TOKENS.labels(model, "output").inc(max(0, int(item.get("output_tokens", 0))))
            if trace.cost is not None:
                AI_COST.inc(float(trace.cost))
            if fallback_reason:
                AI_FALLBACKS.labels(str(fallback_reason)[:64]).inc()
            self.trace_repository.save(state.conversation_id, trace)
        return AgentResponse(message=message, result_ids=[card.id for card in cards],
                             event_cards=cards, trace_id=request_id)

