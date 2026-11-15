import html
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from src.agent.schemas import ConstraintSet
from src.bot.formatter import (
    format_agent_response,
    format_event_card,
    format_filter_summary_block,
    format_help_message,
    format_import_report,
    format_recommendations_results,
    format_search_results,
    format_subscriptions_list,
    format_welcome_message,
    format_datetime_ru,
    format_datetime,
)
from src.bot.keyboards import (
    agent_results_keyboard,
    ages_keyboard,
    categories_keyboard,
    cities_keyboard,
    dates_keyboard,
    delete_account_keyboard,
    event_card_keyboard,
    filters_keyboard,
    main_menu_keyboard,
    prices_keyboard,
    search_results_keyboard,
    subscriptions_keyboard,
    location_keyboard,
    profile_categories_keyboard,
    profile_keyboard,
    remove_reply_keyboard,
)
from src.bot.i18n import t
from src.config import Settings
from src.db.connection import Database
from src.models.schemas import SearchFilters
from src.services.import_service import ImportService
from src.services.agent_service import AgentService
from src.services.normalizer import normalize_text, parse_free_text_query, parse_iso_datetime, resolve_date_preset, resolve_city, resolve_category
from src.services.search_service import SearchService
from src.services.recommendation_service import RecommendationService
from src.services.subscription_service import SubscriptionService
from src.services.profile_service import ProfileService
from src.services.user_service import UserService
from src.utils.logger import get_logger, log_event, new_request_id, scrub_pii
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

    def __init__(self, db: Database, settings: Settings, platform: str = "telegram") -> None:
        self.db = db
        self.settings = settings
        if platform not in {"telegram", "max"}:
            raise ValueError("Unsupported bot platform")
        self.platform = platform
        self.search_service = SearchService(db)
        self.recommendation_service = RecommendationService(db)
        self.import_service = ImportService(db, max_import_bytes=settings.max_import_bytes,
                                            max_import_rows=settings.max_import_rows)
        self.sub_service = SubscriptionService(db, retention_days=settings.subscription_retention_days)
        self.agent_service = AgentService(db, settings)
        self.profile_service = ProfileService(db)
        self.user_service = UserService(db)
        self.rate_limiter = RateLimiter(max_requests_per_minute=settings.rate_limit_per_minute)
        self.logger = get_logger("afisha_rb.bot", settings.log_level)

        self._user_filters: Dict[str, SearchFilters] = {}
        self._awaiting_custom_date: Dict[str, bool] = {}
        self._agent_conversations: Dict[str, str] = {}
        self._manually_edited_filters: set[str] = set()

    def get_user_filters(self, user_id: str) -> SearchFilters:
        uid = str(user_id)
        if uid not in self._user_filters:
            self._user_filters[uid] = SearchFilters()
        return self._user_filters[uid]

    def _scope_user(self, user_id: str) -> str:
        uid = str(user_id)
        return uid if self.platform == "telegram" else f"max:{uid}"

    def reset_user_filters(self, user_id: str) -> SearchFilters:
        uid = str(user_id)
        self._user_filters[uid] = SearchFilters()
        self._awaiting_custom_date.pop(uid, None)
        if uid in self._agent_conversations:
            self._manually_edited_filters.add(uid)
            self._sync_manual_filters_to_agent(uid, self._agent_conversations[uid])
        return self._user_filters[uid]

    def is_admin(self, user_id: str) -> bool:
        if self.platform != "telegram":
            return False
        user = self.user_service.telegram_user(str(user_id), create=False)
        return str(user_id) in set(self.settings.admin_user_ids) or bool(user and user['role'] == 'admin')

    def _language(self, user_id: str) -> str:
        profile = self.profile_service.get(str(user_id))
        return str((profile or {}).get("language") or "ru")

    @staticmethod
    def _is_command(text: str, command: str) -> bool:
        return text == command or text.startswith(command + " ")

    def _build_main_menu_response(self, uid: str, prefix_notice: Optional[str] = None) -> BotResponse:
        filters = self.get_user_filters(uid)
        cnt = self.search_service.count_matching(filters)
        subs_cnt = len(self.sub_service.list_subscriptions(uid))
        welcome = format_welcome_message(filters, matching_count=cnt)
        return BotResponse(text=(f"{prefix_notice}\n\n" if prefix_notice else "") + welcome,
                           reply_markup=main_menu_keyboard(filters, matching_count=cnt, subs_count=subs_cnt, language=self._language(uid)))

    def _execute_search_response(self, user_id: str) -> BotResponse:
        uid = str(user_id)
        filters = self.get_user_filters(uid)
        profile = self.profile_service.get(uid)
        language = self._language(uid)
        if profile:
            events, total, explanation = self.search_service.search_for_profile(
                filters, profile.get("latitude"), profile.get("longitude"), profile.get("favorite_categories"),
            )
        else:
            events, total, explanation = self.search_service.search(filters)
        is_sub = self.sub_service.is_subscribed(uid, filters.city, filters.category)
        if total == 0 and profile:
            recommendation_kind, alternatives = self.search_service.recommend_alternatives(
                filters, self.settings.get_reference_date()
            )
            if alternatives:
                intros = {
                    "ru": {
                        "same_city": f"К сожалению, по выбранным фильтрам в городе <b>{html.escape(filters.city or '')}</b> ничего не найдено. Могу предложить ближайшие мероприятия в этом же городе:",
                        "nearby_city": "К сожалению, в выбранном городе подходящих мероприятий нет. Могу предложить варианты в ближайшем городе:",
                        "other_city": "К сожалению, подходящих мероприятий не найдено. Могу предложить ближайшие варианты:",
                    },
                    "en": {
                        "same_city": f"Unfortunately, nothing matches your filters in <b>{html.escape(filters.city or '')}</b>. Here are the nearest dates in the same city:",
                        "nearby_city": "Unfortunately, there are no matching events in that city. Here are options in the nearest city:",
                        "other_city": "Unfortunately, no exact matches were found. Here are the nearest alternatives:",
                    },
                    "ba": {
                        "same_city": f"Ҡыҙғанысҡа ҡаршы, <b>{html.escape(filters.city or '')}</b> ҡалаһында һайланған шарттар буйынса саралар табылманы. Ошо ҡалалағы яҡындағы саралар:",
                        "nearby_city": "Ҡыҙғанысҡа ҡаршы, һайланған ҡалала ярашлы саралар юҡ. Яҡындағы ҡалалағы варианттар:",
                        "other_city": "Ҡыҙғанысҡа ҡаршы, теүәл ярашлы саралар табылманы. Яҡындағы варианттар:",
                    },
                }
                intro = intros[language].get(recommendation_kind, intros[language]["other_city"])
                lines = [intro]
                for index, event in enumerate(alternatives, start=1):
                    distance = (f", {event.distance_km:.1f} km away" if language == "en" else f", {event.distance_km:.1f} км") if event.distance_km is not None else ""
                    lines.append(f"\n<b>{index}. {html.escape(event.title)}</b>\n"
                                 f"📅 {format_datetime(event.date_start, language)}\n📍 {html.escape(event.city)}{distance}")
                keyboard = {"inline_keyboard": [
                    [{"text": f"📌 {event.title[:34]}", "callback_data": f"card:{event.id}"}]
                    for event in alternatives
                ] + [[{"text": "⚙️ Фильтры", "callback_data": "menu:filters"},
                     {"text": "🏠 Меню", "callback_data": "menu:main"}]]}
                return BotResponse(text="\n".join(lines), reply_markup=keyboard)
        text = format_search_results(
            events, total, filters,
            empty_explanation=explanation if language == "ru" else None,
            language=language,
        )
        kb = search_results_keyboard(events, total, filters, is_subscribed=is_sub, language=language)
        return BotResponse(text=text, reply_markup=kb)

    def _remember_agent_search_filters(self, uid: str, text: str, tool_calls: list) -> bool:
        """Mirror the last executed catalog search into the bot's editable filters."""
        search_calls = [call for call in tool_calls if call.name in {"search_events", "get_venue_schedule"}]
        if not search_calls:
            return False
        args = search_calls[-1].arguments
        filters = SearchFilters()
        for field in ("city", "category", "venue", "time_after", "time_before", "max_duration_min",
                      "max_price", "is_free_only", "max_age_limit"):
            if field in args:
                setattr(filters, field, args[field])
        for field, suffix in (("date_from", "T00:00:00"), ("date_to", "T23:59:59")):
            value = args.get(field)
            if value:
                day = str(value)[:10]
                setattr(filters, field, day + suffix)
        for preset in ("weekend", "today", "tomorrow", "week", "next_week", "month",
                       "friday", "saturday", "sunday"):
            start, end = resolve_date_preset(preset, self.settings.get_reference_date())
            if (start, end) == (filters.date_from, filters.date_to):
                filters.date_preset = preset
                break
        raw_tool_text = args.get("text")
        tool_text = raw_tool_text.strip() if isinstance(raw_tool_text, str) else ""
        if tool_text.casefold() in {"none", "null"}:
            tool_text = ""
        if tool_text and normalize_text(tool_text) != normalize_text(text):
            filters.query_text = scrub_pii(tool_text)[:200]
        self._user_filters[uid] = filters
        self._manually_edited_filters.discard(uid)
        return True

    def _sync_manual_filters_to_agent(self, uid: str, conversation_id: str) -> None:
        if uid not in self._manually_edited_filters:
            return
        state = self.agent_service.dialogue_repo.get(conversation_id)
        if state is None:
            self._manually_edited_filters.discard(uid)
            return
        filters = self.get_user_filters(uid)
        values = {}
        for field in ConstraintSet.model_fields:
            value = getattr(filters, field, None)
            if value is not None:
                values[field] = value[:10] if field in {"date_from", "date_to"} else value
        state.active_constraints = ConstraintSet.model_validate(values)
        state.pending_confirmation = None
        state.pending_clarification = None
        state.last_result_ids = []
        state.last_search_results = []
        state.shown_event_ids = []
        state.selected_event_id = None
        state.has_previous_results = False
        state.next_search_offset = 0
        self.agent_service.dialogue_repo.save(state)
        self._manually_edited_filters.discard(uid)

    def _search_after_filter_change(self, uid: str) -> BotResponse:
        if uid in self._agent_conversations:
            self._manually_edited_filters.add(uid)
            self._sync_manual_filters_to_agent(uid, self._agent_conversations[uid])
        return self._execute_search_response(uid)

    def _recommend_response(self, uid: str) -> BotResponse:
        filters = self.get_user_filters(uid)
        recs = self.recommendation_service.recommend_for_user(
            uid, city=filters.city, limit=5, ref_date=self.settings.get_reference_date())
        events = [item.event for item in recs]
        keyboard_filters = SearchFilters(city=filters.city, limit=5)
        is_sub = self.sub_service.is_subscribed(uid, filters.city, None)
        return BotResponse(
            text=format_recommendations_results(recs, language=self._language(uid)),
            reply_markup=search_results_keyboard(events, len(events), keyboard_filters,
                                                 is_subscribed=is_sub, language=self._language(uid)),
        )

    def _handle_agent_query(self, uid: str, text: str) -> BotResponse:
        """Run a free-form query through the shared agent service.

        Search state comes from the executed tool call and persisted dialogue.
        """
        try:
            _, conversation_id = self.agent_service.get_or_create_principal(uid if uid.startswith("max:") else f"telegram:{uid}")
            self._sync_manual_filters_to_agent(uid, conversation_id)
            result = self.agent_service.chat(
                text,
                conversation_id,
                user_id=uid if uid.startswith("max:") else f"telegram:{uid}",
            )
            self._agent_conversations[uid] = conversation_id
        except Exception:
            raise
        trace = (self.agent_service.get_trace(result.request_id)
                 if result is not None and result.status in {"ok", "fallback", "no_data"} else None)
        remembered = False
        if trace:
            remembered = self._remember_agent_search_filters(uid, text, trace.tool_calls)
        filters = self.get_user_filters(uid)
        filters_note = ("\n\n⚙️ <b>Фильтры:</b> " + html.escape(filters.describe_human())
                        if remembered and not filters.is_empty() else "")
        return BotResponse(
            text=format_agent_response(result.response.message, result.response.event_cards) + filters_note,
            reply_markup=agent_results_keyboard(result.response.event_cards, show_filters=bool(filters_note)),
        )

    def handle_message(self, user_id: str, text: str) -> BotResponse:
        new_request_id()
        uid = self._scope_user(user_id)

        if self.platform == "telegram":
            self.user_service.telegram_user(uid)
        else:
            self.user_service.external_user("max", str(user_id))

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
            if self._is_command(clean_text, "/start"):
                parts = clean_text.split(maxsplit=1)
                if len(parts) == 2 and parts[1].startswith("event_"):
                    ev_id = parts[1][len("event_"):]
                    return self._open_card(uid, ev_id)
                self.reset_user_filters(uid)
                return self._build_main_menu_response(uid)

            if self._is_command(clean_text, "/search"):
                return self._execute_search_response(uid)
            if self._is_command(clean_text, "/recommend"):
                return self._recommend_response(uid)
            if self._is_command(clean_text, "/help"):
                filters = self.get_user_filters(uid)
                return BotResponse(text=format_help_message(), reply_markup=main_menu_keyboard(
                    filters, self.search_service.count_matching(filters),
                    len(self.sub_service.list_subscriptions(uid)), self._language(uid)))
            if self._is_command(clean_text, "/cancel") or self._is_command(clean_text, "/reset"):
                self.reset_user_filters(uid)
                return self._build_main_menu_response(uid, "🔄 <b>Фильтры поиска сброшены.</b>")
            if self._is_command(clean_text, "/unsubscribe"):
                removed = self.sub_service.unsubscribe_all(uid)
                return self._build_main_menu_response(uid, f"✅ <b>Вы отписаны от всех уведомлений</b> (удалено подписок: {removed}).")
            if self._is_command(clean_text, "/subscriptions"):
                subs = self.sub_service.list_subscriptions(uid)
                return BotResponse(text=format_subscriptions_list(subs), reply_markup=subscriptions_keyboard(subs))
            if clean_text.startswith('/event_'):
                return self._open_card(uid, clean_text[len('/event_'):].split()[0])
            if self._is_command(clean_text, "/admin") or clean_text.startswith("/admin_"):
                return self._handle_admin_command(uid, clean_text)

            # 2. Если пользователь вводит выбранную дату после нажатия кнопки «Указать дату»
            if self._awaiting_custom_date.get(uid):
                self._awaiting_custom_date.pop(uid, None)
                return self._handle_custom_date_input(uid, clean_text)

            if re.fullmatch(r"(?:мои\s+)?рекомендации[.!?]?|(?:посоветуй|порекомендуй)\s+что(?:-|\s)?нибудь\s+для\s+меня[.!?]?",
                            clean_text.casefold().replace("ё", "е")):
                return self._recommend_response(uid)

            # 3. Свободный текст обрабатывается как поисковый запрос без возврата в меню (Раздел 6.2 ТЗ)
            return self._handle_agent_query(uid, clean_text)

        except Exception as exc:
            log_event(self.logger, logging.ERROR, "Error handling message", user_id=uid, error_type=type(exc).__name__)
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Поиск временно недоступен. Попробуйте повторить запрос чуть позже.",
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
            return self._search_after_filter_change(uid)
        except ValueError:
            return BotResponse(
                text=(
                    "⚠️ <b>Дата не распознана.</b>\n"
                    "Пожалуйста, введите дату в формате <b>ДД.ММ.ГГГГ</b> (например, <code>14.11.2026</code>) "
                    "или выберите готовый период кнопкой ниже:"
                ),
                reply_markup=dates_keyboard(self.get_user_filters(uid).date_preset),
            )

    def _profile_response(self, uid: str, notice: str = "") -> BotResponse:
        profile = self.profile_service.get(uid) or {}
        language = profile.get("language", "ru")
        cats = ", ".join(profile.get("favorite_categories") or []) or t(language, "not_selected")
        location = t(language, "shared") if profile.get("latitude") is not None else t(language, "not_shared")
        text = (f"{t(language, 'profile_title')}\n\n{t(language, 'field_favorites')}: <b>{html.escape(cats)}</b>\n"
                f"{t(language, 'field_location')}: <b>{location}</b>\n"
                f"{t(language, 'language_label')}: <b>{html.escape(language)}</b>")
        return BotResponse(text=(notice + "\n\n" if notice else "") + text, reply_markup=profile_keyboard(profile))

    def handle_location(self, user_id: str, latitude: float, longitude: float) -> BotResponse:
        uid = self._scope_user(user_id)
        self.profile_service.ensure(uid)
        self.profile_service.update(uid, latitude=latitude, longitude=longitude)
        language = self._language(uid)
        notice = {
            "ru": "✅ Геолокация сохранена. Она используется только для сортировки мероприятий по близости.",
            "en": "✅ Location saved. It is used only to sort events by distance.",
            "ba": "✅ Геолокация һаҡланды. Ул сараларҙы алыҫлыҡ буйынса теҙеү өсөн генә ҡулланыла.",
        }[language]
        return self._profile_response(uid, notice)

    def _open_card(self, uid: str, event_id_or_prefix: str) -> BotResponse:
        ev = self.search_service.get_event_by_id(event_id_or_prefix, include_drafts=self.is_admin(uid))
        if not ev:
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Событие с указанным идентификатором не найдено в базе данных.",
            )
        user = (self.user_service.telegram_user(uid) if self.platform == "telegram"
                else self.user_service.external_user("max", uid.removeprefix("max:")))
        self.user_service.record_view(user['id'], ev.id, self.platform)
        profile = self.profile_service.get(uid)
        if profile and profile.get("latitude") is not None and profile.get("longitude") is not None:
            ev.distance_km = self.search_service.distance_km(profile["latitude"], profile["longitude"], ev)
        is_sub = self.sub_service.is_subscribed(uid, ev.city, ev.category)
        return BotResponse(
            text=format_event_card(ev, language=self._language(uid)),
            reply_markup=event_card_keyboard(ev, is_subscribed=is_sub, language=self._language(uid)),
        )

    def handle_callback(self, user_id: str, callback_data: str) -> BotResponse:
        new_request_id()
        uid = self._scope_user(user_id)

        if not self.rate_limiter.allow(uid):
            return BotResponse(
                text="⏳ Пожалуйста, подождите несколько секунд перед следующим действием."
            )

        log_event(self.logger, logging.INFO, "Incoming callback query", user_id=uid,
                  callback_length=len(callback_data or ""))
        filters = self.get_user_filters(uid)
        self._awaiting_custom_date.pop(uid, None)

        try:
            if callback_data == "profile:open":
                self.profile_service.ensure(uid)
                return self._profile_response(uid)

            if callback_data == "profile:language":
                return BotResponse(
                    text="<b>Выберите язык / Телде һайлағыҙ / Choose language</b>",
                    reply_markup={"inline_keyboard": [
                        [{"text": "Русский", "callback_data": "profile_lang:ru"}],
                        [{"text": "Башҡортса", "callback_data": "profile_lang:ba"}],
                        [{"text": "English", "callback_data": "profile_lang:en"}],
                    ]},
                )

            if callback_data.startswith("profile_lang:"):
                language = callback_data.split(":", 1)[1]
                if language not in {"ru", "ba", "en"}:
                    return self._profile_response(uid)
                self.profile_service.ensure(uid)
                self.profile_service.update(uid, language=language)
                return self._profile_response(uid, t(language, "lang_saved"))

            if callback_data == "profile:delete_confirm":
                language = self._language(uid)
                delete_text = {
                    "ru": "⚠️ <b>Удалить профиль?</b>\n\nБудут удалены имя, город, геолокация, любимые категории и уведомления.",
                    "en": "⚠️ <b>Delete your profile?</b>\n\nYour name, city, location, favourites and notifications will be deleted.",
                    "ba": "⚠️ <b>Профилде юйырғамы?</b>\n\nИсем, ҡала, геолокация, яратҡан категориялар һәм хәбәрнамәләр юйыласаҡ.",
                }[language]
                return BotResponse(
                    text=delete_text,
                    reply_markup=delete_account_keyboard(language),
                )

            if callback_data == "profile:delete":
                language = self._language(uid)
                self.sub_service.unsubscribe_all(uid)
                self.profile_service.delete(uid)
                self.reset_user_filters(uid)
                deleted_text = {
                    "ru": "✅ Аккаунт и связанные данные удалены. Если захотите вернуться, начните заново.",
                    "en": "✅ Your account and associated data have been deleted. You can start again at any time.",
                    "ba": "✅ Аккаунт һәм уға бәйле мәғлүмәттәр юйылды. Теләһәгеҙ, яңынан башлай алаһығыҙ.",
                }[language]
                return BotResponse(
                    text=deleted_text,
                    reply_markup=main_menu_keyboard(),
                )

            if callback_data == "profile:categories":
                profile = self.profile_service.get(uid) or {}
                language = self._language(uid)
                return BotResponse(text=t(language, "choose_types"), reply_markup=profile_categories_keyboard(profile.get("favorite_categories") or [], language))

            if callback_data.startswith("profile:cat:"):
                category = callback_data.rsplit(":", 1)[1]
                profile = self.profile_service.get(uid) or {}
                selected = list(profile.get("favorite_categories") or [])
                if category in selected:
                    selected.remove(category)
                else:
                    selected.append(category)
                self.profile_service.update(uid, favorite_categories=selected)
                language = self._language(uid)
                return BotResponse(text=t(language, "choose_types"), reply_markup=profile_categories_keyboard(selected, language))

            if callback_data == "profile:done":
                language = self._language(uid)
                notice = {"ru": "✅ Любимые категории сохранены.", "en": "✅ Favourite categories saved.", "ba": "✅ Яратҡан категориялар һаҡланды."}[language]
                return self._profile_response(uid, notice)

            if callback_data == "profile:location":
                language = self._language(uid)
                messages = {
                    "ru": "📍 Передайте геолокацию кнопкой ниже. Она нужна только для сортировки мероприятий по близости и не передаётся организаторам.",
                    "en": "📍 Share your location below. It is used only to sort events by distance and is never shared with organisers.",
                    "ba": "📍 Геолокацияғыҙҙы ебәрегеҙ. Ул сараларҙы алыҫлыҡ буйынса теҙеү өсөн генә ҡулланыла һәм ойоштороусыларға тапшырылмай.",
                }
                return BotResponse(text=messages[language], reply_markup=location_keyboard(language))

            if callback_data == "menu:main":
                return self._build_main_menu_response(uid)

            if callback_data == "menu:filters":
                return BotResponse(
                    text="⚙️ <b>Фильтры поиска</b>\n" + format_filter_summary_block(filters) +
                         "\n\nВыберите параметр: после выбора сразу покажу события.",
                    reply_markup=filters_keyboard(filters),
                )

            if callback_data.startswith("filter_clear:"):
                field = callback_data.split(":", 1)[1]
                if field not in {"query_text", "time_after", "time_before", "max_duration_min", "language", "venue"}:
                    return self._build_main_menu_response(uid)
                setattr(filters, field, None)
                filters.offset = 0
                return self._search_after_filter_change(uid)

            if callback_data == "menu:help":
                cnt = self.search_service.count_matching(filters)
                subs_cnt = len(self.sub_service.list_subscriptions(uid))
                return BotResponse(
                    text=format_help_message(),
                    reply_markup=main_menu_keyboard(filters, matching_count=cnt, subs_count=subs_cnt,
                                                    language=self._language(uid)),
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
                city_note = f" (г. {html.escape(filters.city)})" if filters.city else " (вся республика)"
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
                return self._search_after_filter_change(uid)

            if callback_data == "preset:for_you":
                return self._recommend_response(uid)

            if callback_data == "preset:kids":
                filters.max_age_limit = 6
                filters.offset = 0
                return self._search_after_filter_change(uid)

            if callback_data.startswith("city:"):
                val = callback_data.split(":", 1)[1]
                filters.city = None if val == "all" else val
                filters.offset = 0
                return self._search_after_filter_change(uid)

            if callback_data.startswith("cat:"):
                val = callback_data.split(":", 1)[1]
                filters.category = None if val == "all" else val
                filters.offset = 0
                return self._search_after_filter_change(uid)

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
                return self._search_after_filter_change(uid)

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
                return self._search_after_filter_change(uid)

            if callback_data.startswith("age:"):
                val = callback_data.split(":", 1)[1]
                filters.max_age_limit = None if val == "all" else int(val)
                filters.offset = 0
                return self._search_after_filter_change(uid)

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
                        f"Город: <b>{html.escape(sub['city'])}</b> | Категория: <b>{html.escape(sub['category'])}</b>\n"
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
                    msg = f"✅ <b>Подписка оформлена:</b> {html.escape(res['city'])} • {html.escape(res['category'])}"
                else:
                    msg = f"🔕 <b>Подписка удалена:</b> {html.escape(res['city'])} • {html.escape(res['category'])}"
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
                return self._search_after_filter_change(uid)

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
            log_event(self.logger, logging.ERROR, "Callback error", user_id=uid, error_type=type(exc).__name__)
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Произошла ошибка при обработке кнопки. Попробуйте ещё раз.",
            )

    def handle_document_upload(self, user_id: str, filename: str, content: str) -> BotResponse:
        """Импорт файла CSV/JSON через Telegram-бота для модератора (Раздел 3, 6.1, 6.2 ТЗ)."""
        new_request_id()
        uid = self._scope_user(user_id)
        if not self.is_admin(uid):
            return BotResponse(
                text="🔒 Загрузка файлов расписания доступна только модераторам сервиса."
            )
        if not self.rate_limiter.allow(uid):
            return BotResponse(text="⏳ Слишком много запросов. Повторите попытку позже.")
        if len(content.encode("utf-8")) > self.import_service.max_import_bytes:
            return BotResponse(text="❌ Файл импорта слишком большой.")
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
                text=f"❌ Ошибка при импорте файла «{html.escape(filename)}»: {html.escape(str(exc))}"
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
                    f"• <code>{html.escape(r['id'])}</code> — <b>{html.escape(r['title'])}</b> ({html.escape(r['city'])}, статус: {html.escape(r['status'])})\n"
                    f"  Причина: {html.escape(r['dispute_reason'] or '')}\n"
                    f"  Команды: <code>/admin_publish {html.escape(r['id'])}</code> | <code>/admin_cancel {html.escape(r['id'])}</code>"
                )
            return BotResponse(text="\n".join(lines))

        if cmd in ("/admin_cancel", "/admin_publish", "/admin_draft") and len(parts) >= 2:
            target_id = parts[1].strip()
            target = self.search_service.get_event_by_id(target_id, include_drafts=True)
            if target is None:
                return BotResponse(text=f"⚠️ Событие '{html.escape(target_id)}' не найдено.")
            status_map = {
                "/admin_cancel": "отменено",
                "/admin_publish": "опубликовано",
                "/admin_draft": "черновик",
            }
            ev = self.import_service.update_event_status(
                target.id, status_map[cmd], actor=f"tg_moderator_{uid}"
            )
            return BotResponse(
                text=f"✅ Статус события «{html.escape(ev.title)}» изменён на <b>{html.escape(ev.status)}</b> (записано в журнал изменений)."
            )

        if cmd == "/admin_audit":
            rows = self.db.fetchall("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10")
            if not rows:
                return BotResponse(text="Журнал изменений пуст.")
            lines = ["📋 <b>Последние записи журнала изменений (audit_log):</b>\n"]
            for r in rows:
                lines.append(
                    f"• [{html.escape(r['created_at'][:19])}] <b>{html.escape(r['actor'])}</b> → {html.escape(r['action'])} ({html.escape(r['entity_type'])} {html.escape(r['entity_id'][:8])})"
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
