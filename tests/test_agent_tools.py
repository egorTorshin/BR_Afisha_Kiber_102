"""Tool boundary integration tests use the real application services and test database."""

import pytest
from pathlib import Path

from src.agent.tools import (
    FindSimilarOutput, GetEventOutput, GetVenueScheduleOutput, SearchEventsOutput, SubscribeOutput,
    SaveFeedbackOutput, ToolDefinition, ToolRegistry, create_tool_registry,
)
from src.services.feedback_service import FeedbackService
from src.services.import_service import ImportService
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.db.connection import Database
from src.db.migrations import apply_migrations
from tests.test_backend import event


@pytest.fixture
def db():
    database = Database(":memory:")
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def catalog(db):
    importer = ImportService(db)
    _, first = importer.create_event_manual(event(tags=["классика"], price_min=300, age_limit=6))
    _, second = importer.create_event_manual(event(
        title="Другой концерт", source_url="https://example.com/two", date_start="2026-11-21T18:00:00",
        tags=["классика"], price_min=0,
    ))
    _, elsewhere = importer.create_event_manual(event(
        title="Другой зал", venue="Малый зал", source_url="https://example.com/three",
        date_start="2026-11-22T18:00:00",
    ))
    _, draft = importer.create_event_manual(event(
        title="Черновик", status="draft", source_url="https://example.com/four",
        date_start="2026-11-23T18:00:00",
    ))
    registry = create_tool_registry(SearchService(db), SubscriptionService(db), FeedbackService(db), user_id="trusted-user")
    return registry, first, second, elsewhere, draft


def test_all_read_tools_success(catalog):
    registry, first, second, elsewhere, draft = catalog
    search = registry.execute("search_events", {
        "city": "уфа", "category": "концерт", "date_from": "2026-11-20", "date_to": "2026-11-21",
        "max_price": 300, "max_age_limit": 6, "text": "концерт", "limit": 10,
    })
    assert search.error is None
    page = SearchEventsOutput.model_validate(search.payload)
    assert page.total == 2
    assert set(search.result_ids) == {first.id, second.id}
    assert page.events[0].venue == "Большой зал"

    detail = registry.execute("get_event", {"event_id": first.id})
    assert detail.result_ids == [first.id]
    card = GetEventOutput.model_validate(detail.payload).event
    assert card.description and card.address and card.source_url
    assert card.price_min == 300
    assert "source_hash" not in detail.payload["event"]

    similar = registry.execute("find_similar", {"event_id": first.id})
    assert similar.error is None
    assert FindSimilarOutput.model_validate(similar.payload).events
    assert similar.result_ids[0] == second.id
    assert first.id not in similar.result_ids and draft.id not in similar.result_ids

    schedule = registry.execute("get_venue_schedule", {
        "venue": "Большой зал", "city": "Уфа", "date_from": "2026-11-20", "date_to": "2026-11-22",
    })
    assert schedule.error is None
    assert GetVenueScheduleOutput.model_validate(schedule.payload).total == 2
    assert set(schedule.result_ids) == {first.id, second.id}
    assert elsewhere.id not in schedule.result_ids


def test_state_changing_tools_use_trusted_identity(catalog, db):
    registry, first, *_ = catalog
    assert registry.metadata()["subscribe"].state_changing
    assert registry.metadata()["subscribe"].requires_confirmation
    assert registry.metadata()["save_feedback"].requires_confirmation
    assert not registry.metadata()["get_event"].state_changing

    sub = registry.execute("subscribe", {"city": "Уфа", "category": "концерт"}, confirmed=True)
    assert sub.error is None
    assert SubscribeOutput.model_validate(sub.payload).is_new
    assert db.fetchone("SELECT user_id FROM subscriptions WHERE id = ?", (sub.payload["subscription_id"],))["user_id"] == "trusted-user"
    assert not SubscribeOutput.model_validate(registry.execute(
        "subscribe", {"city": "Уфа", "category": "концерт"}, confirmed=True).payload).is_new

    feedback = registry.execute("save_feedback", {"event_id": first.id, "message": "Проверьте время"}, confirmed=True)
    assert feedback.error is None
    record = SaveFeedbackOutput.model_validate(feedback.payload)
    assert record.event_id == first.id and record.status == "pending"
    assert db.fetchone("SELECT message FROM feedback WHERE id = ?", (record.feedback_id,))["message"] == "Проверьте время"


@pytest.mark.parametrize("name,args", [
    ("search_events", {"limit": 0}),
    ("search_events", {"date_from": "2026-11-22", "date_to": "2026-11-20"}),
    ("search_events", {"max_price": -1}),
    ("search_events", {"is_free_only": "yes"}),
    ("get_event", {}),
    ("find_similar", {"limit": 100}),
    ("get_venue_schedule", {"venue": "Большой зал"}),
    ("subscribe", {"city": "Уфа"}),
    ("save_feedback", {"message": "  "}),
    ("save_feedback", {"message": "x", "user_id": "other-user"}),
    ("search_events", {"sql": "DROP TABLE events"}),
    ("get_event", {"event_id": "x", "shell": "rm -rf /"}),
    ("subscribe", {"city": "Уфа", "category": "концерт", "function": "unsubscribe_all"}),
])
def test_invalid_or_dangerous_arguments_are_rejected(catalog, name, args):
    registry = catalog[0]
    result = registry.execute(name, args)
    assert result.error is not None
    assert result.error.code == "invalid_arguments"
    assert result.payload is None
    assert result.result_ids == []


def test_unknown_not_found_and_empty_results(catalog):
    registry, _, _, _, draft = catalog
    assert registry.execute("run_python", {"code": "print(1)"}).error.code == "unknown_tool"
    assert registry.execute("get_event", {"event_id": "missing"}).error.code == "not_found"
    assert registry.execute("get_event", {"event_id": draft.id}).error.code == "not_found"
    assert registry.execute("find_similar", {"event_id": "missing"}).error.code == "not_found"
    assert registry.execute("save_feedback", {"event_id": "missing", "message": "x"}, confirmed=True).error.code == "not_found"
    empty = registry.execute("search_events", {"city": "Несуществующий город"},
                             context=registry.new_context())
    assert empty.error is None and empty.result_ids == [] and empty.payload["total"] == 0
    assert empty.payload["explanation"]
    schedule = registry.execute("get_venue_schedule", {
        "venue": "Большой зал", "date_from": "2026-12-01", "date_to": "2026-12-02",
    }, context=registry.new_context())
    assert schedule.error is None and schedule.payload["total"] == 0


def test_registry_is_allowlisted_and_hides_service_errors(catalog, monkeypatch):
    registry = catalog[0]
    assert set(registry.schemas()) == ToolRegistry.ALLOWLIST
    with pytest.raises(ValueError):
        ToolRegistry([ToolDefinition("run_sql", SearchEventsOutput, SearchEventsOutput, lambda _: None)])
    with pytest.raises(TypeError):
        registry.schemas()["run_sql"] = SearchEventsOutput
    monkeypatch.setattr(SearchService, "search", lambda *_: (_ for _ in ()).throw(RuntimeError("secret db detail")))
    result = registry.execute("search_events", {})
    assert result.error.code == "internal_error"
    assert "secret" not in result.model_dump_json()

