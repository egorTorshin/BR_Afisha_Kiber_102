from typing import Any, Dict, List, Optional
from src.models.schemas import EventRecord, SearchFilters
from src.bot.i18n import t
from src.agent.schemas import AgentEventCard


def language_keyboard() -> Dict[str, Any]:
    # One language per row makes the button clearly belong to its language.
    return {"inline_keyboard": [
        [{"text": "Русский", "callback_data": "lang:ru"}],
        [{"text": "Башҡортса", "callback_data": "lang:ba"}],
        [{"text": "English", "callback_data": "lang:en"}],
    ]}


def location_keyboard(language: str = "ru") -> Dict[str, Any]:
    return {"keyboard": [[{"text": t(language, "send_location"), "request_location": True}]], "resize_keyboard": True,
            "one_time_keyboard": True}


def remove_reply_keyboard() -> Dict[str, Any]:
    return {"remove_keyboard": True}


def profile_keyboard(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "🇷🇺 Русский", "callback_data": "profile_lang:ru"},
         {"text": "🇧🇦 Башҡортса", "callback_data": "profile_lang:ba"},
         {"text": "🇬🇧 English", "callback_data": "profile_lang:en"}],
        [{"text": "📍 Геолокация (сортировка рядом)", "callback_data": "profile:location"},
         {"text": "⭐ Любимые категории", "callback_data": "profile:categories"}],
        [{"text": "⬅️ В главное меню", "callback_data": "menu:main"}],
    ]}


def delete_account_keyboard(language: str = "ru") -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": t(language, "delete_yes"), "callback_data": "profile:delete"}],
        [{"text": t(language, "delete_no"), "callback_data": "profile:open"}],
    ]}


def _category_rows(language: str) -> List[tuple[str, str]]:
    labels = {
        "ru": ["Концерт", "Театр", "Кино", "Экскурсия", "Выставка", "Фестиваль"],
        "en": ["Concert", "Theatre", "Cinema", "Tour", "Exhibition", "Festival"],
        "ba": ["Концерт", "Театр", "Кино", "Экскурсия", "Күргәҙмә", "Фестиваль"],
    }.get(language, []) or ["Концерт", "Театр", "Кино", "Экскурсия", "Выставка", "Фестиваль"]
    codes = ["концерт", "спектакль", "кино", "экскурсия", "выставка", "фестиваль"]
    icons = ["🎵", "🎭", "🎬", "🚶", "🖼", "🎪"]
    return [(f"{icon} {label}", code) for icon, label, code in zip(icons, labels, codes)]


def profile_categories_keyboard(selected: List[str], language: str = "ru") -> Dict[str, Any]:
    cats = _category_rows(language)
    rows = [[{"text": ("✅ " if code in selected else "") + label, "callback_data": f"profile:cat:{code}"}]
            for label, code in cats]
    rows.append([{"text": t(language, "done"), "callback_data": "profile:done"}])
    return {"inline_keyboard": rows}


def main_menu_keyboard(
    filters: Optional[SearchFilters] = None,
    matching_count: Optional[int] = None,
    subs_count: int = 0,
    language: str = "ru",
) -> Dict[str, Any]:
    rows = [
        [{"text": "🔎 События", "callback_data": "search:run"},
         {"text": "⚙️ Фильтры", "callback_data": "menu:filters"}],
        [{"text": "🌐 Язык / 📍 Гео", "callback_data": "profile:open"},
         {"text": "🔔 Подписки", "callback_data": "menu:subs"}],
        [{"text": "✨ Для вас", "callback_data": "preset:for_you"}],
    ]
    if filters is not None and not filters.is_empty():
        rows[-1].append({"text": "🔄 Сбросить фильтры", "callback_data": "menu:reset"})
    return {"inline_keyboard": rows}


