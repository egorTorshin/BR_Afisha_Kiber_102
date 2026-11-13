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


def contact_keyboard(language: str = "ru") -> Dict[str, Any]:
    return {"keyboard": [[{"text": t(language, "send_phone"), "request_contact": True}]], "resize_keyboard": True,
            "one_time_keyboard": True}


def location_keyboard(language: str = "ru") -> Dict[str, Any]:
    return {"keyboard": [[{"text": t(language, "send_location"), "request_location": True}]], "resize_keyboard": True,
            "one_time_keyboard": True}


def remove_reply_keyboard() -> Dict[str, Any]:
    return {"remove_keyboard": True}


def profile_keyboard(profile: Dict[str, Any]) -> Dict[str, Any]:
    language = profile.get("language")
    rows = [
        [{"text": t(language, "edit_name"), "callback_data": "profile:edit:name"}, {"text": t(language, "edit_phone"), "callback_data": "profile:edit:phone"}],
        [{"text": t(language, "edit_city"), "callback_data": "profile:edit:city"}, {"text": t(language, "favorites"), "callback_data": "profile:categories"}],
        [{"text": t(language, "change_language"), "callback_data": "profile:language"}],
        [{"text": t(language, "location"), "callback_data": "profile:location"}],
        [{"text": t(language, "delete"), "callback_data": "profile:delete_confirm"}],
        [{"text": t(language, "back"), "callback_data": "menu:main"}],
    ]
    return {"inline_keyboard": rows}


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


def home_keyboard(language: str = "ru") -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": t(language, "profile"), "callback_data": "profile:open"}, {"text": t(language, "leisure"), "callback_data": "leisure:start"}],
        [{"text": t(language, "location"), "callback_data": "profile:location"}],
    ]}


def leisure_categories_keyboard(selected: List[str], language: str = "ru") -> Dict[str, Any]:
    cats = _category_rows(language)
    rows = [[{"text": ("✅ " if code in selected else "") + label,
              "callback_data": f"leisure:cat:{code}"}] for label, code in cats]
    rows.append([{"text": t(language, "manual_type"), "callback_data": "leisure:category_manual"}])
    rows.append([{"text": t(language, "done"), "callback_data": "leisure:categories_done"}])
    return {"inline_keyboard": rows}


def leisure_search_keyboard(filters: SearchFilters, language: str = "ru") -> Dict[str, Any]:
    city = filters.city or t(language, "choose")
    date = filters.date_from[:10] if filters.date_from else t(language, "choose")
    categories = ", ".join(filters.categories) if filters.categories else t(language, "choose")
    return {"inline_keyboard": [
        [{"text": f"🏙 {t(language, 'city_label')}: {city}", "callback_data": "leisure:city_menu"}],
        [{"text": f"📅 {t(language, 'date')}: {date}", "callback_data": "leisure:date_menu"}],
        [{"text": f"🎭 {t(language, 'leisure_type')}: {categories}", "callback_data": "leisure:category_menu"}],
        [{"text": t(language, "search"), "callback_data": "leisure:run"}],
    ]}


def leisure_cities_keyboard(language: str = "ru") -> Dict[str, Any]:
    cities = ["Уфа", "Стерлитамак", "Салават", "Нефтекамск", "Октябрьский", "Сибай"]
    rows = [[{"text": city, "callback_data": f"leisure:city:{city}"}] for city in cities]
    rows.append([{"text": t(language, "manual_city"), "callback_data": "leisure:city_manual"}])
    rows.append([{"text": t(language, "search_settings"), "callback_data": "leisure:form"}])
    return {"inline_keyboard": rows}


def leisure_dates_keyboard(language: str = "ru") -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": t(language, "today"), "callback_data": "leisure:date:today"}, {"text": t(language, "tomorrow"), "callback_data": "leisure:date:tomorrow"}],
        [{"text": t(language, "weekend"), "callback_data": "leisure:date:weekend"}, {"text": t(language, "week"), "callback_data": "leisure:date:week"}],
        [{"text": t(language, "manual_date"), "callback_data": "leisure:date_manual"}],
        [{"text": t(language, "search_settings"), "callback_data": "leisure:form"}],
    ]}


