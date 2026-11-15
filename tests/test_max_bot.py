from dataclasses import asdict

from src.bot.max.adapter import max_keyboard, normalize_update
from src.bot.max.client import MaxClient, max_ssl_context
from src.bot.max.webhook import MaxWebhookRunner
from src.bot.router import BotResponse, BotRouter
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


def test_normalize_max_audio_attachment():
    message = normalize_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 7}, "recipient": {"chat_id": 8},
        "body": {"attachments": [{"type": "audio", "payload": {"url": "https://cdn.max.ru/a.webm"}}]},
    }})
    assert message["kind"] == "audio" and message["user_id"] == 7
    assert message["audio_url"] == "https://cdn.max.ru/a.webm"


def test_max_media_hosts_follow_official_upload_hosts_and_remain_exact():
    from src.bot.max.adapter import _audio_url_allowed
    assert _audio_url_allowed("https://omu.okcdn.ru/media/a.m4a")
    assert _audio_url_allowed("https://fu.oneme.ru/api/a.mp3")
    assert not _audio_url_allowed("https://attacker.max.ru/a.webm")


def test_max_audio_transcribes_and_routes_original_user(db, monkeypatch):
    import asyncio
    import httpx
    import src.bot.max.adapter as adapter_module

    router = BotRouter(db, Settings(), platform="max")
    seen = {}
    async def transcribe(audio, filename, mime, settings):
        seen["stt"] = (audio, filename, mime, settings)
        return "Ищу выставки"

    monkeypatch.setattr(adapter_module, "transcribe_audio", transcribe)
    monkeypatch.setattr(router, "handle_message", lambda uid, text: (seen.update(route=(uid, text)) or BotResponse("ответ")))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"audio"))) as http:
            return await adapter_module.MaxAdapter(router).dispatch_audio({"update_type": "message_created", "message": {
                "sender": {"user_id": 71}, "recipient": {"chat_id": 81}, "body": {"attachments": [
                    {"type": "audio", "payload": {"url": "https://cdn.max.ru/a.webm"}}
                ]},
            }}, http)
    incoming, transcript, response = asyncio.run(run())
    assert seen["route"] == ("71", "Ищу выставки")
    assert seen["stt"][0] == b"audio"
    assert incoming["chat_id"] == 81 and transcript == "Ищу выставки" and response.text == "ответ"


def test_max_audio_rejects_invalid_url_empty_and_oversized_before_stt(db, monkeypatch):
    import asyncio
    import httpx
    import src.bot.max.adapter as adapter_module
    from src.bot.max.adapter import MaxAudioError

    router = BotRouter(db, Settings(), platform="max")
    adapter = adapter_module.MaxAdapter(router)
    monkeypatch.setattr(adapter_module, "transcribe_audio", lambda *args: (_ for _ in ()).throw(AssertionError("STT must not run")))
    base = {"update_type": "message_created", "message": {"sender": {"user_id": 1},
            "recipient": {"chat_id": 2}, "body": {"attachments": [{"type": "audio", "payload": {}}]}}}

    async def run(url, payload=b"", headers=None):
        update = {**base, "message": {**base["message"], "body": {"attachments": [{"type": "audio", "payload": {"url": url}}]}}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, content=payload))) as http:
            return await adapter.dispatch_audio(update, http)
    async def cases():
        for url in ("http://cdn.max.ru/a", "https://127.0.0.1/a", "https://example.org/a", "https://attacker.max.ru/a"):
            try: await run(url)
            except MaxAudioError: pass
            else: raise AssertionError("bad URL accepted")
        try: await run("https://cdn.max.ru/a")
        except MaxAudioError as exc: assert "пуста" in str(exc)
        else: raise AssertionError("empty audio accepted")
        try: await run("https://cdn.max.ru/a", b"x", {"content-length": str(20 * 1024 * 1024 + 1)})
        except MaxAudioError as exc: assert "20 МБ" in str(exc)
        else: raise AssertionError("oversized audio accepted")
    asyncio.run(cases())


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


def test_callback_payload_is_not_written_to_logs(db, monkeypatch):
    import src.bot.router as router_module

    logged = []
    monkeypatch.setattr(router_module, "log_event", lambda *args, **kwargs: logged.append(kwargs))
    router = BotRouter(db, Settings(), platform="max")
    router.handle_callback("user-1", "private-payload-with-user-data")
    callback_log = next(fields for fields in logged if "callback_length" in fields)
    assert callback_log["callback_length"] == len("private-payload-with-user-data")
    assert "callback" not in callback_log


def test_webhook_deduplication_key_and_configuration(db):
    runner = MaxWebhookRunner("token", "https://bots.example.org/webhook", "s" * 20,
                              BotRouter(db, Settings(), platform="max"))
    update = {"update_type": "message_created", "message": {"body": {"mid": "m1"}}}
    key = runner._event_key(update)
    assert key == runner._event_key(update)
    runner._remember(key)
    assert key in runner._seen

