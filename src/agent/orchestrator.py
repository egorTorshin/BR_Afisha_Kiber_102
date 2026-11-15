"""Request-scoped agent pipeline. Model output never supplies event facts or authority."""

import json
import re
import hashlib
import time
import uuid
from datetime import datetime, timedelta
from typing import MutableMapping

from src.agent.decision_router import DecisionRouter
from src.agent.adaptive_router import AdaptiveIntentRouter, AdaptiveRoute
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.light_llm import LightLLMProvider
from src.agent.smart import SmartProvider
from src.agent.search_context import starts_new_undated_search
from src.agent.structured_deep import StructuredDeepPlanner, StructuredDecisionEngine, eligible as structured_eligible
from src.agent.routing import RoutingFeatureExtractor
from src.agent.schemas import AgentEventCard, AgentRequest, AgentResponse, ConstraintSet, ModelRoute, ToolCall
from src.agent.security import PolicyDecision
from src.agent.state import DialogueState, DialogueTurn
from src.agent.tools import ToolRegistry
from src.agent.trace import AgentTrace
from src.agent.diagnostics import InferenceRecorder, activate as activate_inference, deactivate as deactivate_inference
from src.agent.repositories import (DialogueStateRepository, AgentTraceRepository,
                                    MemoryDialogueStateRepository, MemoryAgentTraceRepository)
from src.config import Settings
from src.services.normalizer import parse_free_text_query
from src.services.normalizer import resolve_city, resolve_category
from src.services.search_service import SearchService
from src.services.recommendation_service import RecommendationService
from src.utils.metrics import AI_CALLS, AI_LATENCY, AI_TOKENS, AI_COST, AI_FALLBACKS


_YES = {"да", "подтверждаю", "подтвердить", "да, подтверждаю"}
_NO = {"нет", "отмена", "отменить"}


def _followup_kind(text: str) -> str | None:
    """Only unambiguous short references are handled without model interpretation."""
    clean = re.sub(r"[?.!,]+$", "", text.strip().casefold().replace("ё", "е")).strip()
    if re.fullmatch(r"(?:а\s+)?еще", clean):
        return "more_results"
    if re.fullmatch(r"(?:а\s+)?(?:если\s+)?(?:в\s+)?другом\s+городе", clean):
        return "other_city"
    if re.fullmatch(r"(?:а\s+что\s+)?подешевле|(?:а\s+что\s+)?дешевле", clean):
        return "cheaper"
    if re.fullmatch(r"(?:давай\s+)?(?:в|на)\s+другой\s+день", clean):
        return "other_day"
    if re.fullmatch(r"(?:а\s+)?где\s+(?:оно|он|это|событие)\s+проходит", clean):
        return "venue"
    if re.fullmatch(r"(?:а\s+)?сколько\s+(?:(?:это|он[оа]?)\s+)?стоит", clean):
        return "price"
    if re.fullmatch(r"(?:есть\s+что(?:-?нибудь)?\s+)?(?:на|в)\s+(?:этих\s+)?выходных|(?:на|в)\s+(?:эти\s+)?выходные", clean):
        return "refine_search"
    if re.fullmatch(r"(?:(?:а|покажи)\s+)?в\s+(?:уфе|стерлитамаке|салавате|нефтекамске|бирске|сибае|белорецке|ишимбае|октябрьском|туймазах)", clean):
        return "refine_search"
    if re.fullmatch(r"(?:а\s+)?в\s+(?:субботу|воскресенье|пятницу)|(?:для\s+)?ребенка\s+\d{1,2}\s+лет|только\s+бесплатн\w*|\d{1,2}\s+\w+", clean):
        return "refine_search"
    return None


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
        period = call.arguments.get("period") or {}
        dates = (f"; события с {period.get('date_from') or 'любой даты'}"
                 f" по {period.get('date_to') or 'любую дату'}") if period else ""
        action = "изменение подписки" if call.arguments.get("replace_subscription_id") else "подписку"
        return f"Подтвердите {action}: {city}, {category}{dates}. Ответьте «да» или «нет»."
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


