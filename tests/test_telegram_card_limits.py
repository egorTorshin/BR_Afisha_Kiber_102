from src.bot.formatter import TELEGRAM_CARD_MAX_LENGTH, format_event_card
from src.models.schemas import EventRecord
from tests.test_backend import event


def payload_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def make_event(description: str, **changes) -> EventRecord:
    values = event(description=description, **changes)
    defaults = {
        "id": "event-1", "normalized_title": values["title"].lower(), "tags": [],
        "date_end": None, "duration_min": None, "lat": None, "lon": None,
        "price_min": None, "price_max": None, "is_free": False, "age_limit": 0,
        "language": None, "org_link": None, "source_type": "web", "source_hash": "hash",
        "verified_at": "2026-11-15", "confidence": 1.0, "status": "active",
        "dedup_key": "event-1", "is_disputed": False, "dispute_reason": None,
        "created_at": "2026-11-15", "updated_at": "2026-11-15",
    }
    for key, value in defaults.items():
        values.setdefault(key, value)
    return EventRecord(**values)


def test_normal_telegram_card_keeps_description_and_links():
    card = format_event_card(make_event("Обычное описание мероприятия."))
    assert "Обычное описание мероприятия." in card
    assert "https://example.com/one" in card
    assert payload_length(card) <= TELEGRAM_CARD_MAX_LENGTH


def test_long_telegram_card_truncates_description_and_preserves_required_fields():
    card = format_event_card(make_event(
        "Подробное описание. " * 500,
        title="Название события",
        org_link="https://tickets.example/event",
        source_url="https://source.example/event",
    ))
    assert payload_length(card) <= TELEGRAM_CARD_MAX_LENGTH
    for value in (
        "Название события", "20 ноября 2026", "18:00", "Уфа", "Большой зал",
        "ул. Ленина, 1", "Стоимость", "Возрастное ограничение", "https://tickets.example/event",
        "https://source.example/event",
    ):
        assert value in card
    assert card.count("Подробное описание.") < 500
    assert card.endswith("https://source.example/event")


def test_extreme_unicode_description_and_escaped_links_fit_serialized_limit():
    card = format_event_card(make_event(
        "🎭 演劇 & <музыка> — ёжик 🧑🏽‍🎤 " * 10000,
        org_link="https://tickets.example/?a=1&b=2",
        source_url="https://source.example/?x=<y>&z=🎟",
    ))
    assert payload_length(card) <= TELEGRAM_CARD_MAX_LENGTH
    assert "Вечер музыки" in card
    assert "https://tickets.example/?a=1&amp;b=2" in card
    assert "https://source.example/?x=&lt;y&gt;&amp;z=🎟" in card
    assert "…\n🎟" in card
