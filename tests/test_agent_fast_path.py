"""FAST uses the real catalog tools without either model router or LLM."""

import pytest

from src.agent.adaptive_router import AdaptiveRoute
from tests.test_agent_followup_p0 import ask, ids, service, state


def fast_service(tmp_path):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True

    class NoModel:
        def __init__(self, *args):
            pass

        def decide(self, *args):
            raise AssertionError("Jev was called")

        def parse(self, *args):
            raise AssertionError("Qwen LIGHT was called")

        def run(self, *args):
            raise AssertionError("Qwen HEAVY was called")

    svc.router_factory = svc.light_factory = svc.heavy_factory = NoModel
    return svc


def assert_fast(svc, reply):
    trace = svc.get_trace(reply.trace_id)
    assert trace.adaptive_route == AdaptiveRoute.FAST
    assert trace.router_decision is None
    assert trace.llm_call_count == 0
    assert trace.inference_diagnostics == []
    assert len(trace.tool_calls) <= 5
    return trace


def test_fast_sequential_search_price_weekend_and_more(tmp_path):
    svc = fast_service(tmp_path)
    first = ask(svc, "Концерты в Уфе")
    assert first.result_ids == ids(9)
    assert_fast(svc, first)
    price = ask(svc, "Сколько стоит?")
    assert "700" in price.message and price.result_ids == ids(9)
    assert assert_fast(svc, price).tool_calls[0].name == "get_event"
    weekend = ask(svc, "На выходных")
    assert weekend.result_ids == ids(9)
    assert_fast(svc, weekend)
    more = ask(svc, "А ещё?")
    assert more.result_ids == [] and "больше" in more.message.casefold()
    assert_fast(svc, more)
    filters = state(svc).active_constraints
    assert (filters.city, filters.category) == ("Уфа", "концерт")
    assert (filters.date_from.isoformat(), filters.date_to.isoformat()) == ("2026-11-14", "2026-11-15")


def test_fast_cheaper_empty_result_and_city_switch_preserve_filters(tmp_path):
    svc = fast_service(tmp_path)
    assert_fast(svc, ask(svc, "Концерты в Уфе"))
    cheaper = ask(svc, "Подешевле")
    assert cheaper.result_ids == []
    assert_fast(svc, cheaper)
    changed = ask(svc, "А в Салавате?")
    assert_fast(svc, changed)
    filters = state(svc).active_constraints
    assert (filters.city, filters.category, filters.max_price) == ("Салават", "концерт", 699)
    assert all(card.city == "Салават" and card.category == "концерт" and card.price_min <= 699
               for card in changed.event_cards)


def test_fast_price_requires_unambiguous_current_event(tmp_path):
    svc = fast_service(tmp_path)
    assert_fast(svc, ask(svc, "Выставки в Уфе"))
    assert len(state(svc).last_result_ids) > 1
    # The router may choose legacy handling, but the deterministic handler must ask.
    reply = ask(svc, "Сколько стоит?")
    trace = svc.get_trace(reply.trace_id)
    assert trace.adaptive_route != AdaptiveRoute.FAST
    assert trace.stop_reason == "clarification" and not trace.tool_calls


def test_fast_excludes_cancelled_events_and_rechecks_price(tmp_path):
    svc = fast_service(tmp_path)
    assert_fast(svc, ask(svc, "Концерты в Уфе"))
    svc.database.execute("UPDATE events SET status = 'cancelled' WHERE id = ?", (ids(9)[0],))
    price = ask(svc, "Сколько стоит?")
    assert_fast(svc, price)
    assert price.result_ids == [] and "подтвердить" in price.message
    # A new search must not return a cancelled event.
    empty = ask(svc, "Концерты в Уфе", "fresh")
    assert_fast(svc, empty)
    assert empty.result_ids == [] and empty.event_cards == []


@pytest.mark.parametrize("message", [
    "Сколько стоит?", "Подешевле", "На выходных", "А ещё?", "А в Уфе?",
])
def test_fast_requires_prior_context(tmp_path, message):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    reply = ask(svc, message)
    assert svc.get_trace(reply.trace_id).adaptive_route != AdaptiveRoute.FAST


def test_adaptive_flag_off_keeps_legacy_routing(tmp_path):
    svc, settings = service(tmp_path)
    assert settings.adaptive_agent_enabled is False
    first = ask(svc, "Концерты в Уфе")
    followup = ask(svc, "А ещё?")
    assert first.result_ids == ids(9)
    assert followup.result_ids == []
    assert svc.get_trace(first.trace_id).adaptive_route is None
    assert svc.get_trace(followup.trace_id).adaptive_route is None


def test_deep_detail_and_similar_use_confirmed_catalog_without_models(tmp_path):
    svc = fast_service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)
    detail = ask(svc, "Расскажи подробнее об этом событии")
    detail_trace = svc.get_trace(detail.trace_id)
    assert detail.result_ids == ids(9)
    assert "Джазовый вечер" in detail.message
    assert detail_trace.execution_path == "DETERMINISTIC_DEEP"
    assert detail_trace.llm_call_count == 0
    assert [call.name for call in detail_trace.tool_calls] == ["get_event"]
    similar = ask(svc, "Покажи похожие мероприятия")
    similar_trace = svc.get_trace(similar.trace_id)
    assert similar.result_ids
    assert ids(9)[0] not in similar.result_ids
    assert similar_trace.execution_path == "DETERMINISTIC_DEEP"
    assert similar_trace.llm_call_count == 0
    assert [call.name for call in similar_trace.tool_calls] == ["find_similar"]


def test_deep_similar_resolves_unique_confirmed_event_after_empty_search(tmp_path):
    svc = fast_service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)
    assert ask(svc, "Подешевле").result_ids == []
    similar = ask(svc, "Покажи похожие.")
    trace = svc.get_trace(similar.trace_id)
    assert similar.result_ids and trace.tool_calls[0].arguments["event_id"] == ids(9)[0]
    assert trace.llm_call_count == 0
