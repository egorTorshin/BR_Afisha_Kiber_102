"""Stage 3.3 regressions for shared Telegram/MAX state and recovery paths."""

from pathlib import Path

import pytest

from src.bot.max.adapter import MaxAdapter
from src.bot.router import BotRouter
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.import_service import ImportService
from tests.test_backend import event


@pytest.fixture
def isolated_db():
    db = Database(":memory:")
    apply_migrations(db, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield db
    finally:
        db.close()


def _max_callback(router, user_id, payload):
    return MaxAdapter(router).dispatch({
        "update_type": "message_callback",
        "callback": {"user": {"user_id": user_id}, "payload": payload, "callback_id": "cb-33"},
        "message": {"recipient": {"chat_id": 3301}, "body": {"mid": "msg-33"}},
    })[1]


@pytest.mark.parametrize("platform", ["telegram", "max"])
def test_subscriptions_are_idempotent_scoped_and_survive_router_restart(isolated_db, platform):
    settings = Settings(rate_limit_per_minute=1000)
    first = BotRouter(isolated_db, settings, platform=platform)
    user_a = "u-33" if platform == "telegram" else "max:u-33"
    user_b = "u-34" if platform == "telegram" else "max:u-34"

    if platform == "telegram":
        first.handle_callback("u-33", "sub:Уфа:концерт")
        first.handle_callback("u-33", "sub:Уфа:концерт")
        first.handle_callback("u-34", "sub:Салават:театр")
    else:
        _max_callback(first, "u-33", "sub:Уфа:концерт")
        _max_callback(first, "u-33", "sub:Уфа:концерт")
        _max_callback(first, "u-34", "sub:Салават:театр")

    assert len(first.sub_service.list_subscriptions(user_a)) == 1
    assert len(first.sub_service.list_subscriptions(user_b)) == 1
    assert first.sub_service.is_subscribed(user_a, "Уфа", "концерт")
    assert not first.sub_service.is_subscribed(user_b, "Уфа", "концерт")

    # A new handler instance reads the same persisted rows from the isolated DB.
    restarted = BotRouter(isolated_db, settings, platform=platform)
    assert len(restarted.sub_service.list_subscriptions(user_a)) == 1
    assert restarted.sub_service.is_subscribed(user_a, "Уфа", "концерт")
    assert restarted.sub_service.is_subscribed(user_b, "Салават", "театр")

    if platform == "telegram":
        restarted.handle_message("u-33", "/unsubscribe")
    else:
        restarted.handle_message("u-33", "/unsubscribe")
    assert restarted.sub_service.list_subscriptions(user_a) == []
    assert len(restarted.sub_service.list_subscriptions(user_b)) == 1


@pytest.mark.parametrize("platform", ["telegram", "max"])
def test_free_text_agent_path_returns_catalog_cards_and_persists_dialogue(isolated_db, platform):
    ImportService(isolated_db).create_event_manual(event(
        title="Проверяемая выставка", city="Уфа", category="выставка",
        source_url="https://source.example/stage33",
    ))
    settings = Settings(ai_mode="no-ai", reference_date_str="2026-11-14", rate_limit_per_minute=1000)
    router = BotRouter(isolated_db, settings, platform=platform)
    query = "Выставки в Уфе"
    response = router.handle_message("u-33", query)
    assert "Проверяемая выставка" in response.text
    assert response.reply_markup and response.reply_markup.get("inline_keyboard")

    principal = "telegram:u-33" if platform == "telegram" else "max:u-33"
    _, conversation_id = router.agent_service.get_or_create_principal(principal)
    state = router.agent_service.dialogue_repo.get(conversation_id)
    assert state is not None
    assert any(turn.content == query for turn in state.turns)