def main_menu_keyboard(
    filters: Optional[SearchFilters] = None,
    matching_count: Optional[int] = None,
    subs_count: int = 0,
) -> Dict[str, Any]:
    f = filters or SearchFilters()
    city_label = f"🏙 Город: {f.city} ✅" if f.city else "🏙 Выбрать город"
    cat_label = f"🎭 {f.category.capitalize()} ✅" if f.category else "🎭 Категория"

    if f.date_preset:
        date_label = f"📅 Период: {f.date_preset} ✅"
    elif f.date_from:
        date_label = f"📅 {f.date_from[:10]} ✅"
    else:
        date_label = "📅 Выбрать дату"

    if f.is_free_only:
        price_label = "💳 Бесплатно ✅"
    elif f.max_price is not None:
        price_label = f"💳 До {f.max_price} ₽ ✅"
    else:
        price_label = "💳 Любая цена"

    age_label = f"👶 Возраст: до {f.max_age_limit}+ ✅" if f.max_age_limit is not None else "👶 Возраст (0+..18+)"
    search_btn_text = (
        f"🔎 Показать события ({matching_count})"
        if matching_count is not None
        else "🔎 Показать события"
    )
    subs_btn_text = f"🔔 Мои подписки ({subs_count})" if subs_count > 0 else "🔔 Мои подписки"

    rows: List[List[Dict[str, str]]] = [
        [{"text": search_btn_text, "callback_data": "search:run"}],
        [
            {"text": city_label, "callback_data": "menu:cities"},
            {"text": cat_label, "callback_data": "menu:categories"},
        ],
        [
            {"text": date_label, "callback_data": "menu:dates"},
            {"text": price_label, "callback_data": "menu:prices"},
        ],
        [
            {"text": age_label, "callback_data": "menu:ages"},
            {"text": "🆓 Только бесплатные", "callback_data": "price:free"},
        ],
        [
            {"text": "🔥 На выходных", "callback_data": "preset:weekend"},
            {"text": "🧒 С детьми (0+/6+)", "callback_data": "preset:kids"},
        ],
        [
            {"text": subs_btn_text, "callback_data": "menu:subs"},
            {"text": "🔄 Сбросить фильтры", "callback_data": "menu:reset"},
        ],
        [
            {"text": "ℹ️ Справка и примеры запросов", "callback_data": "menu:help"},
        ],
    ]
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
    rows.append([{"text": "⬅️ Назад в главное меню", "callback_data": "menu:main"}])
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
    rows.append([{"text": "⬅️ Назад в главное меню", "callback_data": "menu:main"}])
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
                {"text": "⬅️ Назад в меню", "callback_data": "menu:main"},
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
                {"text": "⬅️ Назад в меню", "callback_data": "menu:main"},
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
                {"text": "⬅️ Назад в меню", "callback_data": "menu:main"},
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
        return {"inline_keyboard": [[{"text": t(language, "new_search"), "callback_data": "leisure:start"}]]}

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

    rows.append([{"text": t(language, "new_search"), "callback_data": "leisure:start"}])
    return {"inline_keyboard": rows}


def agent_results_keyboard(cards: List[AgentEventCard]) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for idx, card in enumerate(cards, start=1):
        title = card.title if len(card.title) <= 34 else card.title[:31] + "..."
        rows.append([{"text": f"📌 {idx}. {title}", "callback_data": f"card:{card.id}"}])
    rows.append([
        {"text": "🔎 Новый запрос", "callback_data": "menu:main"},
        {"text": "🏠 Главное меню", "callback_data": "menu:main"},
    ])
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

    rows.append([
        {"text": t(language, "back_list"), "callback_data": "search:run"},
    ])
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
