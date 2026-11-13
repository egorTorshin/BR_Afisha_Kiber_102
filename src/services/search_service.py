from dataclasses import replace
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

    def _matches_python_postfilters(self, ev: EventRecord, filters: SearchFilters) -> bool:
        # Независимая от локали СУБД проверка города, категории, площадки и языка на Python
        if filters.city:
            resolved_c = resolve_city(filters.city, db=self.db) or filters.city
            if normalize_text(ev.city) != normalize_text(resolved_c):
                return False

        if filters.category:
            resolved_cat = resolve_category(filters.category, db=self.db) or filters.category
            if normalize_text(ev.category) != normalize_text(resolved_cat):
                return False

        if filters.venue:
            if normalize_text(filters.venue) not in normalize_text(ev.venue):
                return False

        if filters.language:
            if normalize_text(ev.language or "") != normalize_text(filters.language):
                return False

        # Фильтр по времени начала (например, после 19:00)
        if filters.time_after:
            ev_time = ev.date_start[11:16] if len(ev.date_start) >= 16 else "00:00"
            if ev_time < filters.time_after:
                return False

        # Фильтр по времени окончания (например, не позже 20:00)
        if filters.time_before:
            end_str = ev.date_end or ev.date_start
            ev_end_time = end_str[11:16] if len(end_str) >= 16 else "23:59"
            if ev_end_time > filters.time_before:
                return False

        # Фильтр по длительности (например, не дольше 120 минут)
        if filters.max_duration_min is not None and ev.duration_min is not None:
            if ev.duration_min > filters.max_duration_min:
                return False

        # Фильтр по текстовому запросу (поиск в названии, описании, площадке и тегах)
        if filters.query_text:
            q_words = [w for w in normalize_text(filters.query_text).split() if len(w) >= 2]
            haystack = normalize_text(
                f"{ev.title} {ev.description} {ev.venue} {ev.category} {' '.join(ev.tags)} {ev.language or ''}"
            )
            for qw in q_words:
                stem = qw[: max(3, len(qw) - 2)] if len(qw) >= 5 else qw
                if qw not in haystack and stem not in haystack:
                    return False

        if filters.tag:
            norm_tag = normalize_text(filters.tag)
            if not any(norm_tag in normalize_text(t) for t in ev.tags):
                return False

        return True

    def _query_candidates(self, filters: SearchFilters) -> List[EventRecord]:
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

        where_sql = " AND ".join(clauses)
        sql = f"SELECT * FROM events WHERE {where_sql} ORDER BY date_start ASC, id ASC"
        rows = self.db.fetchall(sql, params)

        seen_dedup = set()
        results: List[EventRecord] = []
        for r in rows:
            ev = EventRecord.from_row(r)
            if ev.dedup_key in seen_dedup:
                continue
            if not self._matches_python_postfilters(ev, filters):
                continue
            seen_dedup.add(ev.dedup_key)
            results.append(ev)
        return results

    def count_matching(self, filters: SearchFilters) -> int:
        return len(self._query_candidates(filters))

    def search(self, filters: SearchFilters, *, sort: str = "date_asc") -> Tuple[List[EventRecord], int, Optional[str]]:
        """
        Выполняет поиск с пагинацией.
        Возвращает: (список событий на текущей странице, общее число подходящих событий, подсказка при пустом результате).
        """
        all_matches = self._query_candidates(filters)
        if sort == "date_desc":
            all_matches.sort(key=lambda ev: (ev.date_start, ev.id), reverse=True)
        elif sort == "price_asc":
            def price_key(ev):
                price = ev.price_min if ev.price_min is not None else ev.price_max
                return (price is None, price if price is not None else 0, ev.date_start, ev.id)
            all_matches.sort(key=price_key)
        elif sort != "date_asc":
            raise ValueError("Недопустимая сортировка")
        total = len(all_matches)
        page = all_matches[filters.offset : filters.offset + filters.limit]
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

    def explain_empty_result(self, filters: SearchFilters) -> str:
        """
        Объясняет пустой результат: что именно не найдено и какое ограничение можно ослабить (Раздел 2, 6.1 ТЗ).
        """
        desc = filters.describe_human()
        suggestions: List[str] = []

        if filters.is_free_only or filters.max_price is not None:
            relaxed_price = replace(filters, is_free_only=False, max_price=None, offset=0)
            cnt = len(self._query_candidates(relaxed_price))
            if cnt > 0:
                suggestions.append(f"снять ограничение по цене — найдётся событий: <b>{cnt}</b>")

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
            cnt = len(self._query_candidates(relaxed_date))
            if cnt > 0:
                suggestions.append(f"расширить дату и время поиска — найдётся событий: <b>{cnt}</b>")

        if filters.query_text:
            relaxed_text = replace(filters, query_text=None, offset=0)
            cnt = len(self._query_candidates(relaxed_text))
            if cnt > 0:
                suggestions.append(
                    f"убрать текстовое уточнение «{filters.query_text}» — в выбранных фильтрах есть другие события (<b>{cnt} шт.</b>)"
                )

        if filters.category:
            relaxed_cat = replace(filters, category=None, offset=0)
            cnt = len(self._query_candidates(relaxed_cat))
            if cnt > 0:
                suggestions.append(f"выбрать «Все категории» вместо «{filters.category}» — найдётся событий: <b>{cnt}</b>")

        if filters.max_age_limit is not None:
            relaxed_age = replace(filters, max_age_limit=None, offset=0)
            cnt = len(self._query_candidates(relaxed_age))
            if cnt > 0:
                suggestions.append(f"ослабить возрастной фильтр {filters.max_age_limit}+ — найдётся событий: <b>{cnt}</b>")

        if filters.city:
            relaxed_city = replace(filters, city=None, offset=0)
            cnt = len(self._query_candidates(relaxed_city))
            if cnt > 0:
                suggestions.append(f"посмотреть события во всех городах РБ — найдётся событий: <b>{cnt}</b>")

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
                    f"\n\n⚠️ <b>Обратите внимание:</b> мероприятие «{c_row['title']}» ({day_prefix}, г. {c_row['city']}) "
                    f"было <b>ОТМЕНЕНО</b> и исключено из активной выдачи.\n"
                    f"Карточка события: /event_{c_row['id']}"
                )

        base_msg = (
            f"🔍 <b>По вашему запросу подходящих мероприятий не найдено</b>\n"
            f"<i>Заданные условия: {desc}</i>{cancelled_note}"
        )
        if suggestions:
            bullet_list = "\n".join(f"• {s}" for s in suggestions[:3])
            return f"{base_msg}\n\n💡 <b>Рекомендация — какое ограничение можно ослабить:</b>\n{bullet_list}"
        return (
            f"{base_msg}\n\n💡 <b>Рекомендация:</b> сейчас в каталоге нет мероприятий с таким сочетанием параметров. "
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
        all_published = self._query_candidates(SearchFilters(city=city, limit=1000))
        counts: Dict[str, int] = {}
        for ev in all_published:
            counts[ev.category] = counts.get(ev.category, 0) + 1
        return [
            {"category": cat, "cnt": cnt}
            for cat, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        ]


    def list_public_venues(self, city: Optional[str] = None) -> List[Dict[str, Any]]:
        # Read actual catalog values; do not manufacture dictionary IDs/coordinates.
        events = self._query_candidates(SearchFilters(city=city))
        venues = {(ev.city, ev.venue, ev.address) for ev in events}
        return [{"city": city, "name": name, "address": address}
                for city, name, address in sorted(venues)]
