"""Conservative, deterministic choice of the future agent execution tier."""

import re
from enum import Enum

from pydantic import Field

from src.agent.schemas import ConstraintSet, ContractModel, RoutingFeatures
from src.agent.state import DialogueState


class AdaptiveRoute(str, Enum):
    FAST = "FAST"
    SMART = "SMART"
    DEEP = "DEEP"


class AdaptiveDecision(ContractModel):
    route: AdaptiveRoute
    reason: str = Field(min_length=1)


_DIRECT_SEARCH = re.compile(
    r"(?:покажи\s+|найди\s+)?(?:концерты|спектакли|выставки|кино)"
    r"(?:\s+в\s+(?:уфе|стерлитамаке|салавате|нефтекамске|бирске|сибае|белорецке|ишимбае|октябрьском|туймазах))?"
    r"[?.!]*",
    re.IGNORECASE,
)


class AdaptiveIntentRouter:
    """FAST needs an exact deterministic handler; uncertainty keeps legacy execution."""

    def decide(self, message: str, state: DialogueState, features: RoutingFeatures, *,
               deterministic_followup: bool = False, followup_kind: str | None = None,
               input_allowed: bool = True) -> AdaptiveDecision:
        if not input_allowed or any((features.possible_prompt_injection, features.possible_jailbreak,
                                     features.possible_prompt_leak_attempt, features.possible_sql_injection)):
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="security_legacy")
        if state.pending_confirmation:
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="confirmation_legacy")
        if state.pending_clarification:
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="dialogue_legacy")
        if features.intent == "planner" or features.requires_multi_step:
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="multi_step")
        if re.search(r"\b(?:посоветуй|порекомендуй|предложи|лучше|подбери|объясни|почему)\b", message, re.I):
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="recommendation_or_explanation")
        if re.search(r"\b(?:купи|купить|оформи|закажи)\b", message, re.I):
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="unsupported_purchase")
        if (not state.turns and re.search(r"\b(?:с|для)\s+реб[её]нк\w*\b", message, re.I)
                and re.search(r"\b(?:куда[- ]нибудь|что[- ]нибудь|мероприяти\w*|событи\w*)\b", message, re.I)
                and not re.search(r"\b(?:посовет\w*|порекоменду\w*|спланир\w*|сравн\w*)\b", message, re.I)):
            return AdaptiveDecision(route=AdaptiveRoute.SMART, reason="child_catalog_search")
        if (features.constraint_count == 0 and
                re.search(r"\b(?:интересн\w*|что[- ]нибудь)\b", message, re.I)):
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="ambiguous_legacy")
        if (features.intent == "search_events" and features.constraint_count == 0 and
                re.search(r"\b(?:интересн\w*|что[- ]нибудь)\b", message, re.I)):
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="ambiguous_legacy")
        if deterministic_followup and followup_kind in {"more_results", "cheaper", "price", "refine_search"}:
            if not state.fast_context_safe or state.last_user_intent not in {
                    "search_events", "refine_search", "more_results", "cheaper", "price"}:
                return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="unsafe_context_legacy")
            if followup_kind == "price":
                if len(state.last_result_ids) == 1 and state.selected_event_id == state.last_result_ids[0]:
                    return AdaptiveDecision(route=AdaptiveRoute.FAST, reason="exact_followup")
            elif state.has_previous_results and (followup_kind != "cheaper" or state.last_search_results):
                return AdaptiveDecision(route=AdaptiveRoute.FAST, reason="exact_followup")
        if features.requires_dialogue_context:
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="dialogue_legacy")
        if features.requires_comparison:
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="comparison")
        if features.intent != "search_events":
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="unsupported_intent_legacy")
        if features.requires_clarification:
            return AdaptiveDecision(route=AdaptiveRoute.DEEP, reason="ambiguous_legacy")
        if (_DIRECT_SEARCH.fullmatch(message.strip().replace("ё", "е")) and not state.turns
                and state.active_constraints == ConstraintSet()):
            return AdaptiveDecision(route=AdaptiveRoute.FAST, reason="exact_catalog_search")
        return AdaptiveDecision(route=AdaptiveRoute.SMART, reason="natural_language_search")
