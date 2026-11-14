"""P0 dialogue regressions against the 18 published audit events (no LLM calls)."""

import json
import uuid
from pathlib import Path

from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.agent_service import AgentService


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "tests/fixtures/agent_audit_events.json"
PREFIX = "11111111-1111-4111-8111-"


def ids(*numbers):
    return [PREFIX + f"{number:012d}" for number in numbers]


def service(tmp_path):
    target = tmp_path / "audit.db"
    database = Database(str(target))
    apply_migrations(database, ROOT / "migrations")
    for record in json.loads(SEED.read_text()):
        fields = {key: value for key, value in record.items() if key != "raw_event_id"}
        database.execute(
            f"INSERT INTO events ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            tuple(fields.values()),
        )
    settings = Settings(ai_mode="no-ai", database_url=f"sqlite:///{target}",
                        rate_limit_secret="p0-dialogue-regression-secret-32-chars",
                        reference_date_str="2026-11-14")
    return AgentService(database, settings), settings


def ask(svc, message, session="one"):
    return svc.chat(message, session, user_id="p0-test", request_id=uuid.uuid4().hex).response


def state(svc, session="one"):
    return svc.dialogue_repo.get(session)


def test_more_keeps_concert_filters_and_never_repeats(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)
    more = ask(svc, "А ещё?")
    assert more.result_ids == [] and "больше" in more.message.casefold()
    assert ask(svc, "А ещё?").result_ids == []
    assert state(svc).active_constraints.city == "Уфа"
    assert state(svc).active_constraints.category == "концерт"
    assert state(svc).shown_event_ids == ids(9)


def test_cheaper_uses_shown_price_and_keeps_filters(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)  # 700 ₽ minimum
    cheaper = ask(svc, "Подешевле")
    assert cheaper.result_ids == []  # no cheaper Ufa concert in audit seed
    assert state(svc).active_constraints.max_price == 699
    assert state(svc).active_constraints.city == "Уфа"
    assert state(svc).active_constraints.category == "концерт"
    assert ask(svc, "А ещё?").result_ids == []


