"""Isolated acceptance regressions for Telegram and MAX search UX (stage 3.2)."""

from pathlib import Path

import pytest

from src.bot.max.adapter import MaxAdapter, max_keyboard
from src.bot.router import BotRouter
from src.config import Settings
from src.services.import_service import ImportService
from src.services.normalizer import resolve_date_preset
from src.services.search_service import SearchService
from src.models.schemas import SearchFilters
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


def _insert_catalog(db):
    service = ImportService(db)
    records = [
        event(title=f"Фестиваль науки {i}", category="фестиваль", city="Уфа",
              venue="Дом науки", address="ул. Науки, 7", description="Открытая программа для всей семьи и детей",
              date_start=f"2026-11-{20 + i:02d}T14:30:00", price_min=300, price_max=500,
              age_limit=6, org_link="https://tickets.example/science",
              source_url=f"https://source.example/science-{i}")
        for i in range(7)
    ]
    ids = [service.create_event_manual(row)[1].id for row in records]
    service.create_event_manual(event(title="Черновик события", status="черновик", source_url="https://source.example/draft"))
    service.create_event_manual(event(title="Отменённое событие", status="отменено", source_url="https://source.example/cancelled"))
    service.create_event_manual(event(title="Мероприятие в Стерлитамаке", city="Стерлитамак",
                                      source_url="https://source.example/other-city"))
    return ids


def _dispatch_callback(router, platform, payload):
    if platform == "telegram":
        return router.handle_callback("u32", payload)
    dispatched = MaxAdapter(router).dispatch({
        "update_type": "message_callback",
        "callback": {"user": {"user_id": "u32"}, "payload": payload, "callback_id": "cb-32"},
        "message": {"recipient": {"chat_id": 32}, "body": {"mid": "m-32"}},
    })
    assert dispatched is not None
    return dispatched[1]


def test_button_filters_dates_custom_date_price_and_state_are_isolated(db):
    _insert_catalog(db)
    for platform in ("telegram", "max"):
        router = BotRouter(db, Settings(reference_date_str="2026-11-14", rate_limit_per_minute=1000), platform=platform)
        for preset in ("today", "tomorrow", "weekend", "week"):
            _dispatch_callback(router, platform, f"date:{preset}")
            filters = router.get_user_filters("u32" if platform == "telegram" else "max:u32")
            assert filters.date_preset == preset
            assert (filters.date_from, filters.date_to) == resolve_date_preset(preset, router.settings.get_reference_date())
        _dispatch_callback(router, platform, "date:custom")
        response = router.handle_message("u32", "22.11.2026")
        filters = router.get_user_filters("u32" if platform == "telegram" else "max:u32")
        assert filters.date_preset is None and filters.date_from == "2026-11-22T00:00:00"
        assert "Фестиваль науки" in response.text
        _dispatch_callback(router, platform, "city:Уфа")
        _dispatch_callback(router, platform, "cat:фестиваль")
        _dispatch_callback(router, platform, "price:500")
        assert (filters.city, filters.category, filters.max_price) == ("Уфа", "фестиваль", 500)
        assert filters.date_from == "2026-11-22T00:00:00"


