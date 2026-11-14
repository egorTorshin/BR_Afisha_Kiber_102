import calendar
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from src.db.connection import Database
from src.models.schemas import SearchFilters, VALID_AGE_LIMITS, VALID_CATEGORIES


def normalize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = text.lower().strip()
    cleaned = cleaned.replace("ё", "е")
    cleaned = re.sub(r"[«»\x22\x27„“”()\[\],.!?;:—–-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def compute_dedup_key(title: str, date_start: str, city: str, venue: str) -> str:
    norm_title = normalize_text(title)
    norm_city = normalize_text(city)
    norm_venue = normalize_text(venue)
    day_part = (date_start or "")[:16]  # YYYY-MM-DDTHH:MM
    raw = f"{norm_title}|{day_part}|{norm_city}|{norm_venue}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def compute_source_hash(payload: Dict[str, Any]) -> str:
    canonical = {
        "title": normalize_text(str(payload.get("title", ""))),
        "date_start": str(payload.get("date_start", "")).strip(),
        "city": normalize_text(str(payload.get("city", ""))),
        "venue": normalize_text(str(payload.get("venue", ""))),
        "source_url": str(payload.get("source_url", "")).strip(),
    }
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous_row = list(range(len(b) + 1))
    for i, c1 in enumerate(a):
        current_row = [i + 1]
        for j, c2 in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _damerau_levenshtein(a: str, b: str) -> int:
    """Расстояние Дамерау — Левенштейна (учитывает транспозицию соседних символов, например 'стерлитакмае')."""
    d: Dict[Tuple[int, int], int] = {}
    lenstr1 = len(a)
    lenstr2 = len(b)
    for i in range(-1, lenstr1 + 1):
        d[(i, -1)] = i + 1
    for j in range(-1, lenstr2 + 1):
        d[(-1, j)] = j + 1
    for i in range(lenstr1):
        for j in range(lenstr2):
            cost = 0 if a[i] == b[j] else 1
            d[(i, j)] = min(
                d[(i - 1, j)] + 1,
                d[(i, j - 1)] + 1,
                d[(i - 1, j - 1)] + cost,
            )
            if i and j and a[i] == b[j - 1] and a[i - 1] == b[j]:
                d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + cost)
    return d[lenstr1 - 1, lenstr2 - 1]


def resolve_city(raw_city: str, db: Optional[Database] = None) -> Optional[str]:
    if not raw_city:
        return None
    norm = normalize_text(raw_city)
    if norm.startswith("г "):
        norm = norm[2:].strip()
    elif norm.startswith("город "):
        norm = norm[6:].strip()

    city_records: List[Dict[str, Any]] = []
    if db is not None:
        city_records = db.fetchall("SELECT name, normalized_name, aliases_json FROM cities")

    if not city_records:
        city_records = [
            {"name": "Уфа", "normalized_name": "уфа", "aliases_json": '["уфе","уфу","ufa"]'},
            {"name": "Стерлитамак", "normalized_name": "стерлитамак", "aliases_json": '["стерлитамаке","стерлитакмае","стерлитакмак"]'},
            {"name": "Салават", "normalized_name": "салават", "aliases_json": '["салавате","салавата"]'},
            {"name": "Нефтекамск", "normalized_name": "нефтекамск", "aliases_json": '["нефтекамске"]'},
            {"name": "Октябрьский", "normalized_name": "октябрьский", "aliases_json": '["октябрьском"]'},
            {"name": "Белорецк", "normalized_name": "белорецк", "aliases_json": '["белорецке"]'},
            {"name": "Бирск", "normalized_name": "бирск", "aliases_json": '["бирске"]'},
            {"name": "Туймазы", "normalized_name": "туймазы", "aliases_json": '["туймазах"]'},
            {"name": "Сибай", "normalized_name": "сибай", "aliases_json": '["сибае"]'},
            {"name": "Ишимбай", "normalized_name": "ишимбай", "aliases_json": '["ишимбае"]'},
        ]

    # 1. Exact or alias match
    for rec in city_records:
        if norm == rec["normalized_name"]:
            return rec["name"]
        aliases = [normalize_text(x) for x in json.loads(rec.get("aliases_json") or "[]")]
        if norm in aliases:
            return rec["name"]

    # 2. Fuzzy match (Damerau-Levenshtein <= 2 for words >= 5 chars)
    if len(norm) >= 4:
        best_city = None
        best_dist = 999
        for rec in city_records:
            candidates = [rec["normalized_name"]] + [
                normalize_text(x) for x in json.loads(rec.get("aliases_json") or "[]")
            ]
            for cand in candidates:
                if abs(len(norm) - len(cand)) > 2:
                    continue
                dist = _damerau_levenshtein(norm, cand)
                max_allowed = 2 if len(cand) >= 6 else 1
                if dist <= max_allowed and dist < best_dist:
                    best_dist = dist
                    best_city = rec["name"]
        if best_city:
            return best_city

    return None


