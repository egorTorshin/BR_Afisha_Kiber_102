"""Structured DEEP uses one interpretation and only catalog-backed decisions."""

import json

import pytest

from src.agent.structured_deep import StructuredDeepPlanner, eligible
from src.agent.decision_router import DecisionRouter
from src.agent.security import SecurityFlags
from src.agent.state import DialogueState
from tests.test_agent_followup_p0 import ask, ids, service, state


class FixedTransport:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return self.output, 21, 9


def structured_service(tmp_path, output):
    svc, settings = service(tmp_path)
    settings.adaptive_agent_enabled = True
    settings.structured_deep_enabled = True
    settings.ai_mode = "local-ai"
    transport = FixedTransport(output)
    svc.structured_factory = lambda settings, registry: StructuredDeepPlanner(settings, registry, transport=transport)

    class NoJev:
        def __init__(self, *args):
            pass

        def decide(self, *args):
            raise AssertionError("Jev was called")

    svc.router_factory = NoJev
    return svc, transport


def plan(intent="compare_groups", targets=None, criteria=None, filters=None):
    return json.dumps({"intent": intent, "filters": filters or {},
                       "comparison_targets": targets or [],
                       "comparison_criteria": criteria or ["price"]}, ensure_ascii=False)


@pytest.mark.parametrize("case,query,targets,expected", [
    ("C01", "Сравни концерты в Уфе и Стерлитамаке по цене",
     [{"city": "Уфа", "category": "концерт"}, {"city": "Стерлитамак", "category": "концерт"}], None),
    ("C02", "Что дешевле: экскурсия в Уфе или Стерлитамаке?",
     [{"city": "Уфа", "category": "экскурсия"}, {"city": "Стерлитамак", "category": "экскурсия"}], None),
    ("C05", "Что выгоднее для ребёнка 8 лет: выставка в Уфе или спектакль в Стерлитамаке?",
     [{"city": "Уфа", "category": "выставка"}, {"city": "Стерлитамак", "category": "спектакль"}], None),
])
def test_gold_comparisons_one_qwen_no_jev(tmp_path, case, query, targets, expected):
    svc, transport = structured_service(tmp_path, plan(targets=targets))
    reply = ask(svc, query)
    trace = svc.get_trace(reply.trace_id)
    assert trace.execution_path == "STRUCTURED_DEEP", case
    assert trace.router_decision is None and transport.calls == 1 and trace.llm_call_count == 1
    assert trace.input_tokens == 21 and trace.output_tokens == 9
    assert len(trace.tool_calls) == 2 and all(call.name == "search_events" for call in trace.tool_calls)
    if case == "C02":
        assert all("max_price" not in call.arguments for call in trace.tool_calls)
    assert set(reply.result_ids).issubset(set().union(*map(set, trace.tool_result_ids)))
    assert all(card.city in {"Уфа", "Стерлитамак"} for card in reply.event_cards)
    assert all(card.age_limit <= 8 for card in reply.event_cards) if case == "C05" else True


def test_cheapest_free_and_missing_prices(tmp_path):
    svc, transport = structured_service(tmp_path, plan("cheapest", [{"city": "Уфа", "category": "выставка"}]))
    reply = ask(svc, "Найди самое дешёвое мероприятие: выставка в Уфе")
    trace = svc.get_trace(reply.trace_id)
    assert trace.execution_path == "STRUCTURED_DEEP" and transport.calls == 1
    assert reply.event_cards and reply.event_cards[0].is_free
    assert "бесплатно" in reply.message
    svc.database.execute("UPDATE events SET price_min=NULL, price_max=NULL, is_free=0 WHERE category='выставка' AND city='Уфа'")
    reply = ask(svc, "Найди самое дешёвое мероприятие: выставка в Уфе", session="other")
    assert svc.get_trace(reply.trace_id).stop_reason == "clarification"
    assert not reply.result_ids


def test_dates_and_start_times(tmp_path):
    targets = [{"city": "Уфа", "category": "выставка"},
               {"city": "Стерлитамак", "category": "спектакль"}]
    svc, transport = structured_service(tmp_path, plan(targets=targets, criteria=["date", "start_time"]))
    reply = ask(svc, "Сравни выставки в Уфе и спектакли в Стерлитамаке по датам и времени начала")
    assert svc.get_trace(reply.trace_id).execution_path == "STRUCTURED_DEEP"
    assert "Сравнение расписания" in reply.message and len(reply.event_cards) == 2