def filters_keyboard(filters: SearchFilters) -> Dict[str, Any]:
    """Keep manual filters on one screen; every choice opens results immediately."""
    rows = [
        [{"text": "🏙 Город", "callback_data": "menu:cities"},
         {"text": "🎭 Категория", "callback_data": "menu:categories"},
         {"text": "📅 Дата", "callback_data": "menu:dates"}],
        [{"text": "💳 Цена", "callback_data": "menu:prices"},
         {"text": "👶 Возраст", "callback_data": "menu:ages"}],
        [{"text": "🔥 Выходные", "callback_data": "preset:weekend"},
         {"text": "🆓 Бесплатно", "callback_data": "price:free"},
         {"text": "🧒 Детям", "callback_data": "preset:kids"}],
    ]
    extras = (
        ("query_text", "🔓 Убрать тему"),
        ("time_after", "🔓 Время начала"),
        ("time_before", "🔓 Время окончания"),
        ("max_duration_min", "🔓 Длительность"),
        ("language", "🔓 Язык события"),
        ("venue", "🔓 Площадка"),
    )
    extra_buttons = [{"text": label, "callback_data": f"filter_clear:{field}"}
                     for field, label in extras if getattr(filters, field) is not None]
    rows.extend(extra_buttons[index:index + 2] for index in range(0, len(extra_buttons), 2))
    rows.append([{"text": "🔎 Показать", "callback_data": "search:run"},
                 *([{"text": "🔄 Сбросить", "callback_data": "menu:reset"}] if not filters.is_empty() else []),
                 {"text": "🏠 Меню", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}


def cities_keyboard(
    city_rows: List[Dict[str, Any]],
    selected_city: Optional[str] = None,
) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    if not city_rows:
        city_rows = [
            {"city": "Уфа", "cnt": 0},
            {"city": "Стерлитамак", "cnt": 0},
            {"city": "Салават", "cnt": 0},
            {"city": "Нефтекамск", "cnt": 0},
            {"city": "Сибай", "cnt": 0},
        ]

    pair: List[Dict[str, str]] = []
    for item in city_rows[:10]:
        c_name = item["city"]
        cnt = item.get("cnt", 0)
        is_sel = selected_city and selected_city.lower() == c_name.lower()
        prefix = "✅ " if is_sel else "🏙 "
        suffix = f" ({cnt})" if cnt > 0 else ""
        pair.append({"text": f"{prefix}{c_name}{suffix}", "callback_data": f"city:{c_name}"})
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    all_prefix = "✅ " if not selected_city else "🌍 "
    rows.append([{"text": f"{all_prefix}Все города Башкортостана", "callback_data": "city:all"}])
    rows.append([{"text": "⬅️ К фильтрам", "callback_data": "menu:filters"}])
    return {"inline_keyboard": rows}


def categories_keyboard(
    cat_counts: Optional[Dict[str, int]] = None,
    selected_category: Optional[str] = None,
) -> Dict[str, Any]:
    counts = cat_counts or {}
    cats = [
        ("🎵 Концерт", "концерт"),
        ("🎭 Спектакль", "спектакль"),
        ("🖼 Выставка", "выставка"),
        ("🎪 Фестиваль", "фестиваль"),
        ("🎬 Кино", "кино"),
        ("🚶 Экскурсия", "экскурсия"),
        ("✨ Другое", "другое"),
    ]
    rows: List[List[Dict[str, str]]] = []
    pair: List[Dict[str, str]] = []
    for label, code in cats:
        is_sel = selected_category and selected_category.lower() == code
        cnt = counts.get(code, 0)
        cnt_str = f" ({cnt})" if cnt > 0 else ""
        text_label = f"✅ {label[2:].strip()}{cnt_str}" if is_sel else f"{label}{cnt_str}"
        pair.append({"text": text_label, "callback_data": f"cat:{code}"})
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    all_prefix = "✅ " if not selected_category else "📋 "
    rows.append([{"text": f"{all_prefix}Все категории", "callback_data": "cat:all"}])
    rows.append([{"text": "⬅️ К фильтрам", "callback_data": "menu:filters"}])
    return {"inline_keyboard": rows}


def dates_keyboard(selected_preset: Optional[str] = None) -> Dict[str, Any]:
    def mark(label: str, code: str) -> str:
        return f"✅ {label}" if selected_preset == code else label

    return {
        "inline_keyboard": [
            [
                {"text": mark("Сегодня", "today"), "callback_data": "date:today"},
                {"text": mark("Завтра", "tomorrow"), "callback_data": "date:tomorrow"},
            ],
            [
                {"text": mark("🔥 На выходных", "weekend"), "callback_data": "date:weekend"},
                {"text": mark("На 7 дней", "week"), "callback_data": "date:week"},
            ],
            [
                {"text": mark("До конца месяца", "month"), "callback_data": "date:month"},
                {"text": "📆 Указать дату (ДД.ММ)", "callback_data": "date:custom"},
            ],
            [
                {"text": "🗓 Любая дата", "callback_data": "date:all"},
                {"text": "⬅️ К фильтрам", "callback_data": "menu:filters"},
            ],
        ]
    }


def prices_keyboard(filters: Optional[SearchFilters] = None) -> Dict[str, Any]:
    f = filters or SearchFilters()
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Бесплатно" if f.is_free_only else "🆓 Бесплатно", "callback_data": "price:free"},
                {"text": "✅ До 500 ₽" if (not f.is_free_only and f.max_price == 500) else "До 500 ₽", "callback_data": "price:500"},
            ],
            [
                {"text": "✅ До 1000 ₽" if f.max_price == 1000 else "До 1000 ₽", "callback_data": "price:1000"},
                {"text": "✅ До 1500 ₽" if f.max_price == 1500 else "До 1500 ₽", "callback_data": "price:1500"},
            ],
            [
                {"text": "✅ Любая цена" if (not f.is_free_only and f.max_price is None) else "💳 Любая цена", "callback_data": "price:all"},
                {"text": "⬅️ К фильтрам", "callback_data": "menu:filters"},
            ],
        ]
    }


