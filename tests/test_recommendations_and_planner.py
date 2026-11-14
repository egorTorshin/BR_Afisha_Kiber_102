from datetime import date, timedelta
from itertools import combinations

from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.services.import_service import ImportService
from src.services.recommendation_service import RecommendationService, _event_similarity
from src.services.subscription_service import SubscriptionService
from src.services.user_service import UserService
from tests.test_backend import db, empty_db, event


def _add(db, title, category, start, venue, price, *, city="Уфа", tags=None,
         lat=54.730, lon=55.950, status="published", duration=60):
    _, item = ImportService(db).create_event_manual(event(
        title=title, category=category, date_start=start, duration_min=duration,
        city=city, venue=venue, address=f"ул. {venue}, 1", tags=tags or [],
        lat=lat, lon=lon, price_min=price, price_max=price, is_free=price == 0,
        status=status, source_url=f"https://example.com/{title.replace(' ', '-')}"))
    return item


def _recommend_catalog(db):
    items = {}
    for number in range(5):
        items[f"concert_{number}"] = _add(
            db, f"Джазовый концерт {number}", "концерт", f"2026-11-14T{10 + number}:00:00",
            "Концертный зал", 300, tags=["джаз", "музыка"])
    items["theatre"] = _add(db, "Семейный спектакль", "спектакль", "2026-11-14T11:30:00",
                            "Драмтеатр", 400, tags=["семья", "театр"], lat=54.735)
    items["museum"] = _add(db, "Выставка живописи", "выставка", "2026-11-14T12:30:00",
                           "Музей", 0, tags=["живопись"], lat=54.738)
    items["tour"] = _add(db, "Экскурсия по городу", "экскурсия", "2026-11-14T13:30:00",
                         "Площадь", 150, tags=["история"], lat=54.740)
    items["draft"] = _add(db, "Скрытое шоу", "концерт", "2026-11-14T16:00:00",
                          "Драмтеатр", 0, status="draft")
    return items


def _plan_catalog(db, city="Уфа"):
    return [
        _add(db, "Утренний концерт", "концерт", "2026-11-14T10:00:00", "Зал", 200, city=city),
        _add(db, "Дневная выставка", "выставка", "2026-11-14T12:00:00", "Музей", 300,
             lat=54.735, lon=55.955, city=city),
        _add(db, "Вечерний спектакль", "спектакль", "2026-11-14T14:00:00", "Театр", 400,
             lat=54.740, lon=55.960, city=city),
    ]


def test_cold_start_mmr_and_publication(db):
    items = _recommend_catalog(db)
    recs = RecommendationService(db).recommend_for_user("new-user", limit=5, ref_date=date(2026, 11, 14))
    assert len(recs) == 5
    assert len({rec.event.venue for rec in recs}) >= 3
    assert all(0 <= rec.score <= 1 and rec.match_reason for rec in recs)
    assert items["draft"].id not in {rec.event.id for rec in recs}
    assert all(rec.event.status == "published" for rec in recs)
    service = RecommendationService(db)
    all_recs = service.recommend_for_user("new-user", limit=20, ref_date=date(2026, 11, 14))
    remaining = sorted(all_recs, key=lambda item: (-item.score, item.event.date_start, item.event.id))
    expected = []
    for _ in range(5):
        best = max(remaining, key=lambda item: (
            0.75 * item.score - 0.25 * max(
                (_event_similarity(service._features(item.event)[0], service._features(chosen.event)[0])
                 for chosen in expected), default=0.0), item.score, -remaining.index(item)))
        expected.append(best)
        remaining.remove(best)
    assert [item.event.id for item in recs] == [item.event.id for item in expected]


def test_views_and_subscription_raise_relevance(db):
    items = _recommend_catalog(db)
    service = RecommendationService(db)
    user = UserService(db).telegram_user("123")
    baseline = {rec.event.id: rec.score for rec in service.recommend_for_user("123", limit=20, ref_date=date(2026, 11, 14))}
    UserService(db).record_view(user["id"], items["concert_0"].id, "telegram")
    SubscriptionService(db).subscribe("123", "Уфа", "концерт")
    recs = service.recommend_for_user("123", limit=20, ref_date=date(2026, 11, 14))
    by_id = {rec.event.id: rec for rec in recs}
    assert items["concert_0"].id not in by_id
    assert by_id[items["concert_1"].id].score > baseline[items["concert_1"].id]
    assert "подписке" in by_id[items["concert_1"].id].match_reason.lower()
    assert service.recommend_for_user(user["id"], limit=20, ref_date=date(2026, 11, 14))
    UserService(db).record_view(user["id"], items["theatre"].id, "web")
    assert items["theatre"].id not in {
        rec.event.id for rec in service.recommend_for_user("123", limit=20, ref_date=date(2026, 11, 14))}