def test_invalid_json_and_fabricated_ids_are_rejected(tmp_path):
    svc, transport = structured_service(tmp_path, "{broken")
    reply = ask(svc, "Сравни концерты в Уфе и Стерлитамаке по цене")
    trace = svc.get_trace(reply.trace_id)
    assert trace.execution_path == "DEEP" and trace.fallback_reason == "structured_invalid_json"
    assert transport.calls == 1 and not reply.result_ids
    transport.output = json.dumps({"intent": "cheapest", "filters": {},
        "comparison_targets": [{"city": "Уфа"}], "comparison_criteria": ["price"],
        "event_ids": ["invented"]})
    reply = ask(svc, "Найди самое дешёвое мероприятие в Уфе", session="other")
    assert svc.get_trace(reply.trace_id).execution_path == "DEEP" and not reply.result_ids


def test_parse_distinguishes_schema_failure_from_invalid_json(tmp_path):
    svc, transport = structured_service(tmp_path, json.dumps({
        "intent": "compare_groups", "comparison_criteria": ["price"]}))
    reply = ask(svc, "Сравни концерты в Уфе и Стерлитамаке по цене")
    trace = svc.get_trace(reply.trace_id)
    assert trace.fallback_reason == "structured_schema_validation"
    assert transport.calls == 1 and not reply.result_ids


def test_ambiguous_unknown_and_cancelled(tmp_path):
    svc, transport = structured_service(tmp_path, plan("clarify", criteria=[]))
    reply = ask(svc, "Сравни мероприятия по цене")
    assert svc.get_trace(reply.trace_id).stop_reason == "clarification"
    transport.output = plan(targets=[{"city": "Казань", "category": "концерт"},
                                      {"city": "Уфа", "category": "концерт"}])
    reply = ask(svc, "Сравни концерты в Казани и Уфе по цене", session="other")
    assert svc.get_trace(reply.trace_id).stop_reason == "clarification"
    reply = ask(svc, "Сравни категорию цирк в Уфе и Стерлитамаке по цене", session="unknown-category")
    assert svc.get_trace(reply.trace_id).stop_reason == "clarification"
    transport.output = plan(targets=[{"city": "Уфа", "category": "концерт"},
                                      {"city": "Стерлитамак", "category": "концерт"}])
    svc.database.execute("UPDATE events SET status='cancelled' WHERE id=?", (ids(9)[0],))
    reply = ask(svc, "Сравни концерты в Уфе и Стерлитамаке по цене", session="third")
    assert ids(9)[0] not in reply.result_ids


def test_context_and_unsupported_goes_directly_to_heavy(tmp_path):
    svc, transport = structured_service(tmp_path, plan("cheapest", [{"city": "Уфа", "category": "концерт"}]))
    first = ask(svc, "Найди самое дешёвое мероприятие: концерт в Уфе")
    assert first.result_ids
    assert state(svc).last_result_ids == first.result_ids
    assert not eligible("Спланируй два мероприятия в Уфе", DialogueState(conversation_id="x"), SecurityFlags())
    assert not eligible("Что ближе?", DialogueState(conversation_id="x"), SecurityFlags())
    assert not eligible("Сравни концерты в Уфе и Стерлитамаке по цене",
                        DialogueState(conversation_id="x"), SecurityFlags(possible_prompt_injection=True))


def test_known_events_use_context_ids_only_after_get_event(tmp_path):
    svc, settings = service(tmp_path)
    first = ask(svc, "Покажи мероприятия")
    assert len(first.event_cards) >= 3
    left, right, third = first.event_cards[:3]
    settings.adaptive_agent_enabled = settings.structured_deep_enabled = True
    settings.ai_mode = "local-ai"
    transport = FixedTransport(plan("compare_known", [{"title": left.title}, {"title": right.title},
                                                   {"title": third.title}],
                                    ["price", "date", "start_time"]))
    svc.structured_factory = lambda settings, registry: StructuredDeepPlanner(settings, registry, transport=transport)

    class NoJev:
        def __init__(self, *args):
            pass

        def decide(self, *args):
            raise AssertionError("Jev was called")

    svc.router_factory = NoJev
    reply = ask(svc, f"Сравни {left.title}, {right.title} и {third.title} по цене и расписанию")
    trace = svc.get_trace(reply.trace_id)
    assert trace.execution_path == "STRUCTURED_DEEP" and trace.llm_call_count == 1
    assert [call.name for call in trace.tool_calls] == ["get_event"] * 3
    assert set(reply.result_ids) == {left.id, right.id, third.id}
    assert "Начало:" in reply.message
    assert len(trace.tool_calls) <= 5
