"""Regression cases for catalog grounding and demonstration safety."""

from datetime import date
from types import SimpleNamespace

from src.services.normalizer import parse_free_text_query
from tests.test_agent_followup_p0 import ask, service, state
from src.agent.schemas import ModelRoute
from tests.test_agent_orchestrator import setup, run, call, final
from src.agent.heavy_llm import HeavyLLMProvider
from src.agent.state import DialogueState
from src.agent.tools import FindSimilarInput, FindSimilarOutput, ToolDefinition, ToolRegistry
from src.config import Settings
from tests.test_agent_orchestrator import Transport
from tests.test_agent_structured_deep import structured_service, plan


def test_recommendation_verb_is_not_search_text(tmp_path):
    svc, settings = service(tmp_path)
    settings.ai_mode = "no-ai"
    parsed = parse_free_text_query("Посоветуй в Уфе спектакль на 14 ноября для ребёнка 7 лет до 500 рублей",
                                   date(2026, 11, 14), svc.database)
    assert parsed.query_text is None
    reply = ask(svc, "Посоветуй в Уфе спектакль на 14 ноября для ребёнка 7 лет до 500 рублей")
    assert reply.result_ids and all(card.city == "Уфа" and card.age_limit <= 7 for card in reply.event_cards)


def test_spelled_age_and_saturday_are_explicit(tmp_path):
    svc, _ = service(tmp_path)
    parsed = parse_free_text_query("В Уфе на субботу с семилетним ребёнком до 600 рублей",
                                   date(2026, 11, 14), svc.database)
    assert parsed.max_age_limit == 7
    assert parsed.date_from[:10] == "2026-11-14"


def test_verified_pair_has_two_events_and_budget(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.ai_mode = "no-ai"
    reply = ask(svc, "Выбрать два события 14 ноября в Стерлитамаке для ребёнка 6 лет без пересечения по времени, суммарно до 1000 рублей на двоих")
    assert len(reply.event_cards) == 2
    assert "900 ₽" in reply.message
    assert "не пересекается" in reply.message
    assert all(card.city == "Стерлитамак" and card.age_limit <= 6 for card in reply.event_cards)


def test_day_and_evening_pair_uses_distinct_time_slots(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.ai_mode = "no-ai"
    reply = ask(svc, "Подбери на двоих два события в Уфе на 15 ноября: одно днём и одно вечером, общий бюджет до 1800 рублей")
    assert len(reply.event_cards) == 2
    assert "12:00" <= reply.event_cards[0].date_start[11:16] < "18:00"
    assert reply.event_cards[1].date_start[11:16] >= "18:00"
    assert "1400 ₽" in reply.message


def test_ordered_categories_form_complete_pair(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.ai_mode = "no-ai"
    reply = ask(svc, "Сначала выставка, потом концерт в Уфе в воскресенье")
    assert [card.category for card in reply.event_cards] == ["выставка", "концерт"]
    assert "не пересекается" in reply.message


def test_accessibility_is_unconfirmed(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.ai_mode = "no-ai"
    reply = ask(svc, "Порекомендуй бесплатное событие в Уфе 15 ноября для ребёнка 5 лет и скажи о доступности для коляски")
    assert reply.result_ids
    assert "не подтверждена" in reply.message
    assert all(card.is_free and card.age_limit <= 5 for card in reply.event_cards)


def test_dialogue_keeps_hard_constraints_after_result(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.ai_mode = "no-ai"
    ask(svc, "Посоветуй в Уфе на субботу с семилетним ребёнком до 600 рублей")
    current = state(svc).active_constraints
    assert (current.city, current.date_from.isoformat(), current.max_age_limit, current.max_price) == (
        "Уфа", "2026-11-14", 7, 600)
    followup = ask(svc, "А после обеда какой вариант выбрать?")
    assert all(card.city == "Уфа" and card.age_limit <= 7 for card in followup.event_cards)
    assert len(followup.event_cards) == 1
    assert "минимальной подтверждённой цене" in followup.message
    trace = svc.get_trace(followup.trace_id)
    assert trace.execution_path == "DETERMINISTIC_DEEP"
    assert [call.name for call in trace.tool_calls] == ["search_events"]


def test_unverified_context_budget_recalculation_is_clarification(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.ai_mode = "no-ai"
    ask(svc, "Спектакли в Уфе")
    reply = ask(svc, "Теперь оставь только варианты, на которые хватит 900 рублей на двоих, и объясни исключения")
    trace = svc.get_trace(reply.trace_id)
    assert trace.stop_reason == "clarification" and not reply.result_ids
    assert not trace.tool_calls and trace.llm_call_count == 0


def test_model_no_data_cannot_negate_confirmed_match():
    agent, _, _ = setup(route=ModelRoute.HEAVY,
                        steps=[call(args={"city": "Уфа", "category": "концерт", "max_price": 500}),
                               final(kind="no_data", question="Nothing found")])
    reply, trace, _ = run(agent, "Посоветуй концерт в Уфе до 500 рублей")
    assert trace.stop_reason == "completed"
    assert reply.result_ids
    assert "Nothing found" not in reply.message


def test_model_cannot_resolve_unconfirmed_event_id():
    called = []
    registry = ToolRegistry([ToolDefinition("find_similar", FindSimilarInput, FindSimilarOutput,
                                            lambda args: called.append(args.event_id) or FindSimilarOutput(events=[]))])
    registry.db = SimpleNamespace(fetchall=lambda *args: [])
    heavy = HeavyLLMProvider(Settings(heavy_model="test"), registry,
                             transport=Transport([call("find_similar", {"event_id": "search_events"})]))
    result = heavy.run("Покажи похожие", DialogueState(conversation_id="c"))
    assert result.result.kind == "clarification"
    assert not called


def test_shared_explicit_category_and_start_time_are_grounded(tmp_path):
    targets = [{"city": "Уфа", "category": "семейные спектакли"},
               {"city": "Стерлитамак", "category": "семейные спектакли"}]
    svc, _ = structured_service(tmp_path, plan(targets=targets, criteria=["price", "start_time"]))
    reply = ask(svc, "Сравни семейные спектакли в Уфе и Стерлитамаке на 14–15 ноября: где дешевле для ребёнка 7 лет и какие начинаются до 15:00?")
    trace = svc.get_trace(reply.trace_id)
    assert len(trace.tool_calls) == 2
    assert all(call.arguments["category"] == "спектакль" for call in trace.tool_calls)
    assert all("max_price" not in call.arguments and "time_before" not in call.arguments
               for call in trace.tool_calls)
    assert reply.result_ids and all(card.date_start[11:16] < "15:00" and card.age_limit <= 7 for card in reply.event_cards)
    assert "до 15:00" in reply.message