def test_similar_hybrid_excludes_source_and_drafts(db):
    items = _recommend_catalog(db)
    similar = RecommendationService(db).find_similar_hybrid(items["concert_0"].id, limit=5)
    assert similar and similar[0].event.category == "концерт"
    assert items["concert_0"].id not in {item.event.id for item in similar}
    assert items["draft"].id not in {item.event.id for item in similar}
    from src.agent.tools import create_tool_registry
    from src.services.feedback_service import FeedbackService
    from src.services.search_service import SearchService
    registry = create_tool_registry(SearchService(db), SubscriptionService(db), FeedbackService(db), user_id="test")
    tool_result = registry.execute("find_similar", {"event_id": items["concert_0"].id, "limit": 5})
    assert tool_result.error is None
    assert tool_result.result_ids == [item.event.id for item in similar]


def test_weekend_plan_respects_time_travel_and_total_budget(db):
    _plan_catalog(db)
    service = RecommendationService(db)
    plan = service.build_weekend_plan(city="Уфа", date_from="2026-11-14", date_to="2026-11-14",
                                      max_budget_total=2000, party_size=2, target_count=3)
    assert len(plan.stops) == 3
    assert plan.conflict_explanation is None
    assert plan.total_price == 1800
    assert plan.total_price == sum(stop.party_price for stop in plan.stops)
    for previous, current in zip(plan.stops, plan.stops[1:]):
        assert previous.end_time + timedelta(minutes=current.travel_from_previous_min + 15) <= current.start_time
        assert current.travel_from_previous_min <= 25


def test_multiday_exhibition_end_date_is_availability_not_visit_duration(db):
    exhibition = _add(db, "Долгая выставка", "выставка", "2026-11-14T10:00:00",
                      "Музей", 0, duration=60)
    _add(db, "Дневной концерт", "концерт", "2026-11-14T12:00:00",
         "Зал", 100, lat=54.735)
    db.execute("UPDATE events SET date_end = ? WHERE id = ?",
               ("2026-11-29T18:00:00", exhibition.id))
    plan = RecommendationService(db).build_weekend_plan(
        city="Уфа", date_from="2026-11-14", date_to="2026-11-14",
        max_budget_total=200, target_count=2)
    assert len(plan.stops) == 2
    assert plan.stops[0].end_time == plan.stops[0].start_time + timedelta(minutes=60)
    assert plan.stops[0].end_time + timedelta(
        minutes=plan.stops[1].travel_from_previous_min + 15) <= plan.stops[1].start_time


def test_budget_conflict_is_exact_and_keeps_partial_route(db):
    _plan_catalog(db)
    service = RecommendationService(db)
    partial = service.build_weekend_plan(
        city="Уфа", date_from="2026-11-14", date_to="2026-11-14",
        max_budget_total=1200, party_size=2, target_count=3)
    assert len(partial.stops) == 2
    assert partial.total_price == 1000
    assert "600 ₽" in partial.conflict_explanation
    plan = service.build_weekend_plan(
        city="Уфа", date_from="2026-11-14", date_to="2026-11-14",
        max_budget_total=100, party_size=2, target_count=3)
    assert plan.stops == []
    assert "1700 ₽" in plan.conflict_explanation
    assert "1800 ₽" in plan.conflict_explanation


def test_travel_and_period_conflicts_offer_single_constraint_relaxation(db):
    items = _plan_catalog(db)
    db.execute("UPDATE events SET lat = ?, lon = ? WHERE id = ?", (54.800, 55.950, items[1].id))
    db.execute("UPDATE events SET lat = ?, lon = ? WHERE id = ?", (54.870, 55.950, items[2].id))
    service = RecommendationService(db)
    travel = service.build_weekend_plan(
        city="Уфа", date_from="2026-11-14", date_to="2026-11-14",
        max_budget_total=2000, party_size=2, max_travel_min=10, target_count=3)
    assert len(travel.stops) < 3
    assert "увеличьте допустимый переезд" in travel.conflict_explanation

    period = service.build_weekend_plan(
        city="Уфа", date_from="2026-11-13", date_to="2026-11-13",
        max_budget_total=2000, party_size=2, max_travel_min=60,
        target_count=3, ref_date=date(2026, 11, 13))
    assert period.stops == []
    assert "на 1 дн." in period.conflict_explanation
    assert "до 2026-11-14" in period.conflict_explanation