def test_other_day_clarifies_without_inventing_date(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Концерты в Уфе в воскресенье").result_ids == ids(9)
    reply = ask(svc, "В другой день")
    assert reply.result_ids == [] and "дат" in reply.message.casefold()
    assert state(svc).pending_clarification
    assert state(svc).active_constraints.date_from is None
    assert state(svc).active_constraints.date_to is None


def test_other_city_clarifies_instead_of_searching_a_made_up_city(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Спектакли в Салавате").result_ids == ids(16)
    reply = ask(svc, "А если в другом городе?")
    assert reply.result_ids == []
    assert "какой город" in reply.message.casefold()
    assert state(svc).pending_clarification
    assert state(svc).active_constraints.city == "Салават"


def test_venue_uses_only_unambiguous_selected_event(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)
    venue = ask(svc, "Где оно проходит?")
    assert "Башкир" in venue.message and venue.result_ids == ids(9)
    assert state(svc).selected_event_id == ids(9)[0]


def test_venue_clarifies_when_multiple_cards_were_shown(tmp_path):
    svc, _ = service(tmp_path)
    assert len(ask(svc, "Покажи мероприятие").result_ids) == 5
    reply = ask(svc, "Где оно проходит?")
    assert reply.result_ids == [] and "каком мероприятии" in reply.message
    assert state(svc).selected_event_id is None


def test_more_pages_broad_search_without_repeating_cards(tmp_path):
    svc, _ = service(tmp_path)
    first = ask(svc, "Покажи мероприятия")
    second = ask(svc, "А ещё?")
    third = ask(svc, "А ещё?")
    fourth = ask(svc, "А ещё?")
    pages = [first.result_ids, second.result_ids, third.result_ids, fourth.result_ids]
    assert [len(page) for page in pages] == [5, 5, 5, 3]
    assert len(set().union(*map(set, pages))) == 18
    assert ask(svc, "А ещё?").result_ids == []
    assert len(state(svc).shown_event_ids) == 18


def test_other_day_accepts_explicit_date_after_clarification(tmp_path):
    svc, _ = service(tmp_path)
    ask(svc, "Бесплатные выставки в Уфе в воскресенье")
    ask(svc, "В другой день")
    reply = ask(svc, "16 ноября")
    assert reply.result_ids == ids(5, 6)
    assert state(svc).active_constraints.date_from.isoformat() == "2026-11-16"


def test_two_refinements_retain_constraints(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Бесплатные выставки в Уфе").result_ids == ids(5, 6)
    assert ask(svc, "В воскресенье").result_ids == ids(5, 6)
    reply = ask(svc, "Для ребёнка 8 лет")
    assert all(card.city == "Уфа" and card.category == "выставка" and card.is_free
               and card.age_limit <= 8 for card in reply.event_cards)
    filters = state(svc).active_constraints
    # The existing normalizer stores the highest permitted age rating (6+) for an 8-year-old.
    assert (filters.city, filters.category, filters.is_free_only, filters.max_age_limit) == ("Уфа", "выставка", True, 6)
    assert filters.date_from is not None


def test_empty_result_then_refinement_keeps_topic(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)
    assert ask(svc, "Подешевле").result_ids == []
    assert ask(svc, "А ещё?").result_ids == []
    assert state(svc).active_constraints.city == "Уфа"
    assert state(svc).active_constraints.max_price == 699


def test_independent_sessions(tmp_path):
    svc, _ = service(tmp_path)
    assert ask(svc, "Концерты в Уфе", "a").result_ids == ids(9)
    assert ask(svc, "Экскурсии в Стерлитамаке", "b").result_ids == ids(15)
    assert ask(svc, "Подешевле", "a").result_ids == []
    assert state(svc, "b").active_constraints.max_price is None
    assert state(svc, "b").shown_event_ids == ids(15)


def test_state_survives_agent_service_recreation(tmp_path):
    svc, settings = service(tmp_path)
    assert ask(svc, "Концерты в Уфе").result_ids == ids(9)
    svc.database.close()
    resumed = AgentService(Database(settings.database_url), settings)
    assert ask(resumed, "Где оно проходит?").result_ids == ids(9)
    assert state(resumed).selected_event_id == ids(9)[0]
    assert state(resumed).shown_event_ids == ids(9)
    resumed.database.close()


def test_weekend_followup_searches_dates_with_saved_filters(tmp_path):
    svc, _ = service(tmp_path)
    ask(svc, "Концерты в Уфе")
    ask(svc, "А что подешевле?")
    reply = ask(svc, "Есть что-нибудь на выходных?")
    filters = state(svc).active_constraints
    assert svc.get_trace(reply.trace_id).tool_calls[0].name == "search_events"
    assert (filters.city, filters.category, filters.max_price) == ("Уфа", "концерт", 699)
    assert (filters.date_from.isoformat(), filters.date_to.isoformat()) == ("2026-11-14", "2026-11-15")


def test_price_followup_gets_verified_event_or_clarifies_ambiguity(tmp_path):
    svc, _ = service(tmp_path)
    ask(svc, "Концерты в Уфе")
    price = ask(svc, "А сколько это стоит?")
    assert svc.get_trace(price.trace_id).tool_calls[0].name == "get_event"
    assert "700" in price.message and price.result_ids == ids(9)
    similar = ask(svc, "Покажи похожие мероприятия")
    assert len(similar.result_ids) > 1
    ambiguous = ask(svc, "А сколько это стоит?")
    assert svc.get_trace(ambiguous.trace_id).stop_reason == "clarification"
    assert not svc.get_trace(ambiguous.trace_id).tool_calls


def test_city_switch_preserves_category_then_weekend_filters(tmp_path):
    svc, _ = service(tmp_path)
    ask(svc, "Найди спектакли в Салавате")
    switched = ask(svc, "А в Уфе?")
    assert svc.get_trace(switched.trace_id).tool_calls[0].name == "search_events"
    assert (state(svc).active_constraints.city, state(svc).active_constraints.category) == ("Уфа", "спектакль")
    weekend = ask(svc, "На выходных")
    filters = state(svc).active_constraints
    assert svc.get_trace(weekend.trace_id).tool_calls[0].name == "search_events"
    assert (filters.city, filters.category) == ("Уфа", "спектакль")
    assert (filters.date_from.isoformat(), filters.date_to.isoformat()) == ("2026-11-14", "2026-11-15")


def test_city_switch_after_empty_search_keeps_excursion_topic(tmp_path):
    svc, _ = service(tmp_path)
    ask(svc, "Какие экскурсии есть в Стерлитамаке?")
    ask(svc, "А что подешевле?")
    changed = ask(svc, "Покажи в Уфе")
    assert svc.get_trace(changed.trace_id).tool_calls[0].name == "search_events"
    assert (state(svc).active_constraints.city, state(svc).active_constraints.category,
            state(svc).active_constraints.max_price) == ("Уфа", "экскурсия", 149)
