"""Isolated command and navigation regressions for stage 3.1."""

from src.bot.max.adapter import MaxAdapter, max_keyboard
from src.bot.router import BotResponse, BotRouter
from src.config import Settings
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.bot.telegram_runner import TelegramPollingRunner


@pytest.fixture
def db(tmp_path):
    database = Database(":memory:")
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield database
    finally:
        database.close()


def _payloads(markup):
    return [button["callback_data"] for row in (markup or {}).get("inline_keyboard", [])
            for button in row if "callback_data" in button]


@pytest.mark.asyncio
async def test_telegram_registers_start_help_and_cancel_commands():
    runner = TelegramPollingRunner("test-token", router=None)
    client = AsyncMock()
    client.get.return_value = Mock(json=Mock(return_value={"ok": False}))

    await runner._setup_bot_commands(client)

    client.post.assert_any_await(
        "https://api.telegram.org/bottest-token/setMyCommands",
        json={"commands": [
            {"command": "start", "description": "🏠 Начать работу с ботом"},
            {"command": "help", "description": "ℹ️ Справка и список команд"},
            {"command": "cancel", "description": "❌ Отменить действие и сбросить фильтры"},
        ]},
    )


def test_telegram_start_help_cancel_and_unknown_command(db, monkeypatch):
    router = BotRouter(db, Settings(reference_date_str="2026-11-14"))
    start = router.handle_message("stage31-user", "/start")
    assert start.reply_markup and "inline_keyboard" in start.reply_markup
    assert _payloads(start.reply_markup)
    help_response = router.handle_message("stage31-user", "/help")
    assert "help" in help_response.text.casefold() or "команд" in help_response.text.casefold()
    router.get_user_filters("stage31-user").city = "Уфа"
    cancel = router.handle_message("stage31-user", "/cancel")
    assert router.get_user_filters("stage31-user").city is None
    assert "inline_keyboard" in cancel.reply_markup

    # Unknown commands currently follow the ordinary free-text path; exercise that
    # dispatch without invoking an external model or service.
    seen = []
    monkeypatch.setattr(router, "_handle_agent_query",
                        lambda uid, text: (seen.append((uid, text)) or BotResponse("ok")))
    assert router.handle_message("stage31-user", "/unknown").text == "ok"
    assert seen == [("stage31-user", "/unknown")]


def test_menu_buttons_and_max_navigation_dispatch_without_broken_payloads(db):
    router = BotRouter(db, Settings(reference_date_str="2026-11-14"))
    adapter = MaxAdapter(router)
    start = adapter.dispatch({"update_type": "bot_started", "user": {"user_id": 401}, "chat_id": 402})
    assert start is not None and start[1].reply_markup
    menu_payloads = _payloads(start[1].reply_markup)
    max_menu = max_keyboard(start[1].reply_markup)
    assert max_menu
    assert {b["payload"] for row in max_menu for b in row if b["type"] == "callback"} == set(menu_payloads)

    # Visit every menu destination and every produced callback once. This catches
    # stale/malformed payload regressions while keeping all state in the test DB.
    seen = set()
    pending = list(menu_payloads)
    while pending:
        payload = pending.pop(0)
        if payload in seen:
            continue
        seen.add(payload)
        dispatched = adapter.dispatch({"update_type": "message_callback",
            "callback": {"user": {"user_id": 401}, "payload": payload, "callback_id": "cb"},
            "message": {"recipient": {"chat_id": 402}, "body": {"mid": "m"}}})
        assert dispatched is not None, payload
        response = dispatched[1]
        assert isinstance(response, BotResponse), payload
        # Follow navigation links and representative results; avoid recursively
        # activating all toggles (which would toggle back and forth indefinitely).
        pending.extend(p for p in _payloads(response.reply_markup)
                       if p.startswith(("menu:", "profile:", "profile_lang:", "search:", "page:"))
                       and p not in seen)


def test_stale_and_unknown_callbacks_return_a_recoverable_screen(db):
    router = BotRouter(db, Settings())
    for payload in ("obsolete:callback", "menu:filters:unexpected", "age:not-a-number", "page:not-a-number"):
        response = router.handle_callback("stage31-user", payload)
        assert isinstance(response, BotResponse)
        assert response.reply_markup and "inline_keyboard" in response.reply_markup


def test_users_and_cancelled_custom_date_state_are_isolated(db):
    router = BotRouter(db, Settings(reference_date_str="2026-11-14"))
    router.get_user_filters("one").city = "Уфа"
    router.handle_callback("one", "date:custom")
    router.get_user_filters("two").city = "Стерлитамак"
    router.handle_message("one", "/cancel")
    assert router.get_user_filters("one").city is None
    assert router.get_user_filters("two").city == "Стерлитамак"
    assert "one" not in router._awaiting_custom_date