def _required_constraints(message: str, state: DialogueState, settings: Settings, db) -> ConstraintSet:
    """User words and saved dialogue, never model proposals, define hard filters."""
    parsed = parse_free_text_query(message, settings.get_reference_date(), db=db)
    values = state.active_constraints.model_dump(mode="json", exclude_none=True)
    current = {key: getattr(parsed, key, None) for key in ConstraintSet.model_fields}
    low = message.casefold().replace("ё", "е")
    if starts_new_undated_search(message, has_date=bool(parsed.date_from or parsed.date_to)):
        values["date_from"] = values["date_to"] = None
    for key, value in current.items():
        if value is not None and value is not False:
            values[key] = value[:10] if key in {"date_from", "date_to"} else value
    if parsed.date_from or parsed.date_to:
        values["date_from"] = parsed.date_from[:10] if parsed.date_from else None
        values["date_to"] = parsed.date_to[:10] if parsed.date_to else None
        values.pop("time_after", None)
        values.pop("time_before", None)
    if re.search(r"\b(?:в|на)\s+другой\s+день\b", low):
        values["date_from"] = values["date_to"] = None
    range_match = re.search(r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\s+ноября\b", low)
    if range_match:
        year = settings.get_reference_date().year
        values["date_from"] = f"{year}-11-{int(range_match.group(1)):02d}"
        values["date_to"] = f"{year}-11-{int(range_match.group(2)):02d}"
    if re.search(r"\bпосле\s+обеда\b", low):
        values["time_after"] = "12:00"
    comparison_without_budget = (bool(re.search(r"\bсравн\w*\b", low)) and
        bool(re.search(r"\bдешевле\b", low)) and
        not bool(re.search(r"\d+\s*(?:₽|руб\w*\b|р\b)", low)))
    if comparison_without_budget:
        values["max_price"] = state.active_constraints.max_price
    if re.search(r"\bначина\w*\s+до\s+\d{1,2}:?\d{2}", low):
        values.pop("time_before", None)
    exact_age = re.search(r"\b(?:ребенк\w*\s+|дет\w*\s+)(\d{1,2})\s*лет\b", low)
    if exact_age:
        values["max_age_limit"] = int(exact_age.group(1))
    if re.search(r"\b(?:на\s+двоих|для\s+двоих|на\s+троих|для\s+троих)\b", low) and parsed.max_price is not None:
        # The parser already divides an adjacent amount. Other wording needs the same rule.
        amount = re.search(r"(\d{2,7})\s*(?:руб\w*|₽)", low)
        if amount:
            people = 3 if "троих" in low else 2
            values["max_price"] = int(amount.group(1)) // people
    if parsed.max_price is not None and parsed.max_price > 0:
        values["is_free_only"] = False
    if re.search(r"\b(?:подешевле|дешевле)\b", low) and state.active_constraints.max_price is not None:
        values["max_price"] = state.active_constraints.max_price
    if parsed.max_age_limit == 6 and state.active_constraints.max_age_limit is not None and not re.search(r"\d{1,2}\s*лет|\d{1,2}\+|летн", low):
        values["max_age_limit"] = state.active_constraints.max_age_limit
    if parsed.is_free_only:
        values["is_free_only"] = True
    return ConstraintSet.model_validate(values)


def _matches(event, required: ConstraintSet, *, city: str | None = None) -> bool:
    if event.status != "published":
        return False
    if (city or required.city) and event.city != (city or required.city):
        return False
    if required.category and event.category != required.category:
        return False
    if required.date_from and (event.date_end or event.date_start)[:10] < required.date_from.isoformat():
        return False
    if required.date_to and event.date_start[:10] > required.date_to.isoformat():
        return False
    if required.max_age_limit is not None and event.age_limit > required.max_age_limit:
        return False
    price = 0 if event.is_free else (event.price_min if event.price_min is not None else event.price_max)
    if required.is_free_only and not event.is_free:
        return False
    if required.max_price is not None and (price is None or price > required.max_price):
        return False
    if required.time_after and event.date_start[11:16] < required.time_after:
        return False
    if required.time_before and (event.date_end or event.date_start)[11:16] > required.time_before:
        return False
    return True


def _pair_end(event):
    start = datetime.fromisoformat(event.date_start)
    if event.duration_min is not None and event.duration_min > 0:
        return start + timedelta(minutes=event.duration_min)
    if event.date_end:
        end = datetime.fromisoformat(event.date_end)
        if end > start and end.date() == start.date():
            return end
    return None


class AgentOrchestrator:
    def __init__(self, registry: ToolRegistry, search: SearchService,
                 router: DecisionRouter, light: LightLLMProvider, heavy: HeavyLLMProvider,
                 *, smart: SmartProvider | None = None, structured: StructuredDeepPlanner | None = None,
                 states: MutableMapping[str, DialogueState] | None = None,
                 traces: MutableMapping[str, AgentTrace] | None = None,
                 state_repository: DialogueStateRepository | None = None,
                 trace_repository: AgentTraceRepository | None = None,
                 settings: Settings | None = None):
        self.registry, self.search = registry, search
        self.router, self.light, self.heavy = router, light, heavy
        self.smart = smart or SmartProvider(settings or Settings(), registry)
        self.structured = structured or StructuredDeepPlanner(settings or Settings(), registry)
        self.state_repository = state_repository or MemoryDialogueStateRepository(states)
        self.trace_repository = trace_repository or MemoryAgentTraceRepository(traces)
        self.states = self.state_repository.states if isinstance(self.state_repository, MemoryDialogueStateRepository) else None
        self.traces = self.trace_repository.traces if isinstance(self.trace_repository, MemoryAgentTraceRepository) else None
        self.settings = settings or Settings()

    def run(self, request: AgentRequest, state: DialogueState | None = None) -> AgentResponse:
        started = time.perf_counter()
        stage_latency: dict[str, int] = {}
        request_id = request.request_id or uuid.uuid4().hex
        state = state if state is not None else (self.state_repository.get(request.conversation_id)
            or DialogueState(conversation_id=request.conversation_id))
        if state.conversation_id != request.conversation_id:
            raise ValueError("conversation_id mismatch")
        stage_latency["dialogue_state_resolution"] = round((time.perf_counter() - started) * 1000)
        context = self.registry.new_context()
        checked = self.registry.preprocess_input(request.message, dialogue_messages=len(state.turns), context=context)
        features = RoutingFeatureExtractor().extract(request.message, state, context.security_flags)
        trace = AgentTrace(trace_id=request_id, request_id=request_id, routing_features=features,
                           conversation_id=request.conversation_id,
                           model_route=ModelRoute.LIGHT, model_name="none", latency_ms=0,
                           input_tokens=0, output_tokens=0, cost=0,
                           security_flags=context.security_flags)
        inference_recorder = InferenceRecorder(request_id, request.conversation_id, len(state.turns) // 2 + 1)
        inference_token = activate_inference(inference_recorder)
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
        followup = _followup_kind(request.message)
        try:
            required = _required_constraints(request.message, state, self.settings, getattr(self.search, "db", None))
        except (ValueError, TypeError):
            required = state.active_constraints
        ordered_pair = re.search(r"\bсначала\s+([^,]+),?\s+потом\s+([^,]+)", normalized)
        wants_pair = bool(re.search(r"\b(?:два|две|2)\s+(?:разных\s+)?(?:событи\w*|мероприяти\w*)\b", normalized)
                          or ordered_pair)
        wants_afternoon_choice = bool(state.has_previous_results and
            re.search(r"\bпосле\s+обеда\b", normalized) and
            re.search(r"\b(?:какой|какое|выбрать|выбери)\b", normalized))
        needs_pair_recalculation = bool(state.has_previous_results and not wants_pair and
            (re.search(r"\bпересчитай\b", normalized) or
             re.search(r"\bоставь\s+только\s+вариант\w*\b", normalized)) and
            re.search(r"\b(?:бюджет|руб\w*|₽|двоих|троих)\b", normalized))
        deterministic_followup = bool(followup and (
            followup != "refine_search" or state.has_previous_results or state.pending_clarification))
        if self.settings.adaptive_agent_enabled:
            adaptive = AdaptiveIntentRouter().decide(
                request.message, state, features,
                deterministic_followup=deterministic_followup,
                followup_kind=followup,
                input_allowed=checked.decision == PolicyDecision.ALLOWED)
            trace.adaptive_route = adaptive.route
            trace.route_reason = adaptive.reason
            if (self.settings.structured_deep_enabled and not deterministic_followup
                    and adaptive.route == AdaptiveRoute.SMART
                    and structured_eligible(request.message, state, context.security_flags)):
                trace.adaptive_route = AdaptiveRoute.DEEP
                trace.route_reason = "structured_comparison"
        smart_handled = False
        structured_handled = False
        deterministic_deep_handled = False
        smart_to_deep = False
        smart_llm_calls = 0
        structured_llm_calls = 0
        deterministic_route = bool(trace.adaptive_route == AdaptiveRoute.FAST and
                                   trace.route_reason == "exact_catalog_search")

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
            elif self.settings.adaptive_agent_enabled and not state.pending_confirmation and trace.route_reason == "unsupported_purchase":
                trace.model_route = ModelRoute.HEAVY
                stop, message = "cannot_satisfy", "Покупка билетов через этот сервис недоступна. Могу помочь найти мероприятие."
            elif self.settings.adaptive_agent_enabled and not state.pending_confirmation and trace.route_reason == "ambiguous_legacy" and not state.has_previous_results:
                trace.model_route = ModelRoute.HEAVY
                stop, message = "clarification", "Уточните город, дату или предпочтения по мероприятиям."
                state.pending_clarification = message
            elif (self.settings.adaptive_agent_enabled and needs_pair_recalculation and
                  not any(context.security_flags.model_dump().values())):
                deterministic_deep_handled = True
                stop, message = "clarification", "Не удалось подтвердить полный пересчёт и сравнение по каталогу. Уточните два события и общий бюджет."
                state.pending_clarification = message
            elif (self.settings.adaptive_agent_enabled and wants_afternoon_choice and
                  not any(context.security_flags.model_dump().values())):
                deterministic_deep_handled = True
                args = required.model_dump(mode="json", exclude_none=True)
                args.update(limit=100, offset=0)
                search_call = ToolCall(name="search_events", arguments=args)
                found = execute(search_call)
                ranked = []
                if found.error is None and found.payload.get("total", 0) <= len(found.result_ids):
                    for event_id in found.result_ids:
                        event = self.search.get_event_by_id(event_id)
                        if event and _matches(event, required):
                            price = 0 if event.is_free else event.price_min
                            if price is not None:
                                ranked.append((price, event.date_start, event))
                if ranked:
                    price, _, chosen = min(ranked, key=lambda item: (item[0], item[1], item[2].id))
                    basis = [chosen.id]
                    heavy_result_kind = "recommendation"
                    message = (f"Из подтверждённых вариантов после обеда выбираю «{chosen.title}»: "
                               f"начало {chosen.date_start[11:16]}, минимум {price} ₽ за билет. "
                               "Выбор основан на минимальной подтверждённой цене.")
                else:
                    stop, message = "clarification", "Не удалось подтвердить варианты после обеда с известной ценой. Уточните предпочтения."
                    state.pending_clarification = message
            elif (self.settings.adaptive_agent_enabled and not state.pending_confirmation and state.has_previous_results and
                    re.fullmatch(r"\s*(?:ему|ей|реб[её]нку)\s+(\d{1,2})\s+лет\s*[.!?]?\s*", request.message, re.I)):
                age = int(re.search(r"\d{1,2}", request.message).group())
                if age > 18:
                    stop, message = "clarification", "Уточните возраст ребёнка от 0 до 18 лет."
                    state.pending_clarification = message
                else:
                    constraints = state.active_constraints.model_copy(update={"max_age_limit": age})
                    search_call = ToolCall(name="search_events", arguments={
                        **constraints.model_dump(mode="json", exclude_none=True), "offset": 0})
                    result = execute(search_call)
                    if result.error is None:
                        basis = result.result_ids
                        if not basis:
                            stop, message = "no_data", "Для этого возраста мероприятий по прежним условиям не найдено."
                    else:
                        stop, message = result.error.code, "Не удалось выполнить поиск."
            elif (self.settings.adaptive_agent_enabled and not state.pending_confirmation and
                    re.fullmatch(r"\s*(?:а\s+)?что\s+ближе\s*[?!.]?\s*", request.message, re.I)):
                trace.model_route = ModelRoute.HEAVY
                stop, message = "clarification", "Уточните ваше местоположение или адрес, от которого сравнить расстояние."
                state.pending_clarification = message
            elif (self.settings.adaptive_agent_enabled and not state.pending_confirmation
                    and not state.pending_clarification and not any(context.security_flags.model_dump().values())
                    and features.intent in {"get_event", "find_similar"}
                    and re.fullmatch(r"\s*(?:покажи\s+)?похожи[её]\s*(?:мероприятия)?\s*[.!?]?\s*|\s*расскажи\s+подробнее\s+об\s+этом\s+событии\s*[.!?]?\s*", request.message, re.I)):
                deterministic_deep_handled = True
                heavy_result_kind = "recommendation"
                # Resolve a reference only when the dialogue points to one confirmed event.
                event_id = (state.selected_event_id or
                            (state.last_result_ids[0] if len(state.last_result_ids) == 1 else None) or
                            (state.shown_event_ids[0] if len(state.shown_event_ids) == 1 else None))
                if event_id is None:
                    stop, message = "clarification", "Уточните, о каком мероприятии речь."
                    state.pending_clarification = message
                elif features.intent == "get_event":
                    result = execute(ToolCall(name="get_event", arguments={"event_id": event_id}))
                    event = result.payload.get("event") if result.error is None and isinstance(result.payload, dict) else None
                    if event and event.get("status") == "published" and event_id in result.result_ids:
                        basis = [event_id]
                        state.selected_event_id = event_id
                        message = f"{event['title']}. {event.get('description') or ''} Место: {event['venue']}, {event['city']}. Источник: {event['source_url']}"
                    else:
                        stop, message = "no_data", "Не удалось подтвердить опубликованное мероприятие."
                else:
                    args = {"event_id": event_id, "limit": 5}
                    if state.active_constraints.city:
                        args["city"] = state.active_constraints.city
                    result = execute(ToolCall(name="find_similar", arguments=args))
                    if result.error is None:
                        basis = result.result_ids
                        message = "Похожие опубликованные мероприятия найдены."
                        if not basis:
                            stop, message = "no_data", "Похожих опубликованных мероприятий не найдено."
                    else:
                        stop, message = "no_data", "Не удалось найти похожие мероприятия."
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
            elif checked.decision == PolicyDecision.ALLOWED and deterministic_followup:
                # This branch is shared by LIGHT, HEAVY and no-AI mode. The model cannot
                # replace a known filter, invent a date or choose an arbitrary event ID.
                trace.model_name = "deterministic_followup"
                if followup == "other_day":
                    state.active_constraints = state.active_constraints.model_copy(
                        update={"date_from": None, "date_to": None})
                    state.next_search_offset = 0
                    state.pending_clarification = "На какую дату или период искать?"
                    stop, message = "clarification", state.pending_clarification
                elif followup == "other_city":
                    state.pending_clarification = "Какой город искать?"
                    stop, message = "clarification", state.pending_clarification
                elif followup == "venue":
                    event_id = state.selected_event_id or (
                        state.last_result_ids[0] if len(state.last_result_ids) == 1 else None)
                    if event_id is None:
                        stop, message = "clarification", "Уточните, о каком мероприятии речь."
                        state.pending_clarification = message
                    else:
                        result = execute(ToolCall(name="get_event", arguments={"event_id": event_id}))
                        if result.error is None and result.payload["event"]["status"] == "published":
                            event = result.payload["event"]
                            basis = [event_id]
                            state.selected_event_id = event_id
                            message = f"«{event['title']}» проходит: {event['venue']}, {event['address']}."
                        else:
                            stop, message = "no_data", "Не удалось подтвердить место проведения мероприятия."
                elif followup == "price":
                    event_id = state.last_result_ids[0] if len(state.last_result_ids) == 1 else None
                    if event_id is None:
                        stop, message = "clarification", "Уточните, о каком мероприятии речь."
                        state.pending_clarification = message
                    else:
                        result = execute(ToolCall(name="get_event", arguments={"event_id": event_id}))
                        if result.error is None and result.payload["event"]["status"] == "published":
                            event = result.payload["event"]
                            basis = [event_id]
                            state.selected_event_id = event_id
                            if event["is_free"]:
                                message = f"«{event['title']}» — бесплатно."
                            elif event["price_min"] is None and event["price_max"] is None:
                                message = f"Цена «{event['title']}» в каталоге не указана."
                            elif event["price_max"] is not None and event["price_max"] != event["price_min"]:
                                message = f"«{event['title']}» стоит от {event['price_min']} до {event['price_max']} ₽."
                            else:
                                message = f"«{event['title']}» стоит {event['price_min'] or event['price_max']} ₽."
                        else:
                            stop, message = "no_data", "Не удалось подтвердить цену мероприятия."
                elif not state.has_previous_results and followup != "refine_search":
                    stop, message = "clarification", "Сначала укажите, какие мероприятия искать."
                    state.pending_clarification = message
                elif followup == "refine_search":
                    parsed = parse_free_text_query(request.message,
                        ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                    patch = {key: (value[:10] if key in {"date_from", "date_to"} else value)
                             for key in ConstraintSet.model_fields
                             if (value := getattr(parsed, key)) is not None and value is not False}
                    if self.settings.adaptive_agent_enabled:
                        explicit_child_age = re.search(
                            r"\b(?:ребенка|ребенком|ребенку)\s+(\d{1,2})\s+лет\b", normalized)
                        if explicit_child_age:
                            patch["max_age_limit"] = int(explicit_child_age.group(1))
                    if not patch:
                        stop, message = "clarification", "Уточните дату, возраст или другое условие."
                        state.pending_clarification = message
                    else:
                        constraints = ConstraintSet.model_validate({
                            **state.active_constraints.model_dump(mode="json"), **patch})
                        args = constraints.model_dump(mode="json", exclude_none=True)
                        args["offset"] = 0
                        search_call = ToolCall(name="search_events", arguments=args)
                        result = execute(search_call)
                        if result.error is None:
                            basis = result.result_ids
                            if not basis:
                                stop, message = "no_data", "По уточнённым условиям мероприятий нет."
                        else:
                            stop, message = result.error.code, "Не удалось выполнить поиск."
                elif followup == "cheaper":
                    prices = [card.price_min for card in state.last_search_results
                              if card.price_min is not None]
                    if not prices and state.active_constraints.max_price is None:
                        stop, message = "clarification", "Уточните мероприятие или цену для сравнения."
                        state.pending_clarification = message
                    else:
                        previous = state.active_constraints
                        ceiling = min(prices) - 1 if prices else previous.max_price - 1
                        if previous.max_price is not None:
                            ceiling = min(ceiling, previous.max_price - 1)
                        constraints = previous.model_copy(update={"max_price": max(0, ceiling)})
                        if ceiling < 0:
                            stop, message = "no_data", "Дешевле бесплатных мероприятий вариантов нет."
                        else:
                            args = constraints.model_dump(mode="json", exclude_none=True)
                            args.update({"offset": 0, "limit": 100})
                            search_call = ToolCall(name="search_events", arguments=args)
                            result = execute(search_call)
                            if result.error is None:
                                basis = [event_id for event_id in result.result_ids
                                         if event_id not in state.shown_event_ids][:5]
                                if not basis:
                                    stop, message = "no_data", "Более дешёвых мероприятий с прежними условиями нет."
                            else:
                                stop, message = result.error.code, "Не удалось выполнить поиск."
                else:  # more_results
                    constraints = state.active_constraints
                    offset = state.next_search_offset
                    while len(basis) < 5 and context.calls_used < self.registry.policy.limits.max_tool_calls:
                        args = constraints.model_dump(mode="json", exclude_none=True)
                        args.update({"offset": offset, "limit": 20})
                        search_call = ToolCall(name="search_events", arguments=args)
                        result = execute(search_call)
                        if result.error:
                            stop, message = result.error.code, "Не удалось выполнить поиск."
                            break
                        for event_id in result.result_ids:
                            offset += 1
                            if event_id not in state.shown_event_ids and event_id not in basis:
                                basis.append(event_id)
                            if len(basis) == 5:
                                break
                        if len(basis) == 5 or not result.result_ids or offset >= result.payload["total"]:
                            break
                    state.next_search_offset = offset
                    if not basis and stop == "completed":
                        stop, message = "no_data", "Больше мероприятий по прежним условиям нет."
            elif wants_pair and not any(context.security_flags.model_dump().values()):
                planner_handled = True
                selected.append("verified_pair")
                party_size, _, total_budget, _ = _planner_numbers(request.message)
                if required.date_from is None or required.date_to is None or not required.city:
                    stop, message = "clarification", "Для подбора двух событий укажите город и дату."
                else:
                    args = {"city": required.city, "date_from": required.date_from.isoformat(),
                            "date_to": required.date_to.isoformat(), "limit": 100, "offset": 0}
                    if required.max_age_limit is not None:
                        args["max_age_limit"] = required.max_age_limit
                    search_call = ToolCall(name="search_events", arguments=args)
                    found = execute(search_call)
                    candidates = []
                    if found.error is None and found.payload.get("total", 0) <= len(found.result_ids):
                        for event_id in found.result_ids:
                            event = self.search.get_event_by_id(event_id)
                            pair_filters = required.model_copy(update={"time_after": None, "time_before": None,
                                "max_price": None, "category": None if ordered_pair else required.category})
                            if event and _matches(event, pair_filters, city=required.city):
                                end = _pair_end(event)
                                price = 0 if event.is_free else event.price_min
                                if end and price is not None and end.date() == datetime.fromisoformat(event.date_start).date():
                                    candidates.append((event, end, price))
                    pair = None
                    day_evening = bool(re.search(r"\bдн[её]м\b", normalized) and re.search(r"\bвечер\w*\b", normalized))
                    ordered_names = []
                    if ordered_pair:
                        for part in ordered_pair.groups():
                            ordered_names.append(next((category for word in re.findall(r"[а-яё]+", part)
                                if (category := resolve_category(word, db=getattr(self.search, "db", None)))), None))
                    for first, first_end, first_price in candidates:
                        for second, second_end, second_price in candidates:
                            if first.id == second.id or first_end > datetime.fromisoformat(second.date_start):
                                continue
                            if first.date_start[:10] != second.date_start[:10]:
                                continue
                            if day_evening and not ("12:00" <= first.date_start[11:16] < "18:00" <= second.date_start[11:16]):
                                continue
                            if ordered_names and ((ordered_names[0] and first.category != ordered_names[0]) or
                                                  (ordered_names[1] and second.category != ordered_names[1])):
                                continue
                            cost = (first_price + second_price) * party_size
                            if (total_budget is None or cost <= total_budget) and (pair is None or (cost, first.date_start) < (pair[0], pair[1].date_start)):
                                pair = (cost, first, first_end, first_price, second, second_end, second_price)
                    if pair:
                        cost, first, first_end, first_price, second, second_end, second_price = pair
                        basis = [first.id, second.id]
                        message = (f"1. {first.title}: {first.date_start[11:16]}–{first_end:%H:%M}, от {first_price} ₽ за билет.\n"
                                   f"2. {second.title}: {second.date_start[11:16]}–{second_end:%H:%M}, от {second_price} ₽ за билет.\n"
                                   f"Минимум на {party_size} чел.: ({first_price} + {second_price}) × {party_size} = {cost} ₽. "
                                   + (f"Общий бюджет до {total_budget} ₽ соблюдён. " if total_budget is not None else "") +
                                   "Время событий не пересекается; "
                                   "время переезда и наличие билетов каталог не подтверждает.")
                        stop = "completed"
                    else:
                        stop, message = "clarification", "Не удалось подтвердить пару с известным временем окончания, ценой и заданным бюджетом. Уточните условия."
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
                for event, _ in (plan.recommendations or []):
                    if event.id not in observed_now:
                        execute(ToolCall(name="get_event", arguments={"event_id": event.id}))
                        observed_now = {event_id for group in tool_ids for event_id in group}
                confirmed_stops = [item for item in plan.stops if item.event.id in observed_now]
                confirmed_recommendations = [item for item in (plan.recommendations or [])
                                             if item[0].id in observed_now]
                basis = ([item.event.id for item in confirmed_stops]
                         + [item[0].id for item in confirmed_recommendations])
                if confirmed_stops:
                    lines = [f"{'Предварительный план' if plan.total_travel_min is None else 'Маршрут'} для {party_size} чел.:"]
                    for number, item in enumerate(confirmed_stops, start=1):
                        if item.travel_from_previous_min:
                            lines.append(f"Переезд: {item.travel_from_previous_min} мин")
                        lines.append(f"{number}. {item.start_time:%d.%m %H:%M}–{item.end_time:%d.%m %H:%M} "
                                     f"{item.event.title} — {item.event.city}, {item.event.venue} "
                                     f"({item.party_price} ₽)")
                    travel_summary = ("время переездов неизвестно" if plan.total_travel_min is None
                                      else f"переезды: {plan.total_travel_min} мин.")
                    lines.append(f"Итого: {sum(item.party_price for item in confirmed_stops)} ₽ "
                                 f"на компанию; {travel_summary}")
                else:
                    lines = ["Подтверждённый маршрут по заданным условиям пока не найден."]
                for event, warning in confirmed_recommendations:
                    lines.append(f"Рекомендация: {event.title} — {warning}")
                if plan.conflict_explanation:
                    lines.append(plan.conflict_explanation)
                if len(confirmed_stops) < len(plan.stops):
                    lines.append("Часть событий маршрута не удалось подтвердить в каталоге.")
                message = "\n".join(lines)
                if len(confirmed_stops) == target_count:
                    stop = "completed"
                else:
                    stop = "clarification"
                    message = "\n".join(lines)
            else:
                if state.pending_confirmation:
                    state.pending_confirmation = None
                if (self.settings.adaptive_agent_enabled and self.settings.structured_deep_enabled
                        and trace.adaptive_route == AdaptiveRoute.DEEP and self.settings.ai_mode != "no-ai"
                        and structured_eligible(request.message, state, context.security_flags)):
                    structured_handled = True
                    trace.model_route = ModelRoute.HEAVY
                    plan, input_tokens, output_tokens, elapsed, error = self.structured.plan(request.message, state)
                    structured_llm_calls = 0 if error == "model_unavailable" else 1
                    stage_latency["structured_interpretation"] = elapsed
                    trace.input_tokens += input_tokens
                    trace.output_tokens += output_tokens
                    if structured_llm_calls:
                        selected.append(self.settings.heavy_model)
                        usage.append({"model_name": self.settings.heavy_model, "input_tokens": input_tokens,
                                      "output_tokens": output_tokens})
                    if error:
                        # A failed structured parse is never rendered as a comparison.
                        # Try the ordinary guarded HEAVY path and include both calls in latency.
                        structured_handled = False
                        fallback_reason = "structured_" + error
                    else:
                        result = StructuredDecisionEngine(self.registry, self.settings).decide(
                            plan, request.message, state, execute)
                        stop, message, basis = result.status, result.message, result.event_ids
                        search_call = result.last_search_call
                        if stop == "completed":
                            heavy_result_kind = "recommendation"
                        elif stop == "clarification":
                            state.pending_clarification = message
                if trace.adaptive_route == AdaptiveRoute.SMART and self.settings.ai_mode != "no-ai":
                    smart_started = time.perf_counter()
                    smart = self.smart.parse(request.message, state)
                    stage_latency["smart_interpretation"] = round((time.perf_counter() - smart_started) * 1000)
                    smart_llm_calls = smart.llm_calls
                    if smart.llm_calls:
                        selected.append(smart.model_name)
                        trace.input_tokens += smart.input_tokens
                        trace.output_tokens += smart.output_tokens
                        usage.append({"model_name": smart.model_name, "input_tokens": smart.input_tokens,
                                      "output_tokens": smart.output_tokens})
                    if smart.kind == "search":
                        smart_handled = True
                        trace.model_route = ModelRoute.LIGHT
                        constraints, search_call = smart.constraints, smart.call
                        result = execute(search_call)
                        if result.error:
                            stop, message = result.error.code, "Не удалось выполнить поиск."
                        else:
                            basis = result.result_ids
                    elif smart.kind == "clarification":
                        smart_handled = True
                        stop = "clarification"
                        message = ("Уточните город или категорию мероприятия." if smart.reason in
                                   {"unknown_city", "unknown_category"} else
                                   "Уточните город, дату, категорию или другое условие поиска.")
                        state.pending_clarification = message
                        fallback_reason = smart.reason
                    else:
                        smart_to_deep = True
                        trace.escalated = True
                        trace.model_route = ModelRoute.HEAVY
                        fallback_reason = "smart_unsupported_to_deep"
                if deterministic_route:
                    selected.append("deterministic_search")
                elif not structured_handled and not smart_handled and not smart_to_deep:
                    route_started = time.perf_counter()
                    decision = self.router.decide(features)
                    stage_latency["routing"] = round((time.perf_counter() - route_started) * 1000)
                    trace.router_decision = decision
                    trace.model_route = decision.route
                    if self.settings.ai_mode == "no-ai":
                        fallback_reason = "no_ai_mode"
                    elif decision.router_error or (decision.fallback and decision.reason_code == "router_failure"):
                        fallback_reason = "jev_unavailable"
                if not deterministic_route and not structured_handled and not smart_handled and not smart_to_deep and fallback_reason is None and trace.model_route == ModelRoute.LIGHT:
                    model_started = time.perf_counter()
                    light = self.light.parse(request.message, state)
                    stage_latency["qwen_light"] = round((time.perf_counter() - model_started) * 1000)
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
                if not deterministic_route and not structured_handled and not smart_handled and trace.model_route == ModelRoute.HEAVY and (fallback_reason in {None, "jev_unavailable"} or (fallback_reason or "").startswith("structured_") or smart_to_deep) and stop != "clarification":
                    model_started = time.perf_counter()
                    heavy = self.heavy.run(request.message, state, context=context, stop_on_confirmation=True)
                    stage_latency["qwen_heavy"] = round((time.perf_counter() - model_started) * 1000)
                    heavy_result_kind = heavy.result.kind
                    last_search_explanation = heavy.last_search_explanation
                    selected.append(heavy.model_name)
                    trace.model_name = heavy.model_name
                    trace.input_tokens += heavy.input_tokens
                    trace.output_tokens += heavy.output_tokens
                    usage.append({"model_name": heavy.model_name, "input_tokens": heavy.input_tokens,
                                  "output_tokens": heavy.output_tokens})
                    trace.number_of_steps = heavy.steps
                    trace.heavy_execution_mode = heavy.execution_mode
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
                        if fallback_reason == "jev_unavailable" or (fallback_reason or "").startswith("structured_"):
                            fallback_reason = None
                        basis = heavy.basis_event_ids
                        message = heavy.result.short_explanation
                    elif heavy.result.kind == "no_data":
                        if fallback_reason == "jev_unavailable" or (fallback_reason or "").startswith("structured_"):
                            fallback_reason = None
                        stop = "no_data"
                        message = " ".join(filter(None, [heavy.result.short_explanation,
                                                         heavy.result.constraint_relaxation,
                                                         last_search_explanation]))
                    elif heavy.result.kind == "cannot_satisfy":
                        if fallback_reason == "jev_unavailable" or (fallback_reason or "").startswith("structured_"):
                            fallback_reason = None
                        stop = "cannot_satisfy"
                        message = " ".join(filter(None, [heavy.result.short_explanation,
                                                         heavy.result.constraint_relaxation,
                                                         last_search_explanation]))
                    else:
                        fallback_reason = heavy.stop_reason
                if (fallback_reason or deterministic_route) and not structured_handled and not smart_handled and not smart_to_deep and stop not in {"clarification", "confirmation_required"}:
                    if any(context.security_flags.model_dump().values()):
                        stop, message = "model_unavailable", "Не могу надёжно обработать этот запрос. Уточните запрос о событии."
                    elif features.intent == "other" and not (state.pending_clarification and
                            re.fullmatch(r"\s*\d{1,2}[. ]\d{1,2}(?:[. ]\d{4})?\s*", request.message)):
                        stop, message = "clarification", "Уточните, пожалуйста, город, дату или интересующий вид мероприятия."
                    elif features.intent in {"subscribe", "save_feedback"}:
                        parsed = parse_free_text_query(request.message,
                            ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                        if features.intent == "subscribe":
                            city = parsed.city or state.active_constraints.city
                            category = parsed.category or state.active_constraints.category
                            replacing = bool(re.search(r"\b(?:перенеси|измени|смени|сменить|изменить)\b", normalized))
                            existing_subs = (self.registry.subscription_service.list_subscriptions(self.registry.user_id)
                                             if replacing else [])
                            if replacing and len(existing_subs) == 1:
                                category = category or existing_subs[0]["category"]
                            if not city or not category:
                                stop, message = "clarification", "Укажите город и категорию для подписки."
                            elif replacing and len(existing_subs) != 1:
                                stop, message = "clarification", "Уточните, какую подписку изменить: укажите её город и категорию."
                            else:
                                arguments = {"city": city, "category": category}
                                if replacing:
                                    arguments["replace_subscription_id"] = existing_subs[0]["id"]
                                if parsed.date_from or parsed.date_to:
                                    arguments["period"] = {"date_from": parsed.date_from[:10] if parsed.date_from else None,
                                                           "date_to": parsed.date_to[:10] if parsed.date_to else None}
                                state.pending_confirmation = ToolCall(name="subscribe", arguments=arguments)
                                stop, message = "confirmation_required", _confirmation_message(state.pending_confirmation)
                        else:
                            feedback = re.sub(r"^(?:оставь|оставить|отправь|отправить|напиши)\s+(?:отзыв|жалобу)\s*[:—-]?\s*", "", request.message, flags=re.I).strip()
                            if not feedback or (feedback == request.message and len(feedback.split()) < 4):
                                stop, message = "clarification", "Напишите текст отзыва, который нужно отправить."
                            else:
                                state.pending_confirmation = ToolCall(name="save_feedback", arguments={"message": feedback})
                                stop, message = "confirmation_required", _confirmation_message(state.pending_confirmation)
                    elif features.intent == "event_status":
                        parsed = parse_free_text_query(request.message,
                            ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                        if not parsed.date_from:
                            stop, message = "clarification", "Уточните дату события."
                        else:
                            clauses = ["date_start >= ?", "date_start <= ?", "merged_into IS NULL", "status IN ('published', 'cancelled')"]
                            params = [parsed.date_from, parsed.date_to]
                            if parsed.category:
                                clauses.append("category = ?")
                                params.append(parsed.category)
                            if parsed.city:
                                clauses.append("city = ?")
                                params.append(parsed.city)
                            rows = self.search.db.fetchall("SELECT id, title FROM events WHERE " + " AND ".join(clauses) + " ORDER BY id LIMIT 100", tuple(params))
                            if parsed.query_text:
                                from src.services.normalizer import normalize_text
                                words = normalize_text(parsed.query_text).split()
                                rows = [row for row in rows if all(word in normalize_text(row["title"]) for word in words)]
                            if len(rows) == 1:
                                result = execute(ToolCall(name="get_event", arguments={"event_id": rows[0]["id"]}))
                                if result.error is None:
                                    event = result.payload["event"]
                                    status = "отменено" if event["status"] == "cancelled" else "опубликовано"
                                    message = f"Событие «{event['title']}» {event['date_start'][:10]}: {status}. Источник: {event['source_url']}"
                                    stop = "completed"
                                else:
                                    stop, message = "no_data", "Не удалось проверить статус события."
                            elif rows:
                                stop, message = "clarification", "На эту дату найдено несколько событий. Уточните название или площадку."
                            else:
                                stop, message = "no_data", "Подходящее событие в каталоге не найдено; подтвердить отмену не могу."
                    elif features.intent == "find_similar":
                        if not state.last_result_ids:
                            stop, message = "clarification", "Укажите событие, для которого искать похожие."
                        else:
                            args = {"event_id": state.last_result_ids[0], "limit": 5}
                            if state.active_constraints.city:
                                args["city"] = state.active_constraints.city
                            result = execute(ToolCall(name="find_similar", arguments=args))
                            basis = result.result_ids if result.error is None else []
                            stop = "fallback_search" if basis else "no_data"
                            if not basis:
                                message = "Похожих опубликованных событий по заданным условиям не найдено."
                    elif features.intent == "get_venue_schedule":
                        from src.services.normalizer import normalize_text
                        parsed = parse_free_text_query(request.message,
                            ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                        names = [row["name"] for row in self.search.db.fetchall("SELECT name FROM venues")]
                        matches = [name for name in names if normalize_text(name) in normalize_text(request.message)]
                        if len(matches) != 1 or not parsed.date_from or not parsed.date_to:
                            stop, message = "clarification", "Укажите точное название площадки и период расписания."
                        else:
                            args = {"venue": matches[0], "date_from": parsed.date_from[:10],
                                    "date_to": parsed.date_to[:10]}
                            if parsed.city:
                                args["city"] = parsed.city
                            result = execute(ToolCall(name="get_venue_schedule", arguments=args))
                            basis = result.result_ids if result.error is None else []
                            stop = "fallback_search" if basis else "no_data"
                            if not basis:
                                message = "На этой площадке в указанный период опубликованных событий не найдено."
                    elif features.intent == "search_events" or (state.pending_clarification and
                            re.fullmatch(r"\s*\d{1,2}[. ]\d{1,2}(?:[. ]\d{4})?\s*", request.message)):
                        try:
                            filters = parse_free_text_query(request.message,
                                ref_date=self.settings.get_reference_date(), db=getattr(self.search, "db", None))
                            merged = (state.active_constraints.model_dump(mode="json", exclude_none=True)
                                      if features.requires_dialogue_context or state.pending_clarification else {})
                            for key in ConstraintSet.model_fields:
                                value = getattr(filters, key, None)
                                if value is not None and value is not False:
                                    merged[key] = value[:10] if key in {"date_from", "date_to"} else value
                            if re.search(r"\b(?:подешевле|дешевле)\b", normalized) and not filters.is_free_only:
                                current = state.active_constraints.max_price
                                merged["max_price"] = min(500, max(0, current - 1)) if current is not None else 500
                            if normalized in {"в другой день", "на другой день"}:
                                stop, message = "clarification", "На какую дату перенести поиск?"
                                state.pending_clarification = message
                            else:
                                constraints = ConstraintSet.model_validate(merged)
                                args = constraints.model_dump(mode="json", exclude_none=True)
                                if (getattr(filters, "query_text", None) and not features.requires_dialogue_context
                                        and not re.search(r"\b(?:доступност\w*|коляск\w*)\b", normalized)):
                                    args["text"] = filters.query_text[:500]
                                args["offset"] = state.next_search_offset if normalized in {"а еще", "а ещё"} else 0
                                search_call = ToolCall(name="search_events", arguments=args)
                                result = execute(search_call)
                                if result.error is None:
                                    basis = result.result_ids
                                    stop = "fallback_search" if basis else "no_data"
                                else:
                                    stop = result.error.code
                        except (ValueError, TypeError):
                            stop = "fallback_unavailable"
                    else:
                        stop, message = "model_unavailable", "Недостаточно данных для ответа. Уточните событие или запрос."

            if deterministic_route and stop == "fallback_search":
                stop = "completed"
            formatting_started = time.perf_counter()
            # Only IDs returned by successful tools in this request may be rendered.
            observed = {event_id for group in tool_ids for event_id in group}
            if stop == "no_data" and heavy_result_kind == "no_data" and search_call:
                # A model refusal cannot negate confirmed, matching search results.
                basis = [event_id for group in tool_ids for event_id in group
                         if (event := self.search.get_event_by_id(event_id)) and _matches(event, required)][:5]
                if basis:
                    stop, message = "completed", _search_explanation(required)
            explicit_cities = {city for word in re.findall(r"[а-яё]+", normalized)
                               if (city := resolve_city(word, db=getattr(self.search, "db", None)))}
            explicit_categories = {category for word in re.findall(r"[а-яё]+", normalized)
                                   if (category := resolve_category(word, db=getattr(self.search, "db", None)))}
            multi_city = len(explicit_cities) > 1
            render_required = required
            if structured_handled and len(explicit_categories) > 1:
                render_required = render_required.model_copy(update={"category": None})
            if planner_handled and len(explicit_categories) > 1:
                render_required = render_required.model_copy(update={"category": None})
            if structured_handled and not search_call:
                render_required = render_required.model_copy(update={"city": None, "category": None})
            if any(call.name == "find_similar" for call in calls) and not explicit_categories:
                render_required = render_required.model_copy(update={"category": None})
            for event_id in dict.fromkeys(basis):
                if event_id not in observed:
                    continue
                event = self.search.get_event_by_id(event_id)
                if event is None or event.id != event_id or (self.settings.adaptive_agent_enabled and not _matches(
                        event, render_required.model_copy(update={"city": None, "time_after": None,
                            "time_before": None, "max_price": None}) if planner_handled else render_required,
                        city=event.city if multi_city else None)):
                    continue
                cards.append(AgentEventCard(
                    id=event.id, title=event.title, category=event.category,
                    date_start=event.date_start, date_end=event.date_end,
                    city=event.city, venue=event.venue, address=event.address,
                    price_min=event.price_min, price_max=event.price_max,
                    is_free=event.is_free, age_limit=event.age_limit,
                    source_url=event.source_url, org_link=event.org_link))
            cards = cards[:5]
            if cards and len(cards) < len(set(basis)) and not planner_handled:
                message = _search_explanation(required)
            if not planner_handled and state.has_previous_results and re.search(
                    r"\b(?:пересчитай|общий\s+бюджет|на\s+двоих)\b", normalized) and re.search(
                    r"\b(?:замен\w*|вариант\w*|сравнен\w*|исключен\w*)\b", normalized):
                cards = []
                basis = []
                stop, message = "clarification", "Не удалось подтвердить полный пересчёт и сравнение по каталогу. Уточните два события и общий бюджет."
            if basis and not cards and stop == "completed":
                stop, message = "clarification", "Найденные события не соответствуют подтверждённым условиям. Уточните запрос."
            if basis and not cards:
                stop, message = "no_data", "Данные о найденных событиях недоступны."
            elif search_call and not cards and stop == "completed":
                stop, message = "no_data", last_search_explanation or "По вашему запросу событий не найдено. Попробуйте расширить период или убрать фильтр города."
            elif search_call and not cards and stop in {"fallback_search", "no_data"} and heavy_result_kind is None and not planner_handled and not followup:
                message = last_search_explanation or "По вашему запросу событий не найдено. Попробуйте расширить период или убрать фильтр города."
            elif cards:
                if heavy_result_kind != "recommendation" and not planner_handled and followup not in {"venue", "price"}:
                    message = _search_explanation(constraints)
                stop = "completed" if stop == "completed" else stop
                state.last_result_ids = [card.id for card in cards]
                state.last_search_results = cards if search_call else state.last_search_results
                if search_call:
                    state.selected_event_id = cards[0].id if len(cards) == 1 else None
                state.shown_event_ids = list(dict.fromkeys([*state.shown_event_ids, *state.last_result_ids]))
                state.has_previous_results = True
                if search_call:
                    if followup != "more_results":
                        state.next_search_offset = int(search_call.arguments.get("offset", 0)) + len(cards)
            elif search_call and stop == "no_data":
                state.last_search_results = []
                if followup != "more_results":
                    state.last_result_ids = []
                    state.selected_event_id = None
            if cards and re.search(r"\b(?:доступност\w*|коляск\w*)\b", normalized):
                message = _search_explanation(required) + " Доступность площадки для коляски в каталоге не подтверждена."
            if checked.decision == PolicyDecision.ALLOWED and stop not in {"internal_error", "fallback_unavailable"}:
                saved = (constraints or state.active_constraints).model_dump(mode="json", exclude_none=True)
                if self.settings.adaptive_agent_enabled:
                    for key, value in required.model_dump(mode="json", exclude_none=True).items():
                        if key not in saved or saved[key] is None:
                            saved[key] = value
                    parsed_now = parse_free_text_query(request.message, self.settings.get_reference_date(),
                                                       db=getattr(self.search, "db", None))
                    for key in ("city", "date_from", "date_to", "max_age_limit", "max_price"):
                        if getattr(parsed_now, key, None) is not None and not re.search(r"\b(?:подешевле|дешевле)\b", normalized):
                            saved[key] = getattr(required, key).isoformat() if key in {"date_from", "date_to"} else getattr(required, key)
                state.active_constraints = ConstraintSet.model_validate(saved)
            if stop not in {"clarification", "confirmation_required"}:
                state.pending_clarification = None
            if checked.decision == PolicyDecision.ALLOWED:
                state.last_user_intent = followup or features.intent
            state.fast_context_safe = (trace.adaptive_route == AdaptiveRoute.FAST and
                                       stop not in {"internal_error", "fallback_unavailable"})
            stage_latency["response_formatting"] = round((time.perf_counter() - formatting_started) * 1000)
        except Exception:
            stop, message = "internal_error", "Сервис временно недоступен. Попробуйте позже."
            state.fast_context_safe = False
        finally:
            trace.inference_diagnostics = inference_recorder.rows
            router_usage = [row for row in inference_recorder.rows
                            if row.get("route") == "ROUTER" and row.get("model_alias")]
            trace.input_tokens += sum(max(0, int(row.get("prompt_tokens") or 0))
                                      for row in router_usage)
            trace.output_tokens += sum(max(0, int(row.get("completion_tokens") or 0))
                                       for row in router_usage)
            for row in router_usage:
                usage.append({
                    "model_name": str(row["model_alias"]),
                    "input_tokens": max(0, int(row.get("prompt_tokens") or 0)),
                    "output_tokens": max(0, int(row.get("completion_tokens") or 0)),
                })
            stage_latency["tool_execution"] = round(context.tool_latency_ms)
            stage_latency["jev_inference"] = sum(int(row.get("inference_latency_ms") or 0)
                for row in inference_recorder.rows if row.get("route") == "ROUTER")
            stage_latency["qwen_inference"] = sum(int(row.get("inference_latency_ms") or 0)
                for row in inference_recorder.rows if row.get("route") in {"LIGHT", "HEAVY", "SMART", "STRUCTURED_DEEP"})
            stage_latency["json_pydantic_validation"] = sum(
                int(row.get("structured_validation_latency_ms") or 0) +
                int(row.get("json_validation_latency_ms") or 0)
                for row in inference_recorder.rows) + round(context.tool_validation_latency_ms)
            trace.stage_latency_ms = stage_latency
            trace.execution_path = ("DETERMINISTIC_DEEP" if deterministic_deep_handled else "STRUCTURED_DEEP" if structured_handled else "SMART_TO_DEEP" if smart_to_deep else "SMART" if smart_handled
                                    else "FAST" if trace.adaptive_route == AdaptiveRoute.FAST
                                    else "DEEP" if trace.model_route == ModelRoute.HEAVY
                                    else "LEGACY")
            trace.llm_call_count = len(inference_recorder.rows) + max(0, smart_llm_calls - sum(
                row.get("route") == "SMART" for row in inference_recorder.rows)) + max(
                0, structured_llm_calls - sum(row.get("route") == "STRUCTURED_DEEP"
                                              for row in inference_recorder.rows))
            deactivate_inference(inference_token)
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
            trace.user_query = None
            trace.plan_summary = f"{('fallback' if fallback_reason else trace.model_route.value)} intent={features.intent}; tools={','.join(call.name for call in calls) or 'none'}"
            safe_args = {"city", "category", "date_from", "date_to", "venue", "event_id",
                         "time_after", "time_before", "max_duration_min", "limit", "offset", "max_price",
                         "max_age_limit", "is_free_only"}
            trace.tool_calls = [ToolCall(name=call.name, arguments={
                key: value
                for key, value in call.arguments.items() if key in safe_args
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

