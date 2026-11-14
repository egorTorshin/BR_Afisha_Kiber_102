from src.agent.adaptive_router import AdaptiveIntentRouter, AdaptiveRoute
from src.agent.routing import RoutingFeatureExtractor
from src.agent.security import SecurityFlags
from src.agent.state import DialogueState
from src.agent.schemas import ConstraintSet
from src.config import Settings


def choose(message, state=None, **kwargs):
    state = state or DialogueState(conversation_id="c")
    features = RoutingFeatureExtractor().extract(message, state, SecurityFlags())
    return AdaptiveIntentRouter().decide(message, state, features, **kwargs)


def test_route_classes_and_ambiguous_legacy():
    assert choose("Концерты в Уфе").route == AdaptiveRoute.FAST
    assert choose("Какие концерты есть в Уфе?").route == AdaptiveRoute.SMART
    assert choose("Сравни концерты и выставки").route == AdaptiveRoute.DEEP
    assert choose("Составь план на день из нескольких событий").reason == "multi_step"
    assert choose("Что-нибудь интересное").reason == "ambiguous_legacy"
    assert choose("Подешевле", deterministic_followup=True, followup_kind="cheaper").route == AdaptiveRoute.DEEP


def test_state_and_security_keep_legacy_path():
    state = DialogueState(conversation_id="c", pending_clarification="Какой город?")
    assert choose("Концерты в Уфе", state).route == AdaptiveRoute.DEEP
    assert choose("Концерты в Уфе", input_allowed=False).reason == "security_legacy"


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("ADAPTIVE_AGENT_ENABLED", raising=False)
    assert Settings().adaptive_agent_enabled is False


def test_fast_requires_context_created_by_fast_and_no_hidden_filters():
    state = DialogueState(conversation_id="c", has_previous_results=True,
                          last_user_intent="search_events",
                          active_constraints=ConstraintSet(city="Уфа", category="концерт"))
    assert choose("А ещё?", state, deterministic_followup=True,
                  followup_kind="more_results").route == AdaptiveRoute.DEEP
    assert choose("Концерты в Уфе", state).route != AdaptiveRoute.FAST


def test_explanation_and_comparison_do_not_use_fast():
    assert choose("Объясни, почему этот концерт лучше").route == AdaptiveRoute.DEEP
    assert choose("Сравни концерты и выставки").route == AdaptiveRoute.DEEP


def test_vague_request_and_ticket_purchase_use_legacy_deep():
    assert choose("Покажи что-нибудь интересное").route == AdaptiveRoute.DEEP
    assert choose("Купи мне билет на концерт").route == AdaptiveRoute.DEEP


def test_child_catalog_request_uses_single_search_interpretation():
    assert choose("Хочу сходить куда-нибудь с ребёнком.").route == AdaptiveRoute.SMART
    assert choose("Посоветуй, куда сходить с ребёнком").route == AdaptiveRoute.DEEP
