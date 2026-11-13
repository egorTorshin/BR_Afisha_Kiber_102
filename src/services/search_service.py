from dataclasses import replace
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, List, Optional, Tuple
from src.db.connection import Database
from src.models.schemas import EventRecord, EventSource, SearchFilters
from src.services.normalizer import normalize_text, resolve_category, resolve_city


class SearchService:
    """
    Сервис поиска и фильтрации мероприятий (Раздел 4, 6.1, 6.2 ТЗ).
    Гарантирует:
    - выдачу только опубликованных событий в общем поиске (отменённые и черновики скрыты);
    - независимое от системной локали сопоставление кириллицы (Уфа/уфа, Концерт/концерт);
    - отсутствие дублей в выдаче по dedup_key;
    - доступ к отменённым событиям по прямой ссылке/ID с явной пометкой;
    - понятное объяснение пустого результата с подсказкой, какое ограничение можно ослабить.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def _record(self, row) -> EventRecord:
        result = EventRecord.from_row(row)
        result.sources = [EventSource(**source) for source in self.db.fetchall(
            "SELECT * FROM event_sources WHERE event_id = ? ORDER BY source_url", (result.id,)
        )]
        return result

    @staticmethod
    def distance_km(latitude: float, longitude: float, event: EventRecord) -> Optional[float]:
        """Great-circle distance; returns None where an event has no coordinates."""
        if event.lat is None or event.lon is None:
            return None
        d_lat, d_lon = radians(event.lat - latitude), radians(event.lon - longitude)
        a = sin(d_lat / 2) ** 2 + cos(radians(latitude)) * cos(radians(event.lat)) * sin(d_lon / 2) ** 2
        return 6371 * 2 * asin(sqrt(a))

    def get_event_by_id(self, event_id: str, *, include_drafts: bool = False) -> Optional[EventRecord]:
        # include_drafts is for trusted internal callers, never a public API parameter.
        clean_id = event_id.strip()
        row = self.db.fetchone("SELECT * FROM events WHERE id = ?", (clean_id,))
        if not row and len(clean_id) >= 8 and not any(c in clean_id for c in "%_"):
            rows = self.db.fetchall("SELECT * FROM events WHERE id LIKE ? LIMIT 2", (f"{clean_id}%",))
            if len(rows) == 1:
                row = rows[0]
        if not row:
            return None
        while row.get("merged_into"):
            row = self.db.fetchone("SELECT * FROM events WHERE id = ?", (row["merged_into"],))
        if row["status"] == "draft" and not include_drafts:
            return None
        return self._record(row)

    @staticmethod
    def _like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _query_sql(self, filters: SearchFilters) -> tuple[str, list[Any]]:
        clauses = ["status = ?", "merged_into IS NULL"]
        params: List[Any] = ["published"]

        if filters.date_from:
            clauses.append("(date_start >= ? OR (date_end IS NOT NULL AND date_end >= ?))")
            params.extend([filters.date_from, filters.date_from])

        if filters.date_to:
            clauses.append("date_start <= ?")
            params.append(filters.date_to)

        if filters.is_free_only:
            clauses.append("is_free = 1")
        elif filters.max_price is not None:
            clauses.append("COALESCE(price_min, price_max) <= ?")
            params.append(filters.max_price)

        if filters.max_age_limit is not None:
            clauses.append("age_limit <= ?")
            params.append(filters.max_age_limit)

        if filters.city:
            city = resolve_city(filters.city, db=self.db) or filters.city
            clauses.append("search_city = ?")
            params.append(normalize_text(city))
        selected_categories = list(dict.fromkeys(([filters.category] if filters.category else []) + filters.categories))
        if len(selected_categories) == 1:
            category = resolve_category(selected_categories[0], db=self.db) or selected_categories[0]
            clauses.append("category = ?")
            params.append(normalize_text(category))
        elif selected_categories:
            normalized = [normalize_text(resolve_category(value, db=self.db) or value) for value in selected_categories]
            clauses.append(f"category IN ({', '.join('?' for _ in normalized)})")
            params.extend(normalized)
        elif filters.category:
            category = resolve_category(filters.category, db=self.db) or filters.category
            clauses.append("category = ?")
            params.append(normalize_text(category))
        if filters.venue:
            clauses.append("search_venue LIKE ? ESCAPE '\\'")
            params.append(f"%{self._like(normalize_text(filters.venue))}%")
        if filters.language:
            clauses.append("search_language = ?")
            params.append(normalize_text(filters.language))
        if filters.tag:
            clauses.append("search_tags LIKE ? ESCAPE '\\'")
            params.append(f"%{self._like(normalize_text(filters.tag))}%")
        if filters.time_after:
            clauses.append("substr(date_start, 12, 5) >= ?")
            params.append(filters.time_after)
        if filters.time_before:
            clauses.append("substr(COALESCE(date_end, date_start), 12, 5) <= ?")
            params.append(filters.time_before)
        if filters.max_duration_min is not None:
            clauses.append("(duration_min IS NULL OR duration_min <= ?)")
            params.append(filters.max_duration_min)
        if filters.query_text:
            for word in (w for w in normalize_text(filters.query_text).split() if len(w) >= 2):
                stem = word[:max(3, len(word) - 2)] if len(word) >= 5 else word
                clauses.append("search_text LIKE ? ESCAPE '\\'")
                params.append(f"%{self._like(stem)}%")

        return " AND ".join(clauses), params

    def count_matching(self, filters: SearchFilters) -> int:
        where_sql, params = self._query_sql(filters)
        return int(self.db.fetchone(f"SELECT COUNT(*) AS n FROM events WHERE {where_sql}", params)["n"])

    def search(self, filters: SearchFilters, *, sort: str = "date_asc") -> Tuple[List[EventRecord], int, Optional[str]]:
        """
        Выполняет поиск с пагинацией.
        Возвращает: (список событий на текущей странице, общее число подходящих событий, подсказка при пустом результате).
        """
        where_sql, params = self._query_sql(filters)
        total = int(self.db.fetchone(f"SELECT COUNT(*) AS n FROM events WHERE {where_sql}", params)["n"])
        order_by = {
            "date_asc": "date_start ASC, id ASC",
            "date_desc": "date_start DESC, id DESC",
            "price_asc": "(COALESCE(price_min, price_max) IS NULL) ASC, COALESCE(price_min, price_max) ASC, date_start ASC, id ASC",
        }.get(sort)
        if order_by is None:
            raise ValueError("Недопустимая сортировка")
        rows = self.db.fetchall(
            f"SELECT * FROM events WHERE {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
            (*params, filters.limit, filters.offset),
        )
        page = [EventRecord.from_row(row) for row in rows]
        if page:
            by_id = {event.id: event for event in page}
            placeholders = ", ".join("?" for _ in page)
            sources = self.db.fetchall(
                f"SELECT * FROM event_sources WHERE event_id IN ({placeholders}) ORDER BY source_url", tuple(by_id)
            )
            for source in sources:
                by_id[source["event_id"]].sources.append(EventSource(**source))
        explanation = None
        if total == 0:
            explanation = self.explain_empty_result(filters)
        return page, total, explanation

    def search_for_profile(self, filters: SearchFilters, latitude: Optional[float], longitude: Optional[float],
                           favorite_categories: Optional[list[str]] = None) -> Tuple[List[EventRecord], int, Optional[str]]:
        """Date → preferred category → distance. Missing coordinates stay at the end."""
        if latitude is None or longitude is None:
            return self.search(filters)
        where_sql, params = self._query_sql(filters)
        rows = self.db.fetchall(f"SELECT * FROM events WHERE {where_sql}", params)
        events = [EventRecord.from_row(row) for row in rows]
        favorites = set(favorite_categories or [])

        for event in events:
            event.distance_km = self.distance_km(latitude, longitude, event)

        def distance(event: EventRecord) -> float:
            return event.distance_km if event.distance_km is not None else float("inf")

        events.sort(key=lambda e: (e.date_start, 0 if e.category in favorites else 1, distance(e), e.id))
        total = len(events)
        page = events[filters.offset:filters.offset + filters.limit]
        return page, total, self.explain_empty_result(filters) if not page else None

    def recommend_alternatives(self, filters: SearchFilters, reference_date) -> tuple[str, list[EventRecord]]:
        """Prefer future alternatives in the chosen city, then nearest other city."""
        future_from = f"{reference_date.isoformat()}T00:00:00"
        same_city = replace(filters, category=None, categories=[], date_preset=None,
                            date_from=future_from, date_to=None, query_text=None, offset=0, limit=2)
        local_events, local_total, _ = self.search(same_city)
        if local_total:
            return "same_city", local_events

        broad = replace(filters, city=None, category=None, categories=[], date_preset=None,
                        date_from=future_from, date_to=None, query_text=None, offset=0, limit=100)
        candidates, _, _ = self.search(broad)
        if not candidates:
            return "none", []
        origin = self.db.fetchone(
            "SELECT AVG(lat) AS lat, AVG(lon) AS lon FROM events WHERE city = ? AND lat IS NOT NULL AND lon IS NOT NULL",
            (filters.city,),
        ) if filters.city else None
        if origin and origin.get("lat") is not None and origin.get("lon") is not None:
            for event in candidates:
                event.distance_km = self.distance_km(float(origin["lat"]), float(origin["lon"]), event)
            candidates.sort(key=lambda event: (event.distance_km is None, event.distance_km or float("inf"), event.date_start, event.id))
            return "nearby_city", candidates[:2]
        return "other_city", candidates[:2]

    def explain_empty_result(self, filters: SearchFilters) -> str:
        """
        Объясняет пустой результат: что именно не найдено и какое ограничение можно ослабить (Раздел 2, 6.1 ТЗ).
        """
        desc = filters.describe_human()
        suggestions: List[str] = []

        if filters.is_free_only or filters.max_price is not None:
            relaxed_price = replace(filters, is_free_only=False, max_price=None, offset=0)
            cnt = self.count_matching(relaxed_price)
            if cnt > 0:
                suggestions.append(f"снять ограничение по цене — найдётся событий: {cnt}")

        if filters.date_from or filters.date_to or filters.date_preset or filters.time_after or filters.time_before:
            relaxed_date = replace(
                filters,
                date_preset=None,
                date_from=None,
                date_to=None,
                time_after=None,
                time_before=None,
                offset=0,
            )
            cnt = self.count_matching(relaxed_date)
            if cnt > 0:
                suggestions.append(f"расширить дату и время поиска — найдётся событий: {cnt}")

        if filters.query_text:
            relaxed_text = replace(filters, query_text=None, offset=0)
            cnt = self.count_matching(relaxed_text)
            if cnt > 0:
                suggestions.append(
                    f"убрать текстовое уточнение «{filters.query_text}» — в выбранных фильтрах есть другие события ({cnt} шт.)"
                )

        if filters.category:
            relaxed_cat = replace(filters, category=None, offset=0)
            cnt = self.count_matching(relaxed_cat)
            if cnt > 0:
                suggestions.append(f"выбрать «Все категории» вместо «{filters.category}» — найдётся событий: {cnt}")

        if filters.max_age_limit is not None:
            relaxed_age = replace(filters, max_age_limit=None, offset=0)
            cnt = self.count_matching(relaxed_age)
            if cnt > 0:
                suggestions.append(f"ослабить возрастной фильтр {filters.max_age_limit}+ — найдётся событий: {cnt}")

        if filters.city:
            relaxed_city = replace(filters, city=None, offset=0)
            cnt = self.count_matching(relaxed_city)
            if cnt > 0:
                suggestions.append(f"посмотреть события во всех городах РБ — найдётся событий: {cnt}")

        cancelled_note = ""
        if filters.date_from and filters.date_to and filters.date_from[:10] == filters.date_to[:10]:
            day_prefix = filters.date_from[:10]
            cancelled_rows = self.db.fetchall(
                "SELECT id, title, city, venue FROM events WHERE status = 'cancelled' AND merged_into IS NULL AND date_start LIKE ?",
                (f"{day_prefix}%",),
            )
            if cancelled_rows:
                c_row = cancelled_rows[0]
                cancelled_note = (
                    f"\n\n⚠️ Обратите внимание: мероприятие «{c_row['title']}» ({day_prefix}, г. {c_row['city']}) "
                    f"было ОТМЕНЕНО и исключено из активной выдачи.\n"
                    f"Карточка события: /event_{c_row['id']}"
                )

        base_msg = (
            f"🔍 По вашему запросу подходящих мероприятий не найдено\n"
            f"Заданные условия: {desc}{cancelled_note}"
        )
        if suggestions:
            bullet_list = "\n".join(f"• {s}" for s in suggestions[:3])
            return f"{base_msg}\n\n💡 Рекомендация — какое ограничение можно ослабить:\n{bullet_list}"
        return (
            f"{base_msg}\n\n💡 Рекомендация: сейчас в каталоге нет мероприятий с таким сочетанием параметров. "
            f"Нажмите «🔄 Сбросить фильтры» или выберите другой город и период."
        )

    def list_cities_with_counts(self) -> List[Dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT city, COUNT(DISTINCT dedup_key) AS cnt
            FROM events
            WHERE status = 'published' AND merged_into IS NULL
            GROUP BY city
            ORDER BY cnt DESC, city ASC
            """
        )
        return rows

    def list_categories_with_counts(self, city: Optional[str] = None) -> List[Dict[str, Any]]:
        where_sql, params = self._query_sql(SearchFilters(city=city))
        return self.db.fetchall(
            f"SELECT category, COUNT(*) AS cnt FROM events WHERE {where_sql} "
            "GROUP BY category ORDER BY cnt DESC, category ASC", params,
        )


    def list_public_venues(self, city: Optional[str] = None) -> List[Dict[str, Any]]:
        # Read actual catalog values; do not manufacture dictionary IDs/coordinates.
        where_sql, params = self._query_sql(SearchFilters(city=city))
        return self.db.fetchall(
            f"SELECT DISTINCT city, venue AS name, address FROM events WHERE {where_sql} "
            "ORDER BY city, name, address", params,
        )