def ages_keyboard(selected_age: Optional[int] = None) -> Dict[str, Any]:
    def mark(label: str, age_val: Optional[int]) -> str:
        return f"✅ {label}" if selected_age == age_val else label

    return {
        "inline_keyboard": [
            [
                {"text": mark("0+ (Малыши)", 0), "callback_data": "age:0"},
                {"text": mark("6+ (Школьники)", 6), "callback_data": "age:6"},
            ],
            [
                {"text": mark("12+ (Подростки)", 12), "callback_data": "age:12"},
                {"text": mark("16+", 16), "callback_data": "age:16"},
            ],
            [
                {"text": mark("18+ (Без ограничений)", None), "callback_data": "age:all"},
                {"text": "⬅️ К фильтрам", "callback_data": "menu:filters"},
            ],
        ]
    }


def search_results_keyboard(
    events: List[EventRecord],
    total: int,
    filters: SearchFilters,
    is_subscribed: bool = False,
    language: str = "ru",
) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []

    if total == 0:
        relax = []
        if filters.date_preset or filters.date_from or filters.date_to:
            relax.append({"text": "🔓 Любая дата", "callback_data": "date:all"})
        if filters.is_free_only or filters.max_price is not None:
            relax.append({"text": "🔓 Любая цена", "callback_data": "price:all"})
        if relax:
            rows.append(relax)
        relax = []
        if filters.category:
            relax.append({"text": "🔓 Все категории", "callback_data": "cat:all"})
        if filters.city:
            relax.append({"text": "🌍 Все города РБ", "callback_data": "city:all"})
        if relax:
            rows.append(relax)
        rows.append([{"text": "⚙️ Фильтры", "callback_data": "menu:filters"},
                     {"text": "🏠 Меню", "callback_data": "menu:main"}])
        return {"inline_keyboard": rows}

    # Кнопки быстрого перехода к карточке каждого события (не более 3 действий до карточки!)
    for idx, ev in enumerate(events, start=filters.offset + 1):
        short_title = ev.title if len(ev.title) <= 34 else ev.title[:31] + "..."
        rows.append([
            {"text": f"📌 {idx}. {short_title}", "callback_data": f"card:{ev.id}"}
        ])

    # Пагинация / кнопка «Показать ещё»
    nav_row: List[Dict[str, str]] = []
    total_pages = max(1, (total + filters.limit - 1) // filters.limit)
    curr_page = (filters.offset // filters.limit) + 1
    if filters.offset > 0:
        prev_offset = max(0, filters.offset - filters.limit)
        nav_row.append({"text": "⬅️ Назад", "callback_data": f"page:{prev_offset}"})
    if total_pages > 1:
        nav_row.append({"text": f"📄 {curr_page}/{total_pages}", "callback_data": f"page:{filters.offset}"})
    if filters.offset + len(events) < total:
        next_offset = filters.offset + filters.limit
        nav_row.append({"text": t(language, "more"), "callback_data": f"page:{next_offset}"})
    if nav_row:
        rows.append(nav_row)

    sub_city = filters.city or "Все города"
    sub_cat = filters.category or "Все категории"
    rows.append([{"text": "⚙️ Фильтры", "callback_data": "menu:filters"},
                 {"text": "🔕 Отписаться" if is_subscribed else "🔔 Подписаться",
                  "callback_data": f"sub_toggle:{sub_city}:{sub_cat}"},
                 {"text": "🏠 Меню", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}


def agent_results_keyboard(cards: List[AgentEventCard], show_filters: bool = False) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for idx, card in enumerate(cards, start=1):
        title = card.title if len(card.title) <= 34 else card.title[:31] + "..."
        rows.append([{"text": f"📌 {idx}. {title}", "callback_data": f"card:{card.id}"}])
    rows.append(([{"text": "⚙️ Изменить фильтры", "callback_data": "menu:filters"}] if show_filters else []) +
                [{"text": "🏠 Меню", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}


def event_card_keyboard(ev: EventRecord, is_subscribed: bool = False, language: str = "ru") -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    url_row: List[Dict[str, str]] = []
    if ev.org_link and (ev.org_link.startswith("http://") or ev.org_link.startswith("https://")):
        url_row.append({"text": t(language, "tickets"), "url": ev.org_link})
    if url_row:
        rows.append(url_row)

    if ev.source_url and (ev.source_url.startswith("http://") or ev.source_url.startswith("https://")):
        rows.append([{"text": t(language, "source"), "url": ev.source_url}])

    rows.append([{"text": "🔕 Отписаться" if is_subscribed else "🔔 Подписаться",
                  "callback_data": f"sub_toggle:{ev.city}:{ev.category}"}])
    rows.append([{"text": "⬅️ К списку", "callback_data": "search:run"},
                 {"text": "🏠 Меню", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}


def subscriptions_keyboard(subs: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for idx, s in enumerate(subs[:8], start=1):
        rows.append([
            {
                "text": f"🔎 {s['city']} • {s['category']}",
                "callback_data": f"sub_search:{s['city']}:{s['category']}",
            },
            {
                "text": f"❌ Удалить №{idx}",
                "callback_data": f"unsub_id:{s['id'][:12]}",
            },
        ])
    if subs:
        rows.append([{"text": "🗑 Отписаться от всех (/unsubscribe)", "callback_data": "unsub:all"}])
    rows.append([{"text": "⬅️ В главное меню", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}
