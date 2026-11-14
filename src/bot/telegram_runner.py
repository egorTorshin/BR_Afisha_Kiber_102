import asyncio
import logging
from typing import Any, Dict, Optional
import httpx
import time
from src.utils.metrics import BOT_REQUESTS, BOT_LATENCY
from src.bot.router import BotResponse, BotRouter
from src.utils.logger import get_logger, log_event, request_id_ctx, new_request_id


class TelegramPollingRunner:
    """
    Асинхронный поллинг Telegram Bot API:
    - Принудительный IPv4 (local_address='0.0.0.0') для предотвращения таймаутов IPv6 в Docker-сети;
    - Режим 'одного окна' (Single-Window UI): при нажатии инлайн-кнопок сообщение редактируется на месте
      (editMessageText), а при вводе нового текста предыдущее сообщение бота автоматически удаляется (deleteMessage);
    - Мгновенное подтверждение нажатия кнопки (answerCallbackQuery) до обращения к БД;
    - Изоляция ошибок отдельных апдейтов от основного цикла getUpdates.
    """

    def __init__(self, token: str, router: BotRouter, log_level: str = "INFO") -> None:
        self.token = token
        self.router = router
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.file_url = f"https://api.telegram.org/file/bot{token}"
        self.logger = get_logger("afisha_rb.poller", log_level)
        self._offset = 0
        # Храним message_id последнего активного сообщения бота в каждом чате для авто-очистки
        self._last_bot_message: Dict[int, int] = {}

    def _create_http_client(self) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(
            local_address="0.0.0.0",
            retries=3,
        )
        timeout = httpx.Timeout(
            connect=10.0,
            read=35.0,
            write=15.0,
            pool=10.0,
        )
        limits = httpx.Limits(
            max_keepalive_connections=10,
            max_connections=20,
            keepalive_expiry=25.0,
        )
        return httpx.AsyncClient(transport=transport, timeout=timeout, limits=limits)

    async def _setup_bot_commands(self, client: httpx.AsyncClient) -> None:
        try:
            await client.post(
                f"{self.base_url}/deleteWebhook",
                json={"drop_pending_updates": False},
            )
            commands = [
                {"command": "start", "description": "🏠 Начать работу с ботом"},
            ]
            await client.post(f"{self.base_url}/setMyCommands", json={"commands": commands})
            me_resp = await client.get(f"{self.base_url}/getMe")
            me_data = me_resp.json()
            if me_data.get("ok"):
                bot_username = me_data["result"].get("username", "unknown")
                log_event(self.logger, logging.INFO, "Telegram bot connected and ready", bot_username=bot_username)
        except Exception as exc:
            log_event(
                self.logger,
                logging.WARNING,
                "Could not initialize bot metadata on startup",
                error_type=type(exc).__name__,
            )

    async def _safe_delete_message(self, client: httpx.AsyncClient, chat_id: int, message_id: int) -> None:
        try:
            await client.post(
                f"{self.base_url}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message_id},
                timeout=5.0,
            )
        except Exception:
            pass

    async def send_new_screen(
        self,
        client: httpx.AsyncClient,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
        delete_previous_bot_msg: bool = True,
    ) -> Optional[int]:
        old_msg_id = self._last_bot_message.get(chat_id)
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
        data = resp.json()
        new_msg_id = None
        if data.get("ok") and "result" in data:
            new_msg_id = int(data["result"]["message_id"])
            self._last_bot_message[chat_id] = new_msg_id

        if delete_previous_bot_msg and old_msg_id and old_msg_id != new_msg_id:
            await self._safe_delete_message(client, chat_id, old_msg_id)

        return new_msg_id

    async def edit_or_replace_screen(
        self,
        client: httpx.AsyncClient,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Редактирует существующее сообщение на месте (editMessageText). Если сообщение устарело — заменяет его."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        resp = await client.post(f"{self.base_url}/editMessageText", json=payload)
        data = resp.json()
        if data.get("ok"):
            self._last_bot_message[chat_id] = message_id
            return

        desc = str(data.get("description", "")).lower()
        if "message is not modified" in desc:
            self._last_bot_message[chat_id] = message_id
            return

        # Если редактирование невозможно (например, сообщение было удалено), отправляем новое и чистим старое
        self._last_bot_message[chat_id] = message_id
        await self.send_new_screen(client, chat_id, text, reply_markup, parse_mode, delete_previous_bot_msg=True)

    async def _handle_update(self, client: httpx.AsyncClient, upd: Dict[str, Any]) -> None:
        started = time.perf_counter()
        outcome = "success"
        ctx_token = request_id_ctx.set("system")
        new_request_id()
        try:
            if "callback_query" in upd:
                cq = upd["callback_query"]
                cq_id = cq["id"]
                msg = cq.get("message") or {}
                chat_id = msg.get("chat", {}).get("id")
                message_id = msg.get("message_id")
                user_id = str(cq.get("from", {}).get("id", chat_id))
                data = cq.get("data", "")

                # Мгновенно гасим спиннер загрузки на кнопке в Telegram
                try:
                    await client.post(
                        f"{self.base_url}/answerCallbackQuery",
                        json={"callback_query_id": cq_id},
                        timeout=5.0,
                    )
                except Exception:
                    pass

                bot_resp = self.router.handle_callback(user_id, data)
                if chat_id is not None and message_id is not None:
                    # Reply keyboards (contact/location request) cannot be attached while
                    # editing an inline message, so send a fresh prompt in this one case.
                    if bot_resp.reply_markup and ("keyboard" in bot_resp.reply_markup or "remove_keyboard" in bot_resp.reply_markup):
                        await self.send_new_screen(client, int(chat_id), bot_resp.text, bot_resp.reply_markup,
                                                   bot_resp.parse_mode, delete_previous_bot_msg=False)
                    else:
                        await self.edit_or_replace_screen(
                            client,
                            chat_id=int(chat_id),
                            message_id=int(message_id),
                            text=bot_resp.text,
                            reply_markup=bot_resp.reply_markup,
                            parse_mode=bot_resp.parse_mode,
                        )
                return

            if "message" in upd:
                msg = upd["message"]
                chat_id = int(msg["chat"]["id"])
                incoming_message_id = int(msg["message_id"])
                user_id = str(msg.get("from", {}).get("id", chat_id))

                if "document" in msg:
                    doc = msg["document"]
                    file_name = doc.get("file_name", "import.csv")
                    max_bytes = self.router.import_service.max_import_bytes
                    if not self.router.is_admin(user_id):
                        bot_resp = self.router.handle_document_upload(user_id, file_name, "")
                    elif int(doc.get("file_size") or 0) > max_bytes:
                        bot_resp = BotResponse(text="❌ Файл импорта слишком большой.")
                    else:
                        f_resp = await client.get(f"{self.base_url}/getFile", params={"file_id": doc["file_id"]})
                        f_resp.raise_for_status()
                        f_data = f_resp.json()
                        if not f_data.get("ok"):
                            raise ValueError("Telegram не вернул путь к файлу")
                        file_path = f_data["result"]["file_path"]
                        content_bytes = bytearray()
                        async with client.stream("GET", f"{self.file_url}/{file_path}") as download:
                            download.raise_for_status()
                            async for chunk in download.aiter_bytes():
                                content_bytes.extend(chunk)
                                if len(content_bytes) > max_bytes:
                                    break
                        if len(content_bytes) > max_bytes:
                            bot_resp = BotResponse(text="❌ Файл импорта слишком большой.")
                        else:
                            bot_resp = self.router.handle_document_upload(
                                user_id, file_name, content_bytes.decode("utf-8-sig")
                            )
                    await self.send_new_screen(
                        client, chat_id, bot_resp.text, bot_resp.reply_markup,
                        bot_resp.parse_mode, delete_previous_bot_msg=False,
                    )
                    return

                if "contact" in msg:
                    bot_resp = BotResponse(text="Для поиска номер телефона не нужен. Напишите запрос или откройте /start.")
                    await self.send_new_screen(client, chat_id, bot_resp.text, bot_resp.reply_markup,
                                               bot_resp.parse_mode, delete_previous_bot_msg=True)
                    await self._safe_delete_message(client, chat_id, incoming_message_id)
                    return

                if "location" in msg:
                    location = msg["location"]
                    bot_resp = self.router.handle_location(user_id, float(location["latitude"]), float(location["longitude"]))
                    await self.send_new_screen(client, chat_id, bot_resp.text, bot_resp.reply_markup,
                                               bot_resp.parse_mode, delete_previous_bot_msg=True)
                    await self._safe_delete_message(client, chat_id, incoming_message_id)
                    return

                if "voice" in msg:
                    bot_resp = BotResponse(text=("🎙 Голосовой запрос получен. Распознавание речи подключается через внешний "
                                                 "speech-to-text сервис; пока отправьте тот же запрос текстом."))
                    await self.send_new_screen(client, chat_id, bot_resp.text, bot_resp.reply_markup,
                                               bot_resp.parse_mode, delete_previous_bot_msg=True)
                    await self._safe_delete_message(client, chat_id, incoming_message_id)
                    return

                text = msg.get("text", "")
                bot_resp = self.router.handle_message(user_id, text)
                await self.send_new_screen(
                    client,
                    chat_id,
                    bot_resp.text,
                    bot_resp.reply_markup,
                    bot_resp.parse_mode,
                    delete_previous_bot_msg=True,
                )
                await self._safe_delete_message(client, chat_id, incoming_message_id)
        except Exception as exc:
            outcome = "error"
            log_event(
                self.logger,
                logging.ERROR,
                "Update processing failed",
                update_id=upd.get("update_id"),
                error_type=type(exc).__name__,
            )
        finally:
            BOT_REQUESTS.labels("telegram", outcome).inc()
            BOT_LATENCY.observe(time.perf_counter() - started)
            request_id_ctx.reset(ctx_token)

    async def run_forever(self) -> None:
        log_event(self.logger, logging.INFO, "Starting Telegram Bot polling loop (IPv4 transport, single-window UI)")
        async with self._create_http_client() as client:
            await self._setup_bot_commands(client)
            while True:
                try:
                    resp = await client.get(
                        f"{self.base_url}/getUpdates",
                        params={
                            "offset": self._offset,
                            "timeout": 25,
                            "allowed_updates": '["message","callback_query"]',
                        },
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        log_event(
                            self.logger,
                            logging.ERROR,
                            "Telegram API returned error",
                            error_code=data.get("error_code"),
                            description=data.get("description", "unknown"),
                        )
                        await asyncio.sleep(3.0)
                        continue
                    for upd in data.get("result", []):
                        self._offset = max(self._offset, int(upd["update_id"]) + 1)
                        await self._handle_update(client, upd)
                except asyncio.CancelledError:
                    break
                except httpx.TimeoutException as exc:
                    # Таймаут long-polling или чтения сокета — штатно переподключаемся без задержки
                    log_event(
                        self.logger,
                        logging.WARNING,
                        "Polling network timeout, reconnecting immediately",
                        error_type=type(exc).__name__,
                    )
                    await asyncio.sleep(0.5)
                except Exception as exc:
                    log_event(
                        self.logger,
                        logging.ERROR,
                        "Polling error",
                        error_type=type(exc).__name__,
                    )
                    await asyncio.sleep(2.0)