def classify_geography(raw_value: str, db: Optional[Database] = None) -> Tuple[str, Optional[str]]:
    """Classify a mention without coercing regions or unknown places into cities."""
    norm = normalize_text(raw_value)
    if not norm:
        return "unknown", None
    region_names = {
        "башкортостан", "башкортостане", "республика башкортостан",
        "республике башкортостан", "республику башкортостан",
        "bashkortostan", "republic of bashkortostan",
    }
    if norm in region_names:
        return "region", None
    city = resolve_city(raw_value, db=db)
    if city:
        return "city", city
    return "unknown", raw_value.strip()


def resolve_category(raw_cat: str, db: Optional[Database] = None) -> Optional[str]:
    if not raw_cat:
        return None
    norm = normalize_text(raw_cat)
    if norm in VALID_CATEGORIES:
        return norm

    cat_records: List[Dict[str, Any]] = []
    if db is not None:
        cat_records = db.fetchall("SELECT code, synonyms_json FROM categories")

    for rec in cat_records:
        code = rec["code"]
        if norm == code:
            return code
        syns = [normalize_text(x) for x in json.loads(rec.get("synonyms_json") or "[]")]
        if norm in syns:
            return code

    # Fallback built-in synonyms
    builtin = {
        "концерт": ["концерты", "музыкальный вечер", "филармония", "симфонический"],
        "спектакль": ["спектакли", "театр", "балет", "опера", "драма", "комедия", "пьеса", "кукольный"],
        "выставка": ["выставки", "экспозиция", "галерея", "вернисаж", "фотовыставка"],
        "фестиваль": ["фестивали", "ярмарка", "праздник"],
        "кино": ["кинопоказ", "кинопоказы", "фильм", "сеанс"],
        "экскурсия": ["экскурсии", "прогулка", "маршрут"],
        "другое": ["мастер класс", "лекция", "встреча"],
    }
    for code, words in builtin.items():
        if norm == code or norm in words:
            return code
    return None


def parse_price_fields(raw_min: Any, raw_max: Any = None, raw_is_free: Any = None) -> Tuple[Optional[int], Optional[int], bool]:
    """Missing/unrecognised prices stay unknown; only explicit zero/free means free."""
    free = (raw_is_free.strip().lower() in ("true", "1", "yes", "да", "бесплатно")
            if isinstance(raw_is_free, str) else bool(raw_is_free))

    def amounts(value):
        if value is None or str(value).strip() == "":
            return []
        text = str(value).strip().lower().replace("\u00a0", " ")
        if re.search(r"-\s*\d", text):
            # A spaced dash between prices is a range, a leading minus is invalid.
            if text.startswith("-") or re.search(r"(?:от|до)\s*-", text):
                raise ValueError("Цена не может быть отрицательной")
        if isinstance(value, (int, float)):
            if value < 0 or not float(value).is_integer():
                raise ValueError("Цена должна быть неотрицательным целым числом")
            return [int(value)]
        text = re.sub(r"(?<=\d) (?=\d{3}(?:\D|$))", "", text)
        if re.search(r"\d[.,]\d", text):
            raise ValueError("Цена должна быть целым числом")
        return [int(n) for n in re.findall(r"\d+", text)]

    low_text = str(raw_min or "").lower()
    explicit_free = any(word in low_text for word in ("бесплатн", "свободн", "free"))
    mins, maxs = amounts(raw_min), amounts(raw_max)
    if free or explicit_free:
        if any(n > 0 for n in mins + maxs):
            raise ValueError("Бесплатный вход противоречит положительной цене")
        return 0, 0, True
    p_min = min(mins) if mins else None
    p_max = max(maxs) if maxs else (max(mins) if mins else None)
    if low_text.strip().startswith("до ") and not maxs:
        p_min = None
    if p_min is not None and p_max is not None and p_max < p_min:
        raise ValueError("price_max не может быть меньше price_min")
    if p_min == 0 and p_max == 0:
        return 0, 0, True
    return p_min, p_max, False


