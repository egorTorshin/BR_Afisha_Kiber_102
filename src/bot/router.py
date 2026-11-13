import html
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from src.bot.formatter import (
    format_agent_response,
    format_event_card,
    format_help_message,
    format_import_report,
    format_search_results,
    format_subscriptions_list,
    format_welcome_message,
    format_datetime_ru,
)
from src.bot.keyboards import (
    agent_results_keyboard,
    ages_keyboard,
    categories_keyboard,
    cities_keyboard,
    dates_keyboard,
    event_card_keyboard,
    main_menu_keyboard,
    prices_keyboard,
    search_results_keyboard,
    subscriptions_keyboard,
    contact_keyboard,
    home_keyboard,
    language_keyboard,
    leisure_categories_keyboard,
    leisure_search_keyboard,
    leisure_cities_keyboard,
    leisure_dates_keyboard,
    location_keyboard,
    profile_categories_keyboard,
    profile_keyboard,
    remove_reply_keyboard,
)
from src.config import Settings
from src.db.connection import Database
from src.models.schemas import SearchFilters
from src.services.import_service import ImportService
from src.services.agent_service import AgentService
from src.services.normalizer import parse_free_text_query, parse_iso_datetime, resolve_date_preset, resolve_city, resolve_category
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.services.profile_service import ProfileService
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
        self.import_service = ImportService(db, max_import_bytes=settings.max_import_bytes,
                                            max_import_rows=settings.max_import_rows)
        self.sub_service = SubscriptionService(db, retention_days=settings.subscription_retention_days)
        self.agent_service = AgentService(db, settings)
        self.profile_service = ProfileService(db)
        self.rate_limiter = RateLimiter(max_requests_per_minute=settings.rate_limit_per_minute)
        self.logger = get_logger("afisha_rb.bot", settings.log_level)

        self._user_filters: Dict[str, SearchFilters] = {}
        self._awaiting_custom_date: Dict[str, bool] = {}
        self._awaiting_profile_field: Dict[str, str] = {}
        self._leisure_wizard: Dict[str, str] = {}

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

    @staticmethod
    def _is_command(text: str, command: str) -> bool:
        return text == command or text.startswith(command + " ")

    def _build_main_menu_response(self, uid: str, prefix_notice: Optional[str] = None) -> BotResponse:
        profile = self.profile_service.get(uid)
        if profile and profile.get("onboarding_state") == "complete":
            name = html.escape(profile.get("name") or "")
            greeting = "Доброго времени суток! Я помогу вам с выбором досуга на основе ваших интересов, расписания и компании."
            if name:
                greeting = f"{greeting}\n\nРада вас видеть, <b>{name}</b>!"
            if prefix_notice:
                greeting = f"{prefix_notice}\n\n{greeting}"
            return BotResponse(text=greeting, reply_markup=home_keyboard())
        if not profile:
            return BotResponse(text="<b>Выберите язык / Телде һайлағыҙ / Choose language</b>", reply_markup=language_keyboard())
        return BotResponse(text="Продолжите регистрацию через /start.")

    def _execute_search_response(self, user_id: str) -> BotResponse:
        uid = str(user_id)
        filters = self.get_user_filters(uid)
        profile = self.profile_service.get(uid)
        if profile and profile.get("onboarding_state") == "complete":
            events, total, explanation = self.search_service.search_for_profile(
                filters, profile.get("latitude"), profile.get("longitude"), profile.get("favorite_categories"),
            )
        else:
            events, total, explanation = self.search_service.search(filters)
        is_sub = self.sub_service.is_subscribed(uid, filters.city, filters.category)
        if total == 0 and profile and profile.get("onboarding_state") == "complete":
            recommendation_kind, alternatives = self.search_service.recommend_alternatives(
                filters, self.settings.get_reference_date()
            )
            if alternatives:
                if recommendation_kind == "same_city":
                    intro = (f"К сожалению, по выбранным фильтрам в городе <b>{html.escape(filters.city or '')}</b> "
                             "ничего не удалось найти. Могу предложить ближайшие мероприятия в этом же городе:")
                elif recommendation_kind == "nearby_city":
                    intro = ("К сожалению, в выбранном городе подходящих будущих мероприятий нет. "
                             "Могу предложить варианты в ближайшем городе:")
                else:
                    intro = "К сожалению, подходящих мероприятий не найдено. Могу предложить ближайшие варианты:"
                lines = [intro]
                for index, event in enumerate(alternatives, start=1):
                    distance = f", {event.distance_km:.1f} км от выбранного города" if event.distance_km is not None else ""
                    lines.append(f"\n<b>{chr(96 + index)}) {html.escape(event.title)}</b>\n"
                                 f"📅 {format_datetime_ru(event.date_start)}\n📍 {html.escape(event.city)}{distance}")
                keyboard = {"inline_keyboard": [
                    [{"text": f"📌 {event.title[:34]}", "callback_data": f"card:{event.id}"}]
                    for event in alternatives
                ] + [[{"text": "✨ Новый поиск", "callback_data": "leisure:start"}]]}
                return BotResponse(text="\n".join(lines), reply_markup=keyboard)
        text = format_search_results(events, total, filters, empty_explanation=explanation)
        kb = search_results_keyboard(events, total, filters, is_subscribed=is_sub)
        return BotResponse(text=text, reply_markup=kb)

    def _handle_agent_query(self, uid: str, text: str) -> BotResponse:
        """Run a free-form query through the shared agent service.

        In deterministic no-ai mode the legacy rich filter renderer remains the
        presentation fallback, while the same request is still persisted and
        traced by the agent pipeline.
        """
        _, conversation_id = self.agent_service.get_or_create_principal(f"telegram:{uid}")
        result = self.agent_service.chat(
            text,
            conversation_id,
            user_id=f"telegram:{uid}",
        )
        if self.settings.ai_mode == "no-ai" and result.status in {"ok", "fallback", "no_data"}:
            parsed_filters = parse_free_text_query(
                text,
                ref_date=self.settings.get_reference_date(),
                db=self.db,
            )
            if parsed_filters.is_empty():
                parsed_filters.query_text = text
            self._user_filters[uid] = parsed_filters
            return self._execute_search_response(uid)
        return BotResponse(
            text=format_agent_response(result.response.message, result.response.event_cards),
            reply_markup=agent_results_keyboard(result.response.event_cards),
        )

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
            if self._is_command(clean_text, "/start"):
                parts = clean_text.split(maxsplit=1)
                if len(parts) == 2 and parts[1].startswith("event_"):
                    ev_id = parts[1][len("event_"):]
                    return self._open_card(uid, ev_id)
                self.reset_user_filters(uid)
                profile = self.profile_service.get(uid)
                if not profile:
                    return BotResponse(
                        text=("<b>Доброго времени суток!</b> Я помогу подобрать досуг с учётом ваших "
                              "интересов, расписания и компании.\n\n"
                              "<b>Выберите язык / Телде һайлағыҙ / Choose language</b>"),
                        reply_markup=language_keyboard(),
                    )
                if profile.get("onboarding_state") != "complete":
                    prompts = {
                        "name": "👋 <b>Как к вам обращаться?</b> Введите ваше имя.",
                        "phone": "📱 <b>Укажите номер телефона.</b> Он нужен только для уведомлений о новых мероприятиях; мы не передаём его организаторам.",
                        "city": "🏙 <b>В каком городе вы живёте?</b> Это необязательное поле для местных уведомлений. Напишите город или пропустите шаг.",
                    }
                    state = profile.get("onboarding_state", "name")
                    markup = contact_keyboard() if state == "phone" else (
                        {"inline_keyboard": [[{"text": "Пропустить", "callback_data": "onboarding:skip_city"}]]}
                        if state == "city" else None
                    )
                    return BotResponse(text=prompts.get(state, prompts["name"]), reply_markup=markup)
                return self._build_main_menu_response(uid)

            if self._is_command(clean_text, "/search"):
                return self._execute_search_response(uid)

            if self._is_command(clean_text, "/help"):
                filters = self.get_user_filters(uid)
                cnt = self.search_service.count_matching(filters)
                subs_cnt = len(self.sub_service.list_subscriptions(uid))
                return BotResponse(
                    text=format_help_message(),
                    reply_markup=main_menu_keyboard(filters, matching_count=cnt, subs_count=subs_cnt),
                )

            if self._is_command(clean_text, "/cancel"):
                self.reset_user_filters(uid)
                return self._build_main_menu_response(
                    uid,
                    prefix_notice="🔄 <b>Фильтры поиска сброшены.</b> Вы можете ввести новый запрос или выбрать параметры ниже:",
                )

            if self._is_command(clean_text, "/unsubscribe"):
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

            if self._is_command(clean_text, "/subscriptions"):
                subs = self.sub_service.list_subscriptions(uid)
                return BotResponse(
                    text=format_subscriptions_list(subs),
                    reply_markup=subscriptions_keyboard(subs),
                )

            if clean_text.startswith("/event_"):
                ev_id = clean_text[len("/event_"):].split()[0]
                return self._open_card(uid, ev_id)

            profile = self.profile_service.get(uid)
            if profile and profile.get("onboarding_state") != "complete":
                return self._handle_profile_text(uid, clean_text, profile)
            field = self._awaiting_profile_field.pop(uid, None)
            if field:
                return self._save_profile_field(uid, field, clean_text)

            if self._leisure_wizard.get(uid) == "city":
                city = resolve_city(clean_text, db=self.db)
                if not city:
                    return BotResponse(text="Не удалось распознать город. Напишите, например: Уфа, Салават или Стерлитамак.")
                filters = self.get_user_filters(uid)
                filters.city = city
                self._leisure_wizard[uid] = "date"
                return BotResponse(text="📅 <b>На какую дату ищем досуг?</b> Введите ДД.ММ.ГГГГ, например <code>15.11.2026</code>.")

            if self._leisure_wizard.get(uid) == "city_manual":
                city = resolve_city(clean_text, db=self.db)
                if not city:
                    return BotResponse(text="Не удалось распознать город. Проверьте написание или выберите вариант кнопкой.", reply_markup=leisure_cities_keyboard())
                self.get_user_filters(uid).city = city
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if self._leisure_wizard.get(uid) == "date":
                return self._handle_custom_date_input(uid, clean_text)

            if self._leisure_wizard.get(uid) == "date_manual":
                try:
                    iso_dt = parse_iso_datetime(clean_text if len(clean_text) != 5 else f"{clean_text}.{self.settings.get_reference_date().year}")
                except ValueError:
                    return BotResponse(text="Введите дату в формате ДД.ММ.ГГГГ, например 15.11.2026.")
                filters = self.get_user_filters(uid)
                filters.date_from, filters.date_to = f"{iso_dt[:10]}T00:00:00", f"{iso_dt[:10]}T23:59:59"
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if self._leisure_wizard.get(uid) == "category_manual":
                category = resolve_category(clean_text, db=self.db)
                if not category:
                    return BotResponse(text="Такой вид досуга не найден. Выберите вариант кнопкой или уточните название.", reply_markup=leisure_categories_keyboard(self.get_user_filters(uid).categories))
                filters = self.get_user_filters(uid)
                if category not in filters.categories:
                    filters.categories.append(category)
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if self._is_command(clean_text, "/admin") or clean_text.startswith("/admin_"):
                return self._handle_admin_command(uid, clean_text)

            # 2. Если пользователь вводит выбранную дату после нажатия кнопки «Указать дату»
            if self._awaiting_custom_date.get(uid):
                self._awaiting_custom_date.pop(uid, None)
                return self._handle_custom_date_input(uid, clean_text)

            # 3. Свободный текст обрабатывается как поисковый запрос без возврата в меню (Раздел 6.2 ТЗ)
            return self._handle_agent_query(uid, clean_text)

        except Exception as exc:
            log_event(self.logger, logging.ERROR, "Error handling message", user_id=uid, error_type=type(exc).__name__)
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
            if self._leisure_wizard.get(uid) == "date":
                self._leisure_wizard[uid] = "category"
                return BotResponse(text="🎭 <b>Выберите интересующие виды досуга:</b>", reply_markup=leisure_categories_keyboard([]))
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

    def _leisure_form_response(self, uid: str, notice: str = "") -> BotResponse:
        text = "<b>Настройте поиск досуга</b>\nВыберите город, дату и интересующие виды досуга."
        if notice:
            text = f"{notice}\n\n{text}"
        return BotResponse(text=text, reply_markup=leisure_search_keyboard(self.get_user_filters(uid)))

    def _handle_profile_text(self, uid: str, text: str, profile: Dict[str, Any]) -> BotResponse:
        state = profile.get("onboarding_state")
        if state == "name":
            if len(text) < 2 or len(text) > 80:
                return BotResponse(text="Введите имя от 2 до 80 символов.")
            self.profile_service.update(uid, name=text, onboarding_state="phone")
            return BotResponse(
                text=("📱 <b>Укажите номер телефона.</b> Он нужен только для уведомлений о новых мероприятиях "
                      "в вашем городе и не передаётся организаторам.\n\n"
                      "Отправьте контакт кнопкой ниже или введите номер в международном формате."),
                reply_markup=contact_keyboard(),
            )
        if state == "phone":
            digits = "".join(char for char in text if char.isdigit())
            if len(digits) < 10 or len(digits) > 15:
                return BotResponse(text="Пожалуйста, отправьте номер кнопкой или введите корректный номер.", reply_markup=contact_keyboard())
            self.profile_service.update(uid, phone=text, onboarding_state="city")
            return BotResponse(text="🏙 <b>В каком городе вы живёте?</b> Это необязательно: город нужен только для местных уведомлений. Напишите город или нажмите «Пропустить».", reply_markup={"inline_keyboard": [[{"text": "Пропустить", "callback_data": "onboarding:skip_city"}]]})
        if state == "city":
            city = resolve_city(text, db=self.db)
            if not city:
                return BotResponse(text="Не нашла такой город в каталоге. Проверьте написание или нажмите «Пропустить».", reply_markup={"inline_keyboard": [[{"text": "Пропустить", "callback_data": "onboarding:skip_city"}]]})
            self.profile_service.update(uid, city=city, onboarding_state="complete")
            return self._build_main_menu_response(uid, "✅ Регистрация завершена.")
        return self._build_main_menu_response(uid)

    def _save_profile_field(self, uid: str, field: str, text: str) -> BotResponse:
        if field == "phone":
            digits = "".join(char for char in text if char.isdigit())
            if len(digits) < 10 or len(digits) > 15:
                self._awaiting_profile_field[uid] = field
                return BotResponse(text="Введите корректный номер или отправьте контакт кнопкой.", reply_markup=contact_keyboard())
        if field == "city":
            city = resolve_city(text, db=self.db)
            if not city:
                self._awaiting_profile_field[uid] = field
                return BotResponse(text="Не нашла такой город в каталоге. Проверьте написание и попробуйте ещё раз.")
            text = city
        self.profile_service.update(uid, **{field: text})
        return self._profile_response(uid, "✅ Данные сохранены.")

    def _profile_response(self, uid: str, notice: str = "") -> BotResponse:
        profile = self.profile_service.get(uid) or {}
        cats = ", ".join(profile.get("favorite_categories") or []) or "не выбраны"
        location = "передана" if profile.get("latitude") is not None else "не передана"
        text = (f"👤 <b>Профиль</b>\n\nИмя: <b>{html.escape(profile.get('name') or '—')}</b>\n"
                f"Телефон: <b>{html.escape(profile.get('phone') or '—')}</b>\n"
                f"Город: <b>{html.escape(profile.get('city') or '—')}</b>\n"
                f"Любимые категории: <b>{html.escape(cats)}</b>\n"
                f"Геолокация: <b>{location}</b>")
        return BotResponse(text=(notice + "\n\n" if notice else "") + text, reply_markup=profile_keyboard(profile))

    def handle_contact(self, user_id: str, phone: str) -> BotResponse:
        profile = self.profile_service.get(str(user_id))
        if not profile:
            return BotResponse(text="Сначала начните с /start.")
        state = profile.get("onboarding_state")
        if state == "phone":
            self.profile_service.update(str(user_id), phone=phone, onboarding_state="city")
            return BotResponse(text="🏙 <b>В каком городе вы живёте?</b> Это необязательное поле для местных уведомлений. Напишите город или пропустите этот шаг.", reply_markup={"inline_keyboard": [[{"text": "Пропустить", "callback_data": "onboarding:skip_city"}]]})
        self.profile_service.update(str(user_id), phone=phone)
        return self._profile_response(str(user_id), "✅ Телефон обновлён.")

    def handle_location(self, user_id: str, latitude: float, longitude: float) -> BotResponse:
        self.profile_service.ensure(str(user_id))
        self.profile_service.update(str(user_id), latitude=latitude, longitude=longitude)
        return self._profile_response(str(user_id), "✅ Геолокация сохранена. Она используется только для сортировки мероприятий по близости и никуда не передаётся.")

    def _open_card(self, uid: str, event_id_or_prefix: str) -> BotResponse:
        ev = self.search_service.get_event_by_id(event_id_or_prefix, include_drafts=self.is_admin(uid))
        if not ev:
            return self._build_main_menu_response(
                uid,
                prefix_notice="⚠️ Событие с указанным идентификатором не найдено в базе данных.",
            )
        profile = self.profile_service.get(uid)
        if profile and profile.get("latitude") is not None and profile.get("longitude") is not None:
            ev.distance_km = self.search_service.distance_km(profile["latitude"], profile["longitude"], ev)
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
            if callback_data.startswith("lang:"):
                language = callback_data.split(":", 1)[1]
                if language not in {"ru", "ba", "en"}:
                    return BotResponse(text="Выберите язык кнопкой ниже.", reply_markup=language_keyboard())
                self.profile_service.ensure(uid, language)
                return BotResponse(text="👋 <b>Как к вам обращаться?</b> Введите ваше имя.")

            if callback_data == "onboarding:skip_city":
                self.profile_service.update(uid, city=None, onboarding_state="complete")
                return self._build_main_menu_response(uid, "✅ Регистрация завершена.")

            if callback_data == "profile:open":
                return self._profile_response(uid)

            if callback_data.startswith("profile:edit:"):
                field = callback_data.rsplit(":", 1)[1]
                if field not in {"name", "phone", "city"}:
                    return self._profile_response(uid)
                self._awaiting_profile_field[uid] = field
                prompt = {"name": "Введите новое имя.", "phone": "Отправьте новый номер кнопкой или введите его текстом.", "city": "Введите город проживания."}[field]
                return BotResponse(text=prompt, reply_markup=contact_keyboard() if field == "phone" else None)

            if callback_data == "profile:categories":
                profile = self.profile_service.get(uid) or {}
                return BotResponse(text="🎭 <b>Выберите любимые категории</b> — можно несколько.", reply_markup=profile_categories_keyboard(profile.get("favorite_categories") or []))

            if callback_data.startswith("profile:cat:"):
                category = callback_data.rsplit(":", 1)[1]
                profile = self.profile_service.get(uid) or {}
                selected = list(profile.get("favorite_categories") or [])
                if category in selected:
                    selected.remove(category)
                else:
                    selected.append(category)
                self.profile_service.update(uid, favorite_categories=selected)
                return BotResponse(text="🎭 <b>Выберите любимые категории</b> — можно несколько.", reply_markup=profile_categories_keyboard(selected))

            if callback_data == "profile:done":
                return self._profile_response(uid, "✅ Любимые категории сохранены.")

            if callback_data == "profile:location":
                return BotResponse(text="📍 Передайте геолокацию кнопкой ниже. Она нужна только для сортировки найденных мероприятий по близости и не передаётся организаторам.", reply_markup=location_keyboard())

            if callback_data == "leisure:start":
                self.reset_user_filters(uid)
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if callback_data == "leisure:form":
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if callback_data == "leisure:city_menu":
                return BotResponse(text="🏙 <b>Выберите город</b> или введите свой вручную.", reply_markup=leisure_cities_keyboard())

            if callback_data == "leisure:city_manual":
                self._leisure_wizard[uid] = "city_manual"
                return BotResponse(text="🏙 Введите название города.")

            if callback_data.startswith("leisure:city:"):
                self.get_user_filters(uid).city = callback_data.rsplit(":", 1)[1]
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if callback_data == "leisure:date_menu":
                return BotResponse(text="📅 <b>Выберите дату</b> или введите свою вручную.", reply_markup=leisure_dates_keyboard())

            if callback_data == "leisure:date_manual":
                self._leisure_wizard[uid] = "date_manual"
                return BotResponse(text="📅 Введите дату в формате ДД.ММ.ГГГГ, например 15.11.2026.")

            if callback_data.startswith("leisure:date:"):
                preset = callback_data.rsplit(":", 1)[1]
                filters = self.get_user_filters(uid)
                filters.date_preset = preset
                filters.date_from, filters.date_to = resolve_date_preset(preset, self.settings.get_reference_date())
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if callback_data == "leisure:category_menu":
                return BotResponse(text="🎭 <b>Выберите один или несколько видов досуга.</b>", reply_markup=leisure_categories_keyboard(self.get_user_filters(uid).categories))

            if callback_data == "leisure:category_manual":
                self._leisure_wizard[uid] = "category_manual"
                return BotResponse(text="🎭 Напишите вид досуга, например: лекция, мастер-класс или спектакль.")

            if callback_data.startswith("leisure:cat:"):
                category = callback_data.rsplit(":", 1)[1]
                filters = self.get_user_filters(uid)
                if category in filters.categories:
                    filters.categories.remove(category)
                else:
                    filters.categories.append(category)
                return BotResponse(text="🎭 <b>Выберите один или несколько видов досуга.</b>", reply_markup=leisure_categories_keyboard(filters.categories))

            if callback_data == "leisure:categories_done":
                self._leisure_wizard[uid] = "form"
                return self._leisure_form_response(uid)

            if callback_data == "leisure:run":
                filters = self.get_user_filters(uid)
                if not filters.city or not filters.date_from or not filters.categories:
                    return self._leisure_form_response(uid, "Заполните город, дату и хотя бы один вид досуга.")
                self._leisure_wizard.pop(uid, None)
                return self._execute_search_response(uid)

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
            log_event(self.logger, logging.ERROR, "Callback error", user_id=uid, error_type=type(exc).__name__)
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
