"""Grounded, deterministic recommendations and small weekend itineraries."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import asin, cos, exp, radians, sin, sqrt
from statistics import median
from typing import Optional

from src.db.connection import Database
from src.models.schemas import EventRecord, EventSource
from src.services.event_series import group_rows, same_series, scheduled_record


_PUNCTUATION = str.maketrans({char: " " for char in "«»\"'„“”()[],.!?;:—–-"})


def normalize_text(value: str) -> str:
    """Fast local equivalent for repeated recommendation feature extraction."""
    return " ".join(str(value).casefold().replace("ё", "е").translate(_PUNCTUATION).split())


@dataclass(frozen=True)
class RecommendedEvent:
    event: EventRecord
    score: float
    match_reason: str


@dataclass(frozen=True)
class ItineraryStop:
    event: EventRecord
    start_time: datetime
    end_time: datetime
    travel_from_previous_min: Optional[int]
    unit_price: int
    party_price: int


@dataclass(frozen=True)
class WeekendPlanResult:
    stops: list[ItineraryStop]
    total_price: int
    total_travel_min: Optional[int]
    target_count: int
    conflict_explanation: Optional[str] = None
    recommendations: list[tuple[EventRecord, str]] | None = None


def _grams(value: str) -> Counter[str]:
    text = f"  {normalize_text(value)}  "
    return Counter(text[index:index + 3] for index in range(max(0, len(text) - 2)))


def _cosine(left: Counter[str], right: Counter[str], *,
            left_norm: Optional[float] = None, right_norm: Optional[float] = None) -> float:
    if not left or not right:
        return 0.0
    dot = sum(count * right.get(gram, 0) for gram, count in left.items())
    norm_left = left_norm if left_norm is not None else sqrt(sum(count * count for count in left.values()))
    norm_right = right_norm if right_norm is not None else sqrt(sum(count * count for count in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _tags(event: EventRecord) -> set[str]:
    return {normalize_text(tag) for tag in event.tags if tag}


def _distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    lat_delta = radians(b_lat - a_lat)
    lon_delta = radians(b_lon - a_lon)
    hav = sin(lat_delta / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(lon_delta / 2) ** 2
    return 6371.0 * 2 * asin(min(1.0, sqrt(max(0.0, hav))))


def _unit_price(event: EventRecord) -> Optional[int]:
    if event.is_free:
        return 0
    if event.price_max is not None and event.price_max > 0:
        return event.price_max
    if event.price_min is not None and event.price_min > 0:
        return event.price_min
    return None


def _price_similarity(left: Optional[int], right: Optional[int]) -> float:
    if left is None or right is None:
        return 0.0
    return exp(-abs(left - right) / max(300, right))


def _event_similarity(left: tuple[str, str, str, set[str]],
                      right: tuple[str, str, str, set[str]]) -> float:
    shared = left[3] & right[3]
    union = left[3] | right[3]
    return (0.45 * float(left[0] == right[0])
            + 0.35 * float(left[1] == right[1] and left[2] == right[2])
            + 0.20 * (len(shared) / len(union) if union else 0.0))


class RecommendationService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._feature_cache: dict[str, tuple[str, tuple[str, str, str, set[str]], Optional[Counter[str]]]] = {}

    def _features(self, event: EventRecord, *, vector: bool = False):
        cached = self._feature_cache.get(event.id)
        if cached is None or cached[0] != event.updated_at:
            tags = _tags(event)
            diversity = (event.category, normalize_text(event.venue), normalize_text(event.city), tags)
            cached = (event.updated_at, diversity, None)
            if len(self._feature_cache) > 5000:
                self._feature_cache.clear()
            self._feature_cache[event.id] = cached
        if vector and cached[2] is None:
            grams = _grams(" ".join((event.normalized_title, *sorted(cached[1][3]), event.category)))
            cached = (cached[0], cached[1], grams)
            self._feature_cache[event.id] = cached
        return cached[1], cached[2]

    def _published(self, *, city: Optional[str] = None, date_from: Optional[str] = None,
                   date_to: Optional[str] = None, group_series: bool = False,
                   exclude_series_row: dict | None = None) -> list[EventRecord]:
        clauses = ["status = 'published'", "merged_into IS NULL"]
        params: list[str] = []
        if city:
            clauses.append("search_city = ?")
            params.append(normalize_text(city))
        if date_from:
            clauses.append("date_start >= ?")
            params.append(date_from[:10] + "T00:00:00")
        if date_to:
            clauses.append("date_start <= ?")
            params.append(date_to[:10] + "T23:59:59")
        rows = self.db.fetchall(
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY date_start, id", params)
        if group_series:
            return [scheduled_record(group) for group in group_rows(rows)
                    if exclude_series_row is None or not same_series(group[0], exclude_series_row)]
        return [EventRecord.from_row(row) for row in rows]

    def _with_sources(self, events: list[EventRecord]) -> None:
        if not events:
            return
        by_id = {event.id: event for event in events}
        placeholders = ", ".join("?" for _ in by_id)
        rows = self.db.fetchall(
            f"SELECT * FROM event_sources WHERE event_id IN ({placeholders}) ORDER BY source_url",
            tuple(by_id),
        )
        for row in rows:
            by_id[row["event_id"]].sources.append(EventSource(**row))

    def _optional_rows(self, query: str, params: tuple = ()) -> list[dict]:
        try:
            return self.db.fetchall(query, params)
        except Exception as exc:
            message = str(exc).lower()
            if "no such table" in message or ("relation" in message and "does not exist" in message):
                return []
            raise

    def _identity_ids(self, user_id: str) -> tuple[list[str], list[str]]:
        canonical = self._optional_rows("SELECT id FROM users WHERE id = ?", (user_id,))
        identities = self._optional_rows(
            "SELECT user_id, provider_id FROM user_identities "
            "WHERE provider = 'telegram' AND (provider_id = ? OR user_id = ?)", (user_id, user_id))
        user_ids = {row["id"] for row in canonical} | {row["user_id"] for row in identities}
        telegram_ids = {user_id} | {row["provider_id"] for row in identities}
        return sorted(user_ids), sorted(telegram_ids)

    def recommend_for_user(self, user_id: str, city: Optional[str] = None, limit: int = 5,
                           ref_date: Optional[date] = None) -> list[RecommendedEvent]:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        today = ref_date or date.today()
        candidates = self._published(city=city, date_from=today.isoformat(), group_series=True)
        if not candidates:
            return []

        user_ids, telegram_ids = self._identity_ids(str(user_id)) if user_id else ([], [])
        viewed_rows: list[dict] = []
        for canonical_id in user_ids:
            viewed_rows.extend(self._optional_rows(
                "SELECT e.* FROM event_views v JOIN events e ON e.id = v.event_id "
                "WHERE v.user_id = ? ORDER BY v.viewed_at DESC, v.id DESC LIMIT 50", (canonical_id,)))
        viewed = [EventRecord.from_row(row) for row in viewed_rows[:50]]
        recent_ids = {event.id for event in viewed[:30]}
        candidates = [event for event in candidates if event.id not in recent_ids]
        if not candidates:
            return []

        subscriptions: list[dict] = []
        profiles: list[dict] = []
        for telegram_id in telegram_ids:
            subscriptions.extend(self._optional_rows(
                "SELECT city, category FROM subscriptions WHERE user_id = ? AND expires_at >= ?",
                (telegram_id, datetime.now(timezone.utc).isoformat())))
            profiles.extend(self._optional_rows(
                "SELECT city, favorite_categories_json, latitude, longitude FROM bot_profiles WHERE user_id = ?",
                (telegram_id,)))
        profile = profiles[0] if profiles else {}
        try:
            import json
            favorites = {normalize_text(item) for item in json.loads(profile.get("favorite_categories_json") or "[]")}
        except (TypeError, ValueError):
            favorites = set()
        preferred_city = city or profile.get("city")
        preferred_city_norm = normalize_text(preferred_city) if preferred_city else None
        subscription_keys = [(sub, normalize_text(sub["city"]), normalize_text(sub["category"]))
                             for sub in subscriptions]
        categories = Counter(event.category for event in viewed)
        viewed_tags = set().union(*(_tags(event) for event in viewed)) if viewed else set()
        user_grams = Counter()
        viewed_vectors = []
        for event in viewed:
            vector = self._features(event, vector=True)[1]
            viewed_vectors.append((event, vector, sqrt(sum(value * value for value in vector.values()))))
            user_grams.update(vector)
        user_norm = sqrt(sum(value * value for value in user_grams.values()))
        prices = [price for event in viewed if (price := _unit_price(event)) is not None]
        median_price = int(median(prices)) if prices else None
        latitude, longitude = profile.get("latitude"), profile.get("longitude")

        ranked: list[tuple[EventRecord, float, str]] = []
        diversity_features: dict[str, tuple[str, str, str, set[str]]] = {}
        for event in candidates:
            diversity, event_vector = self._features(event, vector=bool(viewed))
            diversity_features[event.id] = diversity
            event_tags = diversity[3]
            event_norm = sqrt(sum(value * value for value in event_vector.values())) if viewed else 0.0
            content = (0.65 * _cosine(user_grams, event_vector, left_norm=user_norm, right_norm=event_norm)
                       + 0.35 * (0.5 * float(bool(categories[event.category]))
                                 + 0.5 * len(event_tags & viewed_tags) / max(1, len(event_tags)))) if viewed else 0.0
            category_norm = normalize_text(event.category)
            matched_sub = next((sub for sub, sub_city, sub_category in subscription_keys
                                if (sub_city in {"все города", diversity[2]}
                                    and sub_category in {"все категории", category_norm})), None)
            preference = float(bool(matched_sub or category_norm in favorites))
            distance = None
            if latitude is not None and longitude is not None and event.lat is not None and event.lon is not None:
                distance = _distance_km(float(latitude), float(longitude), event.lat, event.lon)
                geo = exp(-distance / 10.0)
            else:
                geo = float(bool(preferred_city_norm and preferred_city_norm == diversity[2]))
            price = max(_price_similarity(_unit_price(event), median_price), 0.8 if event.is_free else 0.0)
            days = max(0, (date.fromisoformat(event.date_start[:10]) - today).days)
            temporal = exp(-days / 14.0)
            score = ((0.35 * content + 0.25 * preference + 0.20 * geo + 0.10 * price + 0.10 * temporal)
                     if viewed else (0.25 * preference + 0.20 * geo + 0.10 * price + 0.45 * temporal))
            reasons: list[tuple[float, str]] = []
            if viewed and content > 0.15:
                closest = (viewed_vectors[0][0] if len(viewed_vectors) == 1 else
                           max(viewed_vectors, key=lambda item: _cosine(
                               item[1], event_vector, left_norm=item[2], right_norm=event_norm))[0])
                reasons.append((content, f"Похоже на просмотренное вами событие «{closest.title}»"))
            if matched_sub:
                reasons.append((1.0, f"По вашей подписке: {matched_sub['city']} • {matched_sub['category']}"))
            elif category_norm in favorites:
                reasons.append((0.9, f"Любимая категория «{event.category}»"))
            if distance is not None:
                reasons.append((geo, f"{distance:.1f} км от вас"))
            elif preferred_city_norm and preferred_city_norm == diversity[2]:
                reasons.append((0.6, f"В вашем городе: {event.city}"))
            if event.is_free:
                reasons.append((0.45, "Вход свободный"))
            reasons.append((0.3 * temporal, f"Скоро в г. {event.city}"))
            reasons.sort(key=lambda item: -item[0])
            reason = " • ".join(item[1] for item in reasons[:2]) + "."
            ranked.append((event, min(1.0, max(0.0, score)), reason))

        ranked.sort(key=lambda item: (-item[1], item[0].date_start, item[0].id))
        selected: list[tuple[EventRecord, float, str]] = []
        while ranked and len(selected) < limit:
            best_index = 0
            best_key = (-1.0, -1.0, 0)
            for index, item in enumerate(ranked):
                upper_bound = 0.75 * item[1]
                if upper_bound < best_key[0]:
                    break
                similarity = max((_event_similarity(diversity_features[item[0].id],
                                                     diversity_features[chosen[0].id]) for chosen in selected),
                                 default=0.0)
                key = (upper_bound - 0.25 * similarity, item[1], -index)
                if key > best_key:
                    best_index, best_key = index, key
            best = ranked.pop(best_index)
            selected.append(best)
        self._with_sources([event for event, _, _ in selected])
        return [RecommendedEvent(event, round(score, 4), reason) for event, score, reason in selected]

    def find_similar_hybrid(self, event_id: str, city: Optional[str] = None,
                            date_from: Optional[str] = None, date_to: Optional[str] = None,
                            limit: int = 5) -> list[RecommendedEvent]:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        source_rows = self.db.fetchall(
            "SELECT * FROM events WHERE id = ? AND status = 'published' AND merged_into IS NULL", (event_id,))
        if not source_rows:
            return []
        source = EventRecord.from_row(source_rows[0])
        candidates = self._published(city=city, date_from=date_from, date_to=date_to,
                                     group_series=True, exclude_series_row=source_rows[0])
        source_grams = _grams(" ".join((source.normalized_title, *sorted(self._features(source)[0][3]))))
        ranked: list[RecommendedEvent] = []
        for event in candidates:
            text_similarity = _cosine(source_grams, _grams(
                " ".join((event.normalized_title, *sorted(self._features(event)[0][3])))))
            price = _price_similarity(_unit_price(event), _unit_price(source))
            if None not in (source.lat, source.lon, event.lat, event.lon):
                geo = exp(-_distance_km(source.lat, source.lon, event.lat, event.lon) / 10.0)
            else:
                geo = float(source.city == event.city)
            score = (0.35 * float(source.category == event.category) + 0.35 * text_similarity
                     + 0.15 * price + 0.15 * geo)
            reason = (f"Похожая категория «{event.category}»" if source.category == event.category
                      else "Похоже по названию и теме") + (" • близкая площадка." if geo > 0.7 else ".")
            ranked.append(RecommendedEvent(event, round(score, 4), reason))
        ranked.sort(key=lambda item: (-item.score, item.event.date_start, item.event.id))
        selected = ranked[:limit]
        self._with_sources([item.event for item in selected])
        return selected

    @staticmethod
    def _travel_minutes(left: EventRecord, right: EventRecord) -> Optional[int]:
        same_address = normalize_text(left.address) and normalize_text(left.address) == normalize_text(right.address)
        same_venue = normalize_text(left.venue) and normalize_text(left.venue) == normalize_text(right.venue)
        if normalize_text(left.city) == normalize_text(right.city) and (same_address or same_venue):
            return 0
        # Coordinates alone do not establish a drivable or walkable route time.
        return None

    @staticmethod
    def _end_time(event: EventRecord, start: datetime) -> Optional[datetime]:
        if event.date_end:
            try:
                end = datetime.fromisoformat(event.date_end).replace(tzinfo=None)
                # Multi-day end dates describe an exhibition's availability,
                # not a visitor occupying the venue for the whole run.
                if timedelta(0) < end - start <= timedelta(hours=8):
                    return end
            except ValueError:
                pass
        if event.duration_min is not None and event.duration_min > 0:
            return start + timedelta(minutes=event.duration_min)
        return None

    def _plan_candidates(self, city: Optional[str], date_from: str, date_to: str,
                         max_age_limit: Optional[int]) -> list[tuple[EventRecord, datetime, datetime, int]]:
        prepared = []
        for event in self._published(city=city, date_from=date_from, date_to=date_to):
            if max_age_limit is not None and event.age_limit > max_age_limit:
                continue
            price = _unit_price(event)
            if price is None:  # Unknown ticket prices cannot support an exact total.
                continue
            try:
                start = datetime.fromisoformat(event.date_start).replace(tzinfo=None)
            except ValueError:
                continue
            end = self._end_time(event, start)
            if end is None:
                continue
            prepared.append((event, start, end, price))
        return prepared

    def _unknown_duration_recommendations(self, city: Optional[str], date_from: str, date_to: str,
                                          max_age_limit: Optional[int]) -> list[tuple[EventRecord, str]]:
        recommendations = []
        for event in self._published(city=city, date_from=date_from, date_to=date_to):
            if max_age_limit is not None and event.age_limit > max_age_limit:
                continue
            try:
                start = datetime.fromisoformat(event.date_start).replace(tzinfo=None)
            except ValueError:
                continue
            if self._end_time(event, start) is None:
                recommendations.append((event, "Длительность неизвестна; событие не включено в расписание."))
        self._with_sources([event for event, _ in recommendations])
        return recommendations

    def _solve_plan(self, candidates: list[tuple[EventRecord, datetime, datetime, int]],
                    party_size: int, target_count: int, budget: Optional[int],
                    travel_limit: Optional[int], objective: str = "quality") -> tuple[list[int], int, int, int]:
        """Pareto-pruned dynamic programming over a chronological event DAG."""
        best_path: list[int] = []
        best_cost = 0
        best_travel = 0
        best_bottleneck = 0
        best_key: tuple = ()
        count = len(candidates)
        costs = [item[3] * party_size for item in candidates]
        edges: list[list[tuple[int, int]]] = [[] for _ in candidates]
        for left in range(count):
            left_event, _, end, _ = candidates[left]
            for right in range(left + 1, count):
                right_event, start, _, _ = candidates[right]
                leg = self._travel_minutes(left_event, right_event)
                if leg is not None and travel_limit is not None and leg > travel_limit:
                    continue
                if (end + timedelta(minutes=leg + 15) <= start if leg is not None else end <= start):
                    edges[left].append((right, leg))

        # State: (path, total_cost, total_travel, largest_leg).
        State = tuple[tuple[int, ...], int, int, int]
        frontiers: dict[tuple[int, frozenset[str]], list[State]] = {}
        for index, (event, _, _, _) in enumerate(candidates):
            if budget is None or costs[index] <= budget:
                frontiers[(index, frozenset({event.category}))] = [((index,), costs[index], 0, 0)]

        def consider(path: tuple[int, ...], cost: int, travel_sum: int,
                     categories: frozenset[str], bottleneck: int) -> None:
            nonlocal best_path, best_cost, best_travel, best_bottleneck, best_key
            if objective == "quality" and path:
                key = (len(path), len(categories), -travel_sum, -cost, tuple(-index for index in path))
                if not best_key or key > best_key:
                    best_key, best_path = key, list(path)
                    best_cost, best_travel, best_bottleneck = cost, travel_sum, bottleneck
            elif objective == "cost" and len(path) == target_count:
                if not best_path or (cost, travel_sum, path) < (best_cost, best_travel, tuple(best_path)):
                    best_path, best_cost, best_travel, best_bottleneck = list(path), cost, travel_sum, bottleneck
            elif objective == "travel" and len(path) == target_count:
                if not best_path or (bottleneck, travel_sum, cost, path) < (
                        best_bottleneck, best_travel, best_cost, tuple(best_path)):
                    best_path, best_cost, best_travel, best_bottleneck = list(path), cost, travel_sum, bottleneck

        for length in range(1, target_count + 1):
            if not frontiers:
                break
            next_frontiers: dict[tuple[int, frozenset[str]], list[State]] = {}
            for (last, categories), states in frontiers.items():
                for path, cost, travel_sum, bottleneck in states:
                    consider(path, cost, travel_sum, categories, bottleneck)
                    if length == target_count:
                        continue
                    for next_index, leg in edges[last]:
                        next_cost = cost + costs[next_index]
                        if budget is not None and next_cost > budget:
                            continue
                        next_travel = travel_sum + (leg or 0)
                        next_bottleneck = max(bottleneck, leg or 0)
                        key = (next_index, categories | {candidates[next_index][0].category})
                        frontier = next_frontiers.setdefault(key, [])
                        if any(old[1] <= next_cost and old[2] <= next_travel and old[3] <= next_bottleneck
                               for old in frontier):
                            continue
                        frontier[:] = [old for old in frontier if not (
                            next_cost <= old[1] and next_travel <= old[2] and next_bottleneck <= old[3])]
                        frontier.append((path + (next_index,), next_cost, next_travel, next_bottleneck))
            frontiers = next_frontiers
        return best_path, best_cost, best_travel, best_bottleneck

    def build_weekend_plan(self, city: Optional[str] = None, date_from: Optional[str] = None,
                           date_to: Optional[str] = None, max_budget_total: Optional[int] = None,
                           party_size: int = 1, max_age_limit: Optional[int] = None,
                           max_travel_min: int = 25, target_count: int = 3,
                           ref_date: Optional[date] = None) -> WeekendPlanResult:
        if not 2 <= target_count <= 4 or not 1 <= party_size <= 20:
            raise ValueError("target_count must be 2..4 and party_size must be 1..20")
        if max_budget_total is not None and max_budget_total < 0:
            raise ValueError("max_budget_total must be non-negative")
        if max_travel_min < 0 or max_age_limit is not None and not 0 <= max_age_limit <= 18:
            raise ValueError("Invalid travel or age limit")
        today = ref_date or date.today()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        first = date.fromisoformat(date_from[:10]) if date_from else saturday
        last = date.fromisoformat(date_to[:10]) if date_to else (first if date_from else saturday + timedelta(days=1))
        if first > last:
            raise ValueError("date_from must not be after date_to")
        candidates = self._plan_candidates(city, first.isoformat(), last.isoformat(), max_age_limit)
        recommendations = self._unknown_duration_recommendations(
            city, first.isoformat(), last.isoformat(), max_age_limit)
        path, total_price, travel, _ = self._solve_plan(
            candidates, party_size, target_count, max_budget_total, max_travel_min)
        stops: list[ItineraryStop] = []
        for index in path:
            event, start, end, unit_price = candidates[index]
            leg = self._travel_minutes(stops[-1].event, event) if stops else 0
            stops.append(ItineraryStop(event, start, end, leg, unit_price, unit_price * party_size))
        self._with_sources([stop.event for stop in stops])
        unknown_travel = any(stop.travel_from_previous_min is None for stop in stops[1:])
        if unknown_travel:
            conflict = ("Предложенный порядок событий не подтверждает время переезда: "
                        "нет данных маршрутизации. Соблюдение лимита перемещения не подтверждено.")
        else:
            conflict = None

        if len(stops) < target_count:
            if max_budget_total is not None:
                relaxed, required, _, _ = self._solve_plan(
                    candidates, party_size, target_count, None, max_travel_min, "cost")
                if relaxed and required > max_budget_total:
                    difference = required - max_budget_total
                    conflict = (f"Чтобы посетить {target_count} события компанией из {party_size} чел., "
                                f"не хватает {difference} ₽ — увеличьте общий бюджет с "
                                f"{max_budget_total} ₽ до {required} ₽.")
            if conflict is None:
                relaxed, _, _, required_travel = self._solve_plan(
                    candidates, party_size, target_count, max_budget_total, None, "travel")
                if relaxed and required_travel > max_travel_min:
                    conflict = (f"Для {target_count} событий увеличьте допустимый переезд на "
                                f"{required_travel - max_travel_min} мин: с {max_travel_min} до {required_travel} мин.")
            if conflict is None:
                earliest = max(today, first - timedelta(days=30))
                future = self._plan_candidates(city, earliest.isoformat(),
                                               (last + timedelta(days=30)).isoformat(), max_age_limit)
                expansions = sorted(
                    [(last_offset, "to", next_date)
                     for next_date in {start.date() for _, start, _, _ in future if last < start.date()}
                     if (last_offset := (next_date - last).days) <= 30]
                    + [(first_offset, "from", next_date)
                       for next_date in {start.date() for _, start, _, _ in future if start.date() < first}
                       if (first_offset := (first - next_date).days) <= 30])
                for days, direction, next_date in expansions:
                    extended = [item for item in future if
                                (first <= item[1].date() <= next_date if direction == "to"
                                 else next_date <= item[1].date() <= last)]
                    feasible, _, _, _ = self._solve_plan(
                        extended, party_size, target_count, max_budget_total, max_travel_min)
                    if len(feasible) == target_count:
                        side = "до" if direction == "to" else "с"
                        conflict = (f"Для {target_count} событий расширьте период на {days} дн. — "
                                    f"{side} {next_date.isoformat()}.")
                        break
            if conflict is None:
                conflict = (f"В выбранных условиях нет маршрута из {target_count} опубликованных "
                            "событий с заданными ограничениями.")
        if unknown_travel and len(stops) < target_count:
            travel_warning = ("Предложенный порядок событий не подтверждает время переезда: "
                              "нет данных маршрутизации. Соблюдение лимита перемещения не подтверждено.")
            conflict = f"{conflict} {travel_warning}" if conflict else travel_warning
        total_travel = None if unknown_travel else travel
        return WeekendPlanResult(stops, total_price, total_travel, target_count, conflict, recommendations)