def test_same_catalog_results_cards_and_pagination_match_on_both_platforms(db):
    ids = _insert_catalog(db)
    settings = Settings(reference_date_str="2026-11-14", rate_limit_per_minute=1000)
    telegram = BotRouter(db, settings)
    max_bot = BotRouter(db, settings, platform="max")
    telegram.get_user_filters("u32").limit = 3
    max_bot.get_user_filters("max:u32").limit = 3
    telegram_first = telegram.handle_message("u32", "/search")
    max_first = max_bot.handle_message("u32", "/search")
    assert telegram_first.text == max_first.text
    assert "Черновик события" not in telegram_first.text and "Отменённое событие" not in telegram_first.text
    assert max_keyboard(max_first.reply_markup)
    next_page = _dispatch_callback(telegram, "telegram", "page:3")
    max_next_page = _dispatch_callback(max_bot, "max", "page:3")
    assert next_page.text == max_next_page.text
    assert "4." in next_page.text
    card = _dispatch_callback(telegram, "telegram", f"card:{ids[2]}")
    max_card = _dispatch_callback(max_bot, "max", f"card:{ids[2]}")
    assert card.text == max_card.text
    for fact in ("Фестиваль науки", "Фестиваль", "22 ноября 2026", "Уфа", "Дом науки",
                 "ул. Науки, 7", "Открытая программа для всей семьи", "от 300 до 500 ₽", "6+",
                 "ручной ввод", "tickets.example/science", "source.example/science-2"):
        assert fact in card.text, fact
    assert max_keyboard(max_card.reply_markup)
    for response in (telegram_first, max_first, next_page, max_next_page, card, max_card):
        assert len(response.text) <= 4096
        for row in (response.reply_markup or {}).get("inline_keyboard", []):
            for button in row:
                assert len(button.get("text", "")) <= 64
                if "callback_data" in button:
                    assert len(button["callback_data"].encode("utf-8")) <= 64


def test_free_text_and_repeated_search_stay_in_results_flow(db):
    _insert_catalog(db)
    for platform in ("telegram", "max"):
        router = BotRouter(db, Settings(reference_date_str="2026-11-14", ai_mode="no-ai"), platform=platform)
        response = router.handle_message("u32", "Фестивали в Уфе до 500 рублей")
        assert "Фестиваль науки" in response.text
        again = router.handle_message("u32", "Фестивали в Уфе")
        assert "Фестиваль науки" in again.text


def test_empty_results_explain_and_offer_relaxing_filters_on_both_platforms(db):
    _insert_catalog(db)
    for platform in ("telegram", "max"):
        router = BotRouter(db, Settings(reference_date_str="2026-11-14"), platform=platform)
        filters = router.get_user_filters("u32" if platform == "telegram" else "max:u32")
        filters.city, filters.category = "Уфа", "фестиваль"
        filters.date_from, filters.date_to, filters.date_preset = "2099-01-01", "2099-01-01", "custom"
        response = _dispatch_callback(router, platform, "search:run")
        assert "не найдено" in response.text.lower() or "нет" in response.text.lower()
        payloads = [b.get("callback_data") for row in response.reply_markup["inline_keyboard"] for b in row]
        assert "date:all" in payloads and "menu:filters" in payloads


def test_filter_callbacks_and_platform_button_limits(db):
    router = BotRouter(db, Settings())
    response = router.handle_callback("u32", "page:not-a-number")
    assert "inline_keyboard" in response.reply_markup
    oversized = {"inline_keyboard": [[{"text": "x" * 200, "callback_data": "valid"},
                                      {"text": "x", "callback_data": "x" * 1025}] * 5] * 31}
    translated = max_keyboard(oversized)
    assert translated is not None and len(translated) <= 30
    assert all(len(row) <= 7 for row in translated)
    assert all(len(button["text"]) <= 128 for row in translated for button in row)
    assert all(len(button.get("payload", "")) <= 1024 for row in translated for button in row)


def test_telegram_card_with_long_catalog_description_fits_message_limit(db):
    """Telegram rejects messages longer than 4096 characters; cards must fit."""
    _, record = ImportService(db).create_event_manual(event(
        title="Длинное описание", description="Проверяемый текст. " * 300,
        source_url="https://source.example/long-card",
    ))
    response = BotRouter(db, Settings(rate_limit_per_minute=1000)).handle_callback("u32", f"card:{record.id}")
    assert len(response.text) <= 4096


def test_catalog_search_excludes_drafts_cancelled_rows_and_merged_duplicates(db):
    ids = _insert_catalog(db)
    service = SearchService(db)
    results, total, _ = service.search(SearchFilters(limit=100))
    assert total == len(results) == 8  # seven science events plus the other city
    assert len({row.id for row in results}) == len(results)
    assert not ({"Черновик события", "Отменённое событие"} & {row.title for row in results})
    assert len(ids) == 7