def test_csp_dynamic_program_matches_small_exhaustive_search(db):
    service = RecommendationService(db)
    for number, (hour, category, price) in enumerate([
        (9, "концерт", 100), (10, "спектакль", 200), (11, "выставка", 150),
        (12, "концерт", 250), (14, "экскурсия", 100), (16, "спектакль", 300),
    ]):
        _add(db, f"Контрольное событие {number}", category, f"2026-11-14T{hour:02d}:00:00",
             f"Площадка {number}", price, duration=60, lat=54.730 + number * 0.001)
    candidates = service._plan_candidates("Уфа", "2026-11-14", "2026-11-14", None)
    path, cost, travel, _ = service._solve_plan(candidates, 2, 3, 1000, 25)
    feasible = []
    for length in range(1, 4):
        for indices in combinations(range(len(candidates)), length):
            total_cost = sum(candidates[index][3] * 2 for index in indices)
            if total_cost > 1000:
                continue
            legs = [service._travel_minutes(candidates[left][0], candidates[right][0])
                    for left, right in zip(indices, indices[1:])]
            if any(leg > 25 or candidates[left][2] + timedelta(minutes=leg + 15) > candidates[right][1]
                   for (left, right), leg in zip(zip(indices, indices[1:]), legs)):
                continue
            key = (length, len({candidates[index][0].category for index in indices}),
                   -sum(legs), -total_cost)
            feasible.append(key)
    assert (len(path), len({candidates[index][0].category for index in path}), -travel, -cost) == max(feasible)


def test_bot_one_tap_recommendations_and_planner_grounding(db):
    _recommend_catalog(db)
    from src.bot.router import BotRouter
    settings = Settings(reference_date_str="2026-11-14", ai_mode="no-ai", rate_limit_per_minute=50)
    bot = BotRouter(db, settings)
    menu = bot.handle_message("123", "/start")
    assert any(button["callback_data"] == "preset:for_you" for row in menu.reply_markup["inline_keyboard"] for button in row)
    recs = bot.handle_callback("123", "preset:for_you")
    assert "💡" in recs.text
    assert recs.reply_markup["inline_keyboard"][0][0]["callback_data"].startswith("card:")
    assert "💡" in bot.handle_message("123", "Рекомендации").text
    assert "💡" in bot.handle_message("123", "/recommend").text


def test_agent_planner_uses_verified_catalog_ids(db):
    _plan_catalog(db)
    from src.services.agent_service import AgentService
    agent = AgentService(db, Settings(reference_date_str="2026-11-13", ai_mode="no-ai"))
    result = agent.chat("Сделай план на субботу: три события, 2000 рублей на двоих, без долгих переездов в Уфе",
                        "planner-conversation")
    trace = agent.get_trace(result.request_id)
    assert result.status == "ok"
    assert len(result.response.event_cards) == 3
    assert "Итого: 1800 ₽" in result.response.message
    assert trace and any(call.name == "search_events" for call in trace.tool_calls)
    observed = {event_id for group in trace.tool_result_ids for event_id in group}
    assert set(result.response.result_ids) <= observed


def test_bot_keeps_planner_response_in_no_ai_mode(db):
    _plan_catalog(db)
    from src.bot.router import BotRouter
    bot = BotRouter(db, Settings(reference_date_str="2026-11-13", ai_mode="no-ai"))
    response = bot.handle_message(
        "planner-user", "Сделай план на субботу: три события, 2000 рублей на двоих в Уфе")
    assert "Маршрут" in response.text
    assert "Итого: 1800 ₽" in response.text
    assert response.reply_markup["inline_keyboard"][0][0]["callback_data"].startswith("card:")


def test_recommendation_api_requires_own_session_and_plan_api_is_grounded(db):
    items = _recommend_catalog(db)
    settings = Settings(reference_date_str="2026-11-14")
    with TestClient(create_app(db, settings), raise_server_exceptions=False) as client:
        public = client.get("/api/v1/recommendations")
        assert public.status_code == 200
        assert public.json()["items"]
        assert all(item["event"]["status"] == "published" for item in public.json()["items"])
        assert client.get("/api/v1/recommendations", params={"user_id": "someone"}).status_code == 401
        user = UserService(db).telegram_user("123")
        UserService(db).record_view(user["id"], items["concert_0"].id, "web")
        token = UserService(db).create_session(user["id"])
        client.cookies.set("user_session", token)
        assert client.get("/api/v1/recommendations", params={"user_id": "someone"}).status_code == 403
        personal = client.get("/api/v1/recommendations", params={"user_id": user["id"]})
        assert personal.status_code == 200
        assert all(item["event"]["id"] != items["draft"].id for item in personal.json()["items"])
        assert all(item["event"]["id"] != items["concert_0"].id for item in personal.json()["items"])
        assert client.get("/api/v1/recommendations", params={"limit": 21}).status_code == 422

    _plan_catalog(db, city="Салават")
    with TestClient(create_app(db, settings), raise_server_exceptions=False) as client:
        result = client.post("/api/v1/weekend-plan", json={
            "city": "Салават", "date_from": "2026-11-14", "date_to": "2026-11-14",
            "max_budget_total": 2000, "party_size": 2, "target_count": 3,
        })
        assert result.status_code == 200, result.text
        assert len(result.json()["stops"]) == 3
        assert result.json()["total_price"] == 1800
