from dataclasses import asdict

from src.bot.max.adapter import max_keyboard, normalize_update
from src.bot.max.client import MaxClient, max_ssl_context
from src.bot.max.webhook import MaxWebhookRunner
from src.bot.router import BotRouter
from src.config import Settings
from src.services.import_service import ImportService
from src.services.user_service import UserService
from tests.test_backend import db, empty_db, event


def test_max_keyboard_maps_callback_and_link_buttons():
    keyboard = max_keyboard({"inline_keyboard": [[
        {"text": "Открыть", "callback_data": "card:evt-1"},
        {"text": "Источник", "url": "https://example.org/event"},
    ]]})
    assert keyboard == [[
        {"type": "callback", "text": "Открыть", "payload": "card:evt-1"},
        {"type": "link", "text": "Источник", "url": "https://example.org/event"},
    ]]


def test_max_keyboard_drops_oversized_callback_payload():
    assert max_keyboard({"inline_keyboard": [[{"text": "x", "callback_data": "x" * 1025}]]}) is None


def test_max_keyboard_maps_location_request():
    assert max_keyboard({"keyboard": [[{"text": "Геолокация", "request_location": True}]]}) == [[
        {"type": "request_geo_location", "text": "Геолокация", "quick": False}
    ]]


def test_max_tls_context_keeps_certificate_verification_enabled():
    import ssl

    assert max_ssl_context().verify_mode == ssl.CERT_REQUIRED


def test_max_updates_types_are_sent_as_comma_separated_query_value():
    import asyncio
    from urllib.parse import parse_qs

    import httpx

    captured = {}

    async def handle(request):
        captured.update(parse_qs(request.url.query.decode()))
        return httpx.Response(200, json={"updates": [], "marker": 4})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = MaxClient("test-token", http)
            client.base_url = "https://api.invalid"
            await client.get_updates("3")

    asyncio.run(run())
    assert captured["types"] == ["message_created,message_callback,bot_started"]


def test_max_callback_answer_sends_replacement_message_and_keyboard():
    import asyncio

    import httpx

    captured = {}

    async def handle(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"success": True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = MaxClient("test-token", http)
            client.base_url = "https://api.invalid"
            await client.answer_callback("cb-1", "Ответ", [[
                {"type": "callback", "text": "Назад", "payload": "menu"}
            ]])

    import json

    asyncio.run(run())
    assert captured["method"] == "POST"
    assert captured["path"] == "/answers"
    assert captured["params"] == {"callback_id": "cb-1"}
    payload = json.loads(captured["body"])
    assert payload["message"]["text"] == "Ответ"
    assert payload["message"]["attachments"][0]["payload"]["buttons"][0][0]["payload"] == "menu"


def test_normalize_max_message_and_callback():
    message = normalize_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 7}, "recipient": {"chat_id": 8}, "body": {"text": "/start"}
    }})
    assert message == {"kind": "message", "chat_id": 8, "user_id": 7, "text": "/start"}
    callback = normalize_update({"update_type": "message_callback", "callback": {
        "user": {"user_id": 7}, "payload": "search:run", "callback_id": "cb1"
    }, "message": {"recipient": {"chat_id": 8}, "body": {"mid": "msg1"}}})
    assert callback["payload"] == "search:run"
    assert callback["message_id"] == "msg1"
    location = normalize_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 7}, "recipient": {"chat_id": 8},
        "body": {"attachments": [{"type": "location", "latitude": 54.7, "longitude": 55.9}]}
    }})
    assert location["kind"] == "location" and location["latitude"] == 54.7


def test_max_and_telegram_identities_are_distinct(db):
    users = UserService(db)
    telegram = users.telegram_user("555")
    max_user = users.external_user("max", "555")
    assert telegram["id"] != max_user["id"]
    assert users.telegram_user("555")["id"] == telegram["id"]
    assert users.external_user("max", "555")["id"] == max_user["id"]


def test_search_business_result_is_shared_between_telegram_and_max(db):
    ImportService(db).create_event_manual(event())
    telegram = BotRouter(db, Settings(reference_date_str="2026-11-14"))
    max_bot = BotRouter(db, Settings(reference_date_str="2026-11-14"), platform="max")
    telegram_result = telegram.handle_message("909", "/search")
    max_result = max_bot.handle_message("909", "/search")
    assert telegram_result.text == max_result.text
    assert asdict(telegram.get_user_filters("909")) == asdict(max_bot.get_user_filters("max:909"))
    assert telegram_result.reply_markup["inline_keyboard"]
    assert max_keyboard(max_result.reply_markup)


def test_webhook_deduplication_key_and_configuration(db):
    runner = MaxWebhookRunner("token", "https://bots.example.org/webhook", "s" * 20,
                              BotRouter(db, Settings(), platform="max"))
    update = {"update_type": "message_created", "message": {"body": {"mid": "m1"}}}
    key = runner._event_key(update)
    assert key == runner._event_key(update)
    runner._remember(key)
    assert key in runner._seen

