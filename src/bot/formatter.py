import html
from datetime import datetime
from typing import Any, Dict, List, Optional
from src.models.schemas import EventRecord, ImportReport, SearchFilters

MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

WEEKDAYS_SHORT = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс",
}

CATEGORY_ICONS = {
    "концерт": "🎵",
    "спектакль": "🎭",
    "выставка": "🖼",
    "фестиваль": "🎪",
    "кино": "🎬",
    "экскурсия": "🚶",
    "другое": "✨",
}


def category_badge(category: str) -> str:
    cat_low = (category or "другое").lower()
    icon = CATEGORY_ICONS.get(cat_low, "✨")
    return f"{icon} {cat_low.capitalize()}"


def format_datetime_ru(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "не указано"
    try:
        dt = datetime.fromisoformat(iso_str)
        wd = WEEKDAYS_SHORT.get(dt.weekday(), "")
        month_name = MONTHS_GENITIVE.get(dt.month, str(dt.month))
        return f"{dt.day} {month_name} {dt.year} ({wd}), {dt.strftime('%H:%M')}"
    except ValueError:
        return iso_str


def format_price_ru(ev: EventRecord) -> str:
    if ev.is_free:
        return "🆓 Бесплатно"
    if ev.price_min is None and ev.price_max is None:
        return "Стоимость уточняется"
    if ev.price_min is None:
        return f"до {ev.price_max} ₽"
    if ev.price_max is None:
        return f"от {ev.price_min} ₽"
    if ev.price_min == ev.price_max:
        return f"{ev.price_min} ₽"
    if ev.price_min == 0 and ev.price_max > 0:
        return f"до {ev.price_max} ₽"
    return f"от {ev.price_min} до {ev.price_max} ₽"


def format_filter_summary_block(filters: Optional[SearchFilters], matching_count: Optional[int] = None) -> str:
    f = filters or SearchFilters()
    city_str = f.city or "Вся республика"
    cat_str = f.category.capitalize() if f.category else "Все категории"
    if f.date_preset:
        preset_map = {
            "today": "Сегодня",
            "tomorrow": "Завтра",
            "weekend": "На выходных",
            "week": "На неделю",
            "next_week": "Следующая неделя",
            "month": "До конца месяца",
            "friday": "Пятница",
            "saturday": "Суббота",
            "sunday": "Воскресенье",
        }
        date_str = preset_map.get(f.date_preset, f.date_preset)
    elif f.date_from:
        date_str = f.date_from[:10]
    else:
        date_str = "Любая дата"

    price_str = "Бесплатно" if f.is_free_only else (f"до {f.max_price} ₽" if f.max_price is not None else "Любая")
    age_str = f"до {f.max_age_limit}+" if f.max_age_limit is not None else "Любой"

    extra = []
    if f.query_text:
        extra.append(f"Текст: «{html.escape(f.query_text)}»")
    if f.language:
        extra.append(f"Язык: {html.escape(f.language)}")
    if f.time_after:
        extra.append(f"После {f.time_after}")
    if f.time_before:
        extra.append(f"До {f.time_before}")
    if f.max_duration_min:
        extra.append(f"Не дольше {f.max_duration_min} мин")

    extra_line = f"\n🔎 {' • '.join(extra)}" if extra else ""
    count_line = f"\n📊 Подходящих событий в базе: <b>{matching_count}</b>" if matching_count is not None else ""

    return (
        f"<blockquote>"
        f"🏙 <b>Город:</b> {html.escape(city_str)}  |  🎭 <b>Категория:</b> {html.escape(cat_str)}\n"
        f"📅 <b>Дата:</b> {html.escape(date_str)}  |  💳 <b>Цена:</b> {price_str}  |  👶 <b>Возраст:</b> {age_str}"
        f"{extra_line}{count_line}"
        f"</blockquote>"
    )


def format_welcome_message(filters: Optional[SearchFilters] = None, matching_count: Optional[int] = None) -> str:
    """Приветствие /start без 'простыни' текста и с явной пометкой статуса сервиса (Раздел 6.2, 11 ТЗ)."""
    summary = format_filter_summary_block(filters, matching_count)
    return (
        "🏛 <b>Афиша культурных мероприятий Башкортостана</b>\n"
        "<i>Информационный сервис (отдельный демонстрационный контур, не является официальным сайтом Минкультуры РБ).</i>\n\n"
        f"{summary}\n\n"
        "✍️ <b>Напишите запрос в свободной форме</b> (например: <i>«Куда сходить с ребёнком в субботу в Стерлитамаке до 500 рублей»</i>) "
        "или настройте фильтры кнопками:"
    )


def format_help_message() -> str:
    return (
        "ℹ️ <b>Справка по сервису «Афиша культуры Башкортостана»</b>\n\n"
        "• <b>Навигация в одном окне</b>: кнопки меню плавно обновляют текущее сообщение без засорения чата.\n"
        "• <b>Поиск свободным текстом</b>: отправьте сообщение в любой момент (возвращаться в меню не нужно). Бот понимает города (даже с опечатками), даты, бюджет «на двоих», возраст ребёнка и язык мероприятия:\n"
        "  — <i>Что интересного в Уфе в эти выходные?</i>\n"
        "  — <i>Бесплатные выставки в Уфе до конца месяца</i>\n"
        "  — <i>Куда пойти в субботу с ребёнком 8 лет в Стерлитамаке до 1000 рублей на двоих</i>\n\n"
        "• <b>Основные команды</b>:\n"
        "  /start — главное меню и сводка фильтров\n"
        "  /subscriptions — управление подписками\n"
        "  /unsubscribe — отписаться от всех рассылок одной командой\n"
        "  /cancel — сбросить все активные фильтры\n"
        "  /help — эта справка"
    )


def format_event_card(ev: EventRecord) -> str:
    """
    Полная карточка события (Раздел 6.2 ТЗ):
    название, категория, дата и время, город и площадка, адрес, описание,
    цена или «бесплатно», возрастное ограничение, ссылка на источник и билеты.
    При статусе 'отменено' выводится явная пометка (Раздел 6.1 ТЗ).
    """
    lines: List[str] = []
    if ev.status in ("cancelled", "отменено"):
        lines.append("⚠️ <b>ВНИМАНИЕ: МЕРОПРИЯТИЕ ОТМЕНЕНО!</b>")
        lines.append("<i>Событие снято с активной выдачи, карточка доступна только по прямой ссылке.</i>\n")
    elif ev.status in ("draft", "черновик"):
        lines.append("📝 <b>СТАТУС: ЧЕРНОВИК (НА МОДЕРАЦИИ)</b>\n")

    lines.append(f"📌 <b>{html.escape(ev.title)}</b>")
    lines.append(f"🏷 <b>Категория:</b> {category_badge(ev.category)} • <b>{ev.age_limit}+</b>")

    date_line = format_datetime_ru(ev.date_start)
    if ev.date_end and ev.date_end[:10] != ev.date_start[:10]:
        date_line += f" — до {format_datetime_ru(ev.date_end)}"
    elif ev.duration_min:
        date_line += f" (⏱ {ev.duration_min} мин)"
    lines.append(f"📅 <b>Дата и время:</b> {date_line}")

    lines.append(f"🏛 <b>Город и площадка:</b> г. {html.escape(ev.city)}, {html.escape(ev.venue)}")
    lines.append(f"📍 <b>Адрес:</b> {html.escape(ev.address)}")
    lines.append(f"💳 <b>Стоимость:</b> {format_price_ru(ev)}")
    lines.append(f"🔞 <b>Возрастное ограничение:</b> {ev.age_limit}+")
    if ev.language:
        lines.append(f"🗣 <b>Язык:</b> {html.escape(ev.language)}")
    if ev.tags:
        tags_str = ", ".join(f"#{html.escape(t)}" for t in ev.tags[:6])
        lines.append(f"🏷 <b>Теги:</b> {tags_str}")

    lines.append(f"\n📖 <b>Описание:</b>\n{html.escape(ev.description)}")

    lines.append(f"\n🔗 <b>Источник:</b> {html.escape(ev.source_url)}")
    if ev.org_link:
        lines.append(f"🎟 <b>Билеты / Организатор:</b> {html.escape(ev.org_link)}")

    if ev.is_disputed and ev.dispute_reason:
        lines.append(f"\n🔎 <i>Отметка модерации: {html.escape(ev.dispute_reason)}</i>")

    lines.append(f"\n<code>Прямая команда: /event_{ev.id}</code>")
    return "\n".join(lines)


def format_search_results(
    events: List[EventRecord],
    total: int,
    filters: SearchFilters,
    empty_explanation: Optional[str] = None,
) -> str:
    if total == 0 or not events:
        return empty_explanation or "По вашему запросу подходящих событий не найдено."

    start_num = filters.offset + 1
    end_num = filters.offset + len(events)
    total_pages = max(1, (total + filters.limit - 1) // filters.limit)
    curr_page = (filters.offset // filters.limit) + 1

    header = (
        f"🔎 <b>Найдено событий: {total}</b>  <i>(стр. {curr_page} из {total_pages}, показаны {start_num}–{end_num})</i>\n"
        f"<blockquote>Условия: {html.escape(filters.describe_human())}</blockquote>"
    )
    items: List[str] = [header]
    for idx, ev in enumerate(events, start=start_num):
        price_str = format_price_ru(ev)
        dt_str = format_datetime_ru(ev.date_start)
        badge = category_badge(ev.category)
        items.append(
            f"<b>{idx}. {html.escape(ev.title)}</b> ({ev.age_limit}+)\n"
            f"   {badge} • 💳 <b>{price_str}</b>\n"
            f"   📅 {dt_str}\n"
            f"   📍 г. {html.escape(ev.city)}, {html.escape(ev.venue)}"
        )
    items.append("👇 <i>Выберите событие кнопкой ниже для просмотра карточки и ссылок на билеты:</i>")
    return "\n\n".join(items)


def format_subscriptions_list(subs: List[Dict[str, Any]]) -> str:
    if not subs:
        return (
            "🔔 <b>Мои подписки</b>\n\n"
            "У вас пока нет активных подписок.\n"
            "Вы можете подписаться на любой город и категорию прямо из результатов поиска или карточки события — "
            "нажмите кнопку <b>«🔔 Подписаться»</b>."
        )
    lines = [
        f"🔔 <b>Ваши активные подписки ({len(subs)}):</b>\n"
        "<i>Никаких навязчивых уведомлений: вы управляете каждым фильтром вручную.</i>\n"
    ]
    for idx, s in enumerate(subs, start=1):
        exp = (s.get("expires_at") or "")[:10]
        lines.append(
            f"<b>{idx}.</b> 🏙 Город: <b>{html.escape(s['city'])}</b>  |  🎭 Категория: <b>{html.escape(s['category'])}</b>\n"
            f"   <i>Срок хранения до: {exp}</i>"
        )
    lines.append("\nНажмите на подписку ниже, чтобы удалить её, или отправьте /unsubscribe для отписки от всех сразу.")
    return "\n".join(lines)


def format_import_report(rep: ImportReport) -> str:
    lines = [
        f"📊 <b>Отчёт об импорте файла «{html.escape(rep.source_file)}»</b>",
        f"• Всего записей обработано: <b>{rep.total_rows}</b>",
        f"• Добавлено новых: <b>{rep.added_count}</b>",
        f"• Обновлено существующих: <b>{rep.updated_count}</b>",
        f"• Без изменений (идемпотентные дубли): <b>{rep.unchanged_count}</b>",
        f"• Отклонено из-за ошибок валидации: <b>{rep.rejected_count}</b>",
        f"• Помечено как спорные карточки: <b>{rep.disputed_count}</b>",
    ]
    if rep.rejected_reasons:
        lines.append("\n❌ <b>Причины отклонения (первые 5):</b>")
        for r in rep.rejected_reasons[:5]:
            lines.append(f"  — Строка {r['row']} ({html.escape(str(r['title']))}): {html.escape(str(r['reason']))}")
    if rep.disputed_items:
        lines.append("\n⚠️ <b>Спорные карточки на проверку (первые 5):</b>")
        for d in rep.disputed_items[:5]:
            lines.append(f"  — Строка {d['row']} ({html.escape(str(d['title']))}): {html.escape(str(d['reason']))}")
    return "\n".join(lines)