def parse_iso_datetime(val: Any) -> str:
    if not val:
        raise ValueError("Не указана дата начала события")
    s = str(val).strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                dt = dt.replace(hour=12, minute=0, second=0)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"Некорректный формат даты: '{s}'")


def resolve_date_preset(preset: str, ref_date: date) -> Tuple[str, str]:
    """Возвращает (date_from_iso, date_to_iso) для пресета даты относительно опорной даты."""
    preset = preset.strip().lower()
    if preset in ("today", "сегодня"):
        d_start = datetime.combine(ref_date, datetime.min.time())
        d_end = datetime.combine(ref_date, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("tomorrow", "завтра"):
        tmr = ref_date + timedelta(days=1)
        d_start = datetime.combine(tmr, datetime.min.time())
        d_end = datetime.combine(tmr, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("friday", "пятница"):
        days_ahead = (4 - ref_date.weekday()) % 7
        target = ref_date + timedelta(days=days_ahead)
        d_start = datetime.combine(target, datetime.min.time())
        d_end = datetime.combine(target, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("saturday", "суббота"):
        days_ahead = (5 - ref_date.weekday()) % 7
        target = ref_date + timedelta(days=days_ahead)
        d_start = datetime.combine(target, datetime.min.time())
        d_end = datetime.combine(target, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("sunday", "воскресенье"):
        days_ahead = (6 - ref_date.weekday()) % 7
        target = ref_date + timedelta(days=days_ahead)
        d_start = datetime.combine(target, datetime.min.time())
        d_end = datetime.combine(target, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("weekend", "выходные"):
        # Ближайшая суббота и воскресенье
        wd = ref_date.weekday()
        if wd <= 5:
            sat = ref_date + timedelta(days=(5 - wd))
            sun = sat + timedelta(days=1)
        else:
            sat = ref_date
            sun = ref_date
        d_start = datetime.combine(sat, datetime.min.time())
        d_end = datetime.combine(sun, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("week", "неделя"):
        d_start = datetime.combine(ref_date, datetime.min.time())
        d_end = datetime.combine(ref_date + timedelta(days=7), datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("next_week", "следующая неделя"):
        days_to_monday = (7 - ref_date.weekday()) % 7 or 7
        next_mon = ref_date + timedelta(days=days_to_monday)
        next_sun = next_mon + timedelta(days=6)
        d_start = datetime.combine(next_mon, datetime.min.time())
        d_end = datetime.combine(next_sun, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    if preset in ("month", "месяц"):
        last_day = calendar.monthrange(ref_date.year, ref_date.month)[1]
        end_date = date(ref_date.year, ref_date.month, last_day)
        d_start = datetime.combine(ref_date, datetime.min.time())
        d_end = datetime.combine(end_date, datetime.max.time()).replace(microsecond=0)
        return d_start.strftime("%Y-%m-%dT%H:%M:%S"), d_end.strftime("%Y-%m-%dT%H:%M:%S")

    raise ValueError(f"Неизвестный пресет периода: {preset}")


MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_free_text_query(text: str, ref_date: date, db: Optional[Database] = None) -> SearchFilters:
    """
    Разбор свободного текстового запроса пользователя на структурированные фильтры (Раздел 2, 6.2, Приложение А).
    Обрабатывает город (включая опечатки), категорию, дату/время, длительность, цену (в т.ч. 'на двоих'), возраст и язык.
    """
    filters = SearchFilters()
    if not text or not text.strip():
        return filters

    raw = text.strip()
    low = raw.lower().replace("ё", "е")
    consumed_spans: List[Tuple[int, int]] = []

    def mark_span(match_obj: re.Match) -> None:
        consumed_spans.append((match_obj.start(), match_obj.end()))

    # 1. Язык
    m_lang = re.search(r"на\s+(башкирском|русском|татарском)(\s+языке)?", low)
    if m_lang:
        mark_span(m_lang)
        lang_map = {"башкирском": "башкирский", "русском": "русский", "татарском": "татарский"}
        filters.language = lang_map.get(m_lang.group(1))

    # 2. Возраст ('с ребёнком 8 лет', 'ребенку 7 лет', 'для детей', '6+')
    m_child_age = re.search(r"(?:с\s+ребенком|ребенку|для\s+ребенка)\s*(\d{1,2})\s*(?:лет|года|год)?", low)
    if m_child_age:
        mark_span(m_child_age)
        child_years = int(m_child_age.group(1))
        allowed_brackets = [b for b in sorted(VALID_AGE_LIMITS) if b <= child_years]
        filters.max_age_limit = max(allowed_brackets) if allowed_brackets else 0
    else:
        m_for_kids = re.search(r"\b(для\s+детей|детский|детские|с\s+детьми|с\s+ребенком)\b", low)
        if m_for_kids:
            mark_span(m_for_kids)
            filters.max_age_limit = 6

    # 3. Время начала / окончания ('после 19:00', 'не позже 20:00', 'вечером')
    m_after = re.search(r"после\s+(\d{1,2})(?::(\d{2}))?", low)
    if m_after:
        mark_span(m_after)
        hh = int(m_after.group(1))
        mm = int(m_after.group(2) or 0)
        filters.time_after = f"{hh:02d}:{mm:02d}"

    m_before = re.search(r"(?:не\s+позже|до)\s+(\d{1,2}):(\d{2})", low)
    if m_before:
        mark_span(m_before)
        hh = int(m_before.group(1))
        mm = int(m_before.group(2))
        filters.time_before = f"{hh:02d}:{mm:02d}"

    m_evening = re.search(r"\bвечером\b", low)
    if m_evening and not filters.time_after:
        mark_span(m_evening)
        filters.time_after = "18:00"

    # 4. Длительность ('не дольше двух часов', 'не дольше 2 часов', 'до 90 минут')
    m_dur = re.search(r"не\s+дольше\s+(двух|2|трех|3|одного|1)\s*час(?:а|ов)?", low)
    if m_dur:
        mark_span(m_dur)
        word_to_hours = {"одного": 1, "1": 1, "двух": 2, "2": 2, "трех": 3, "3": 3}
        filters.max_duration_min = word_to_hours[m_dur.group(1)] * 60

    # 5. Цена ('бесплатные', 'бесплатно', 'до 1000 рублей на двоих', 'до 1500 рублей', 'недорого')
    m_free = re.search(r"\b(бесплатн[а-я]+|вход\s+свободный)\b", low)
    if m_free:
        mark_span(m_free)
        filters.is_free_only = True
        filters.max_price = 0
    else:
        m_price = re.search(
            r"(?:до\s+)?(\d{2,5})\s*(?:руб(?:лей|ля|\.)?|₽)(\s+на\s+(двоих|троих|2|3))?",
            low
        )
        if m_price:
            mark_span(m_price)
            amount = int(m_price.group(1))
            persons_str = m_price.group(3) or ""
            if persons_str in ("двоих", "2"):
                amount = amount // 2
            elif persons_str in ("троих", "3"):
                amount = amount // 3
            filters.max_price = amount
        else:
            m_cheap = re.search(r"\b(недорого|подешевле|дешевле|бюджетно)\b", low)
            if m_cheap:
                mark_span(m_cheap)
                filters.max_price = 500

    # 6. Даты и периоды
    m_dm_ru = re.search(r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b(?:\s+(\d{4})(?:\s+года?)?\b)?", low)
    m_dmy = re.search(r"\b(\d{2})\.(\d{2})(?:\.(\d{4}))?\b", low)
    if m_dm_ru:
        mark_span(m_dm_ru)
        day_num = int(m_dm_ru.group(1))
        month_num = MONTHS_RU[m_dm_ru.group(2)]
        target_d = date(int(m_dm_ru.group(3)) if m_dm_ru.group(3) else ref_date.year, month_num, day_num)
        filters.date_from = f"{target_d.isoformat()}T00:00:00"
        filters.date_to = f"{target_d.isoformat()}T23:59:59"
    elif m_dmy:
        mark_span(m_dmy)
        day_num = int(m_dmy.group(1))
        month_num = int(m_dmy.group(2))
        year_num = int(m_dmy.group(3)) if m_dmy.group(3) else ref_date.year
        target_d = date(year_num, month_num, day_num)
        filters.date_from = f"{target_d.isoformat()}T00:00:00"
        filters.date_to = f"{target_d.isoformat()}T23:59:59"
    else:
        date_patterns = [
            (r"\b(в\s+эти\s+выходные|на\s+этих\s+выходных|на\s+выходных|в\s+выходные|выходные)\b", "weekend"),
            (r"\b(на\s+следующей\s+неделе)\b", "next_week"),
            (r"\b(на\s+этой\s+неделе|на\s+неделе)\b", "week"),
            (r"\b(до\s+конца\s+месяца|в\s+этом\s+месяце)\b", "month"),
            (r"\b(сегодня)\b", "today"),
            (r"\b(завтра)\b", "tomorrow"),
            (r"\b(в\s+пятницу|пятница)\b", "friday"),
            (r"\b(в\s+субботу|суббота)\b", "saturday"),
            (r"\b(в\s+воскресенье|воскресенье)\b", "sunday"),
        ]
        for pat, preset_code in date_patterns:
            m_p = re.search(pat, low)
            if m_p:
                mark_span(m_p)
                filters.date_preset = preset_code
                filters.date_from, filters.date_to = resolve_date_preset(preset_code, ref_date)
                break

    # A region is a geographic scope, never a city name or free-text event keyword.
    m_region = re.search(
        r"\b(?:республик(?:а|е|у)\s+башкортостан(?:а|е)?|башкортостан(?:а|е)?|"
        r"republic\s+of\s+bashkortostan|bashkortostan)\b", low)
    if m_region:
        mark_span(m_region)

    # 7. Токенизация оставшегося текста для определения города, категории и ключевых слов
    # Вырезаем уже распознанные конструкции
    chars = list(low)
    for st, en in consumed_spans:
        for idx in range(st, min(en, len(chars))):
            chars[idx] = " "
    leftover = "".join(chars)

    # Убираем служебные стоп-слова
    stopwords = {
        "что", "интересного", "куда", "пойти", "сходить", "посмотреть", "есть", "ли",
        "в", "во", "на", "по", "из", "до", "для", "или", "и", "а", "но", "не", "без",
        "чтобы", "закончилось", "ехать", "больше", "минут", "часов", "часа", "сколько",
        "стоит", "билет", "ближайший", "ближайшие", "мероприятия", "мероприятие", "события",
        "событие", "отменили", "найди", "покажи", "какие", "где"
    }

    words = [w for w in re.findall(r"[а-яa-z0-9]+", leftover) if w not in stopwords]
    remaining_keywords: List[str] = []

    for w in words:
        if not filters.city:
            resolved_c = resolve_city(w, db=db)
            if resolved_c:
                filters.city = resolved_c
                continue
        if not filters.category:
            # Не превращаем узкие жанровые слова ('балет', 'джаз', 'курай') только в широкую категорию,
            # чтобы не потерять текстовое уточнение
            if w in ("балет", "джаз", "курай", "орган"):
                remaining_keywords.append(w)
                continue
            resolved_cat = resolve_category(w, db=db)
            if resolved_cat:
                filters.category = resolved_cat
                continue
        remaining_keywords.append(w)

    if remaining_keywords:
        filters.query_text = " ".join(remaining_keywords)

    return filters
