import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from src.bot.formatter import (
    format_event_card,
    format_help_message,
    format_import_report,
    format_search_results,
    format_subscriptions_list,
    format_welcome_message,
)
from src.bot.keyboards import (
    ages_keyboard,
    categories_keyboard,
    cities_keyboard,
    dates_keyboard,
    event_card_keyboard,
    main_menu_keyboard,
    prices_keyboard,
    search_results_keyboard,
    subscriptions_keyboard,
)
from src.config import Settings
from src.db.connection import Database
from src.models.schemas import SearchFilters
from src.services.import_service import ImportService
from src.services.normalizer import parse_free_text_query, parse_iso_datetime, resolve_date_preset
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.utils.logger import get_logger, log_event, new_request_id
from src.utils.rate_limiter import RateLimiter


@dataclass
class BotResponse:
    text: str
    reply_markup: Optional[Dict[str, Any]] = None
    parse_mode: str = "HTML"


class BotRouter:
    """
    Обработчик команд, текстовых сообщений, инлайн-кнопок и загрузки файлов Telegram-бота (Раздел 6.2 ТЗ).
    """

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.search_service = SearchService(db)
        self.import_service = ImportService(db)
        self.sub_service = SubscriptionService(db, retention_days=settings.subscription_retention_days)
        self.rate_limiter = RateLimiter(max_requests_per_minute=settings.rate_limit_per_minute)
        self.logger = get_logger("afisha_rb.bot", settings.log_level)

        self._user_filters: Dict[str, SearchFilters] = {}
        self._awaiting_custom_date: Dict[str, bool] = {}

    def get_user_filters(self, user_id: str) -> SearchFilters:
        uid = str(user_id)
        if uid not in self._user_filters:
            self._user_filters[uid] = SearchFilters()
        return self._user_filters[uid]

    def reset_user_filters(self, user_id: str) -> SearchFilters:
        uid = str(user_id)
        self._user_filters[uid] = SearchFilters()
        self._awaiting_custom_date.pop(uid, None)
        return self._user_filters[uid]

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in set(self.settings.admin_user_ids)

    def _build_main_menu_response(self, uid: str, prefix_notice: Optional[str] = None) -> BotResponse:
        filters = self.get_user_filters(uid)
        cnt = self.search_service.count_matching(filters)
        subs_cnt = len(self.sub_service.list_subscriptions(uid))
        welcome = format_welcome_message(filters, matching_count=cnt)
        if prefix_notice:
            welcome = f"{prefix_notice}\n\n{welcome}"
        return BotResponse(
            text=welcome,
            reply_markup=main_menu_keyboard(filters, matching_count=cnt, subs_count=subs_cnt),
        )

    def _execute_search_response(self, user_id: str) -> BotResponse:
        uid = str(user_id)
        filters = self.get_user_filters(uid)
        events, total, explanation = self.search_service.search(filters)
        is_sub = self.sub_service.is_subscribed(uid, filters.city, filters.category)
        text = format_search_results(events, total, filters, empty_explanation=explanation)
        kb = search_results_keyboard(events, total, filters, is_subscribed=is_sub)
        return BotResponse(text=text, reply_markup=kb)

    def handle_message(self, user_id: str, text: str) -> BotResponse:
        new_request_id()
        uid = str(user_id)

        if not self.rate_limiter.allow(uid):
            log_event(self.logger, logging.WARNING, "Rate limit exceeded", user_id=uid)
            return BotResponse(
                text="⏳ Вы отправляете запросы слишком часто. Пожалуйста, подождите несколько секунд и повторите попытку."
            )

        clean_text = (text or "").strip()
        log_event(
            self.logger,
            logging.INFO,
            "Incoming bot message",
            user_id=uid,
            text_length=len(clean_text),
            is_command=clean_text.startswith("/"),
        )

        if not clean_text:
            return self._build_main_menu_response(uid)

        try:
            # 1. Обработка команд /start (включая deep-link /start event_<id>)
            if clean_text.startswith("/start"):
                parts = clean_text.split(maxsplit=1)
                if len(parts) == 2 and parts[1].startswith("event_"):
                    ev_id = parts[1][len("event_"):]
                    return self._open_card(uid, ev_id)
                self.reset_user_filters(uid)
                return self._build_main_menu_response(uid)

            if clean_text.startswith("/search"):
                return self._execute_search_response(uid)

            if clean_text.startswith("/help"):
                filters = self.get_user_filters(uid)
                cnt = self.search_service.count_matching(filters)
                subs_cnt = len(self.sub_service.list_subscriptions(uid))
                return BotResponse(
                    text=format_help_message(),
                    reply_markup=main_menu_keyboard(filters, matching_count=cnt, subs_count=subs_cnt),
                )

            if clean_text.startswith("/cancel"):
                self.reset_user_filters(uid)
                return self._build_main_menu_response(
                    uid,
                    prefix_notice="🔄 <b>Фильтры поиска сброшены.</b> Вы можете ввести новый запрос или выбрать параметры ниже:",
                )

            if clean_text.startswith("/unsubscribe"):
                removed = self.sub_service.unsubscribe_all(uid)
                if removed > 0:
                    return self._build_main_menu_response(
                        uid,
                        prefix_notice=f"✅ <b>Вы отписаны от всех уведомлений</b> (удалено подписок: {removed}).",
                    )
                return self._build_main_menu_response(
                    uid,
                    prefix_notice="ℹ️ У вас не было активных подписок.",
                )

            if clean_text.startswith("/subscriptions"):
                subs = self.sub_service.list_subscriptions(uid)
                return BotResponse(
                    text=format_subscriptions_list(subs),
                    reply_markup=subscriptions_keyboard(subs),
                )

            if clean_text.startswith("/event_"):
                ev_id = clean_text[len("/event_"):].split()[0]
                return self._open_card(uid, ev_id)

            if clean_text.startswith("/admin"):
                return self._handle_admin_command(uid, clean_text)

            # 2. Если пользователь вводит выбранную дату после нажатия кнопки «Указать дату»
            if self._awaiting_custom_date.get(uid):
                self._awaiting_custom_date.pop(uid, None)
                return self._handle_custom_date_input(uid, clean_text)

            # 3. Свободный текст обрабатывается как поисковый запрос без возврата в меню (Раздел 6.2 ТЗ)
            parsed_filters = parse_free_text_query(
                clean_text,
                ref_date=self.settings.get_reference_date(),
                db=self.db,
            )
            if parsed_filters.is_empty():
                parsed_filters.query_text = clean_text

            self._user_filters[uid] = parsed_filters
            return self._execute_search_response(uid)

        except Exception as exc:
            log_event(self.logger, logging.ERROR, "Error handling message", user_id=uid, error=repr(exc))
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Не удалось разобрать запрос. Пожалуйста, уточните формулировку или выберите фильтр кнопками:",
            )

    def _handle_custom_date_input(self, uid: str, raw_date: str) -> BotResponse:
        ref_year = self.settings.get_reference_date().year
        candidate = raw_date.strip()
        if len(candidate) == 5 and candidate[2] == ".":
            candidate = f"{candidate}.{ref_year}"
        try:
            iso_dt = parse_iso_datetime(candidate)
            day_str = iso_dt[:10]
            filters = self.get_user_filters(uid)
            filters.date_preset = None
            filters.date_from = f"{day_str}T00:00:00"
            filters.date_to = f"{day_str}T23:59:59"
            filters.offset = 0
            return self._execute_search_response(uid)
        except ValueError:
            return BotResponse(
                text=(
                    "⚠️ <b>Дата не распознана.</b>\n"
                    "Пожалуйста, введите дату в формате <b>ДД.ММ.ГГГГ</b> (например, <code>14.11.2026</code>) "
                    "или выберите готовый период кнопкой ниже:"
                ),
                reply_markup=dates_keyboard(self.get_user_filters(uid).date_preset),
            )

    def _open_card(self, uid: str, event_id_or_prefix: str) -> BotResponse:
        ev = self.search_service.get_event_by_id(event_id_or_prefix, include_drafts=self.is_admin(uid))
        if not ev:
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Событие с указанным идентификатором не найдено в базе данных.",
            )
        is_sub = self.sub_service.is_subscribed(uid, ev.city, ev.category)
        return BotResponse(
            text=format_event_card(ev),
            reply_markup=event_card_keyboard(ev, is_subscribed=is_sub),
        )

    def handle_callback(self, user_id: str, callback_data: str) -> BotResponse:
        new_request_id()
        uid = str(user_id)

        if not self.rate_limiter.allow(uid):
            return BotResponse(
                text="⏳ Пожалуйста, подождите несколько секунд перед следующим действием."
            )

        log_event(self.logger, logging.INFO, "Incoming callback query", user_id=uid, callback=callback_data)
        filters = self.get_user_filters(uid)
        self._awaiting_custom_date.pop(uid, None)

        try:
            if callback_data == "menu:main":
                return self._build_main_menu_response(uid)

            if callback_data == "menu:help":
                cnt = self.search_service.count_matching(filters)
                subs_cnt = len(self.sub_service.list_subscriptions(uid))
                return BotResponse(
                    text=format_help_message(),
                    reply_markup=main_menu_keyboard(filters, matching_count=cnt, subs_count=subs_cnt),
                )

            if callback_data == "menu:reset":
                self.reset_user_filters(uid)
                return self._build_main_menu_response(
                    uid,
                    prefix_notice="🔄 <b>Все фильтры сброшены.</b> Выберите новые параметры поиска:",
                )

            if callback_data == "menu:cities":
                city_rows = self.search_service.list_cities_with_counts()
                return BotResponse(
                    text="🏙 <b>Выберите город или населённый пункт Республики Башкортостан</b>\n<i>В скобках указано количество доступных опубликованных событий:</i>",
                    reply_markup=cities_keyboard(city_rows, selected_city=filters.city),
                )

            if callback_data == "menu:categories":
                cat_rows = self.search_service.list_categories_with_counts(city=filters.city)
                cat_counts = {r["category"]: r["cnt"] for r in cat_rows}
                city_note = f" (г. {filters.city})" if filters.city else " (вся республика)"
                return BotResponse(
                    text=f"🎭 <b>Выберите категорию культурного мероприятия{city_note}:</b>",
                    reply_markup=categories_keyboard(cat_counts=cat_counts, selected_category=filters.category),
                )

            if callback_data == "menu:dates":
                return BotResponse(
                    text="📅 <b>Выберите интересующий период или укажите конкретную дату:</b>",
                    reply_markup=dates_keyboard(selected_preset=filters.date_preset),
                )

            if callback_data == "menu:prices":
                return BotResponse(
                    text="💳 <b>Выберите ограничение по стоимости билета:</b>",
                    reply_markup=prices_keyboard(filters),
                )

            if callback_data == "menu:ages":
                return BotResponse(
                    text="👶 <b>Выберите возрастное ограничение мероприятия:</b>",
                    reply_markup=ages_keyboard(selected_age=filters.max_age_limit),
                )

            if callback_data == "menu:subs":
                subs = self.sub_service.list_subscriptions(uid)
                return BotResponse(
                    text=format_subscriptions_list(subs),
                    reply_markup=subscriptions_keyboard(subs),
                )

            if callback_data == "preset:weekend":
                filters.date_preset = "weekend"
                filters.date_from, filters.date_to = resolve_date_preset("weekend", self.settings.get_reference_date())
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data == "preset:kids":
                filters.max_age_limit = 6
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data.startswith("city:"):
                val = callback_data.split(":", 1)[1]
                filters.city = None if val == "all" else val
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data.startswith("cat:"):
                val = callback_data.split(":", 1)[1]
                filters.category = None if val == "all" else val
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data.startswith("date:"):
                val = callback_data.split(":", 1)[1]
                if val == "custom":
                    self._awaiting_custom_date[uid] = True
                    return BotResponse(
                        text="📆 <b>Введите желаемую дату</b> в формате <b>ДД.ММ.ГГГГ</b> или <b>ДД.ММ</b> (например, <code>14.11.2026</code>):",
                        reply_markup=dates_keyboard(selected_preset=filters.date_preset),
                    )
                if val == "all":
                    filters.date_preset = None
                    filters.date_from = None
                    filters.date_to = None
                else:
                    filters.date_preset = val
                    filters.date_from, filters.date_to = resolve_date_preset(
                        val, self.settings.get_reference_date()
                    )
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data.startswith("price:"):
                val = callback_data.split(":", 1)[1]
                if val == "free":
                    filters.is_free_only = True
                    filters.max_price = 0
                elif val == "all":
                    filters.is_free_only = False
                    filters.max_price = None
                else:
                    filters.is_free_only = False
                    filters.max_price = int(val)
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data.startswith("age:"):
                val = callback_data.split(":", 1)[1]
                filters.max_age_limit = None if val == "all" else int(val)
                filters.offset = 0
                return self._execute_search_response(uid)

            if callback_data == "search:run":
                return self._execute_search_response(uid)

            if callback_data.startswith("page:"):
                new_offset = int(callback_data.split(":", 1)[1])
                filters.offset = max(0, new_offset)
                return self._execute_search_response(uid)

            if callback_data.startswith("card:"):
                ev_id = callback_data.split(":", 1)[1]
                return self._open_card(uid, ev_id)

            if callback_data.startswith("sub:"):
                parts = callback_data.split(":", 2)
                city_val = parts[1] if len(parts) > 1 else "Все города"
                cat_val = parts[2] if len(parts) > 2 else "Все категории"
                sub = self.sub_service.subscribe(uid, city_val, cat_val)
                status_word = "оформлена" if sub["is_new"] else "продлена"
                subs = self.sub_service.list_subscriptions(uid)
                return BotResponse(
                    text=(
                        f"✅ <b>Подписка {status_word}!</b>\n"
                        f"Город: <b>{sub['city']}</b> | Категория: <b>{sub['category']}</b>\n"
                        f"Отписаться в любой момент можно одной командой: /unsubscribe\n\n"
                        + format_subscriptions_list(subs)
                    ),
                    reply_markup=subscriptions_keyboard(subs),
                )

            if callback_data.startswith("sub_toggle:"):
                parts = callback_data.split(":", 2)
                city_val = parts[1] if len(parts) > 1 else "Все города"
                cat_val = parts[2] if len(parts) > 2 else "Все категории"
                res = self.sub_service.toggle_subscription(uid, city_val, cat_val)
                subs = self.sub_service.list_subscriptions(uid)
                if res["subscribed"]:
                    msg = f"✅ <b>Подписка оформлена:</b> {res['city']} • {res['category']}"
                else:
                    msg = f"🔕 <b>Подписка удалена:</b> {res['city']} • {res['category']}"
                return BotResponse(
                    text=f"{msg}\n\n{format_subscriptions_list(subs)}",
                    reply_markup=subscriptions_keyboard(subs),
                )

            if callback_data.startswith("sub_search:"):
                parts = callback_data.split(":", 2)
                city_val = parts[1] if len(parts) > 1 else "Все города"
                cat_val = parts[2] if len(parts) > 2 else "Все категории"
                filters = self.reset_user_filters(uid)
                filters.city = None if city_val == "Все города" else city_val
                filters.category = None if cat_val in ("Все категории", "все категории") else cat_val
                return self._execute_search_response(uid)

            if callback_data.startswith("unsub_id:"):
                sub_id = callback_data.split(":", 1)[1]
                self.sub_service.unsubscribe_by_id(uid, sub_id)
                subs = self.sub_service.list_subscriptions(uid)
                return BotResponse(
                    text=f"✅ Подписка удалена.\n\n{format_subscriptions_list(subs)}",
                    reply_markup=subscriptions_keyboard(subs),
                )

            if callback_data == "unsub:all":
                removed = self.sub_service.unsubscribe_all(uid)
                return self._build_main_menu_response(
                    uid,
                    prefix_notice=f"✅ <b>Все подписки удалены ({removed} шт.).</b> Навязчивых уведомлений не будет.",
                )

            return self._build_main_menu_response(uid)
        except Exception as exc:
            log_event(self.logger, logging.ERROR, "Callback error", user_id=uid, error=repr(exc))
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Произошла ошибка при обработке кнопки. Попробуйте ещё раз.",
            )

    def handle_document_upload(self, user_id: str, filename: str, content: str) -> BotResponse:
        """Импорт файла CSV/JSON через Telegram-бота для модератора (Раздел 3, 6.1, 6.2 ТЗ)."""
        new_request_id()
        uid = str(user_id)
        if not self.is_admin(uid):
            return BotResponse(
                text="🔒 Загрузка файлов расписания доступна только модераторам сервиса."
            )
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        try:
            report = self.import_service.import_from_content(
                content=content,
                filename=filename,
                suffix=suffix,
                actor=f"tg_moderator_{uid}",
            )
            return BotResponse(text=format_import_report(report))
        except Exception as exc:
            return BotResponse(
                text=f"❌ Ошибка при импорте файла «{filename}»: {exc}"
            )

    def _handle_admin_command(self, uid: str, text: str) -> BotResponse:
        if not self.is_admin(uid):
            return BotResponse(text="🔒 Команда доступна только модераторам сервиса.")

        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/admin_disputed":
            rows = self.db.fetchall(
                "SELECT id, title, city, status, dispute_reason FROM events WHERE is_disputed = 1 AND merged_into IS NULL ORDER BY updated_at DESC LIMIT 10"
            )
            if not rows:
                return BotResponse(text="✅ В очереди модерации нет спорных карточек.")
            lines = ["🔎 <b>Очередь спорных карточек:</b>\n"]
            for r in rows:
                lines.append(
                    f"• <code>{r['id']}</code> — <b>{r['title']}</b> ({r['city']}, статус: {r['status']})\n"
                    f"  Причина: {r['dispute_reason']}\n"
                    f"  Команды: <code>/admin_publish {r['id']}</code> | <code>/admin_cancel {r['id']}</code>"
                )
            return BotResponse(text="\n".join(lines))

        if cmd in ("/admin_cancel", "/admin_publish", "/admin_draft") and len(parts) >= 2:
            target_id = parts[1].strip()
            row = self.db.fetchone(
                "SELECT id FROM events WHERE id = ? OR id LIKE ?",
                (target_id, f"{target_id}%"),
            )
            if not row:
                return BotResponse(text=f"⚠️ Событие '{target_id}' не найдено.")
            status_map = {
                "/admin_cancel": "отменено",
                "/admin_publish": "опубликовано",
                "/admin_draft": "черновик",
            }
            ev = self.import_service.update_event_status(
                str(row["id"]), status_map[cmd], actor=f"tg_moderator_{uid}"
            )
            return BotResponse(
                text=f"✅ Статус события «{ev.title}» изменён на <b>{ev.status}</b> (записано в журнал изменений)."
            )

        if cmd == "/admin_audit":
            rows = self.db.fetchall("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10")
            if not rows:
                return BotResponse(text="Журнал изменений пуст.")
            lines = ["📋 <b>Последние записи журнала изменений (audit_log):</b>\n"]
            for r in rows:
                lines.append(
                    f"• [{r['created_at'][:19]}] <b>{r['actor']}</b> → {r['action']} ({r['entity_type']} {r['entity_id'][:8]})"
                )
            return BotResponse(text="\n".join(lines))

        return BotResponse(
            text=(
                "🛠 <b>Панель модератора (Уровень 1):</b>\n"
                "• Отправьте файл <code>.csv</code> или <code>.json</code> в чат для импорта событий\n"
                "• /admin_disputed — очередь спорных карточек\n"
                "• <code>/admin_publish &lt;id&gt;</code> — опубликовать карточку\n"
                "• <code>/admin_cancel &lt;id&gt;</code> — отметить событие как отменённое\n"
                "• /admin_audit — последние записи журнала изменений"
            )
        )
