import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VALID_CATEGORIES = {
    "концерт",
    "спектакль",
    "выставка",
    "фестиваль",
    "кино",
    "экскурсия",
    "другое",
}

VALID_STATUSES = {
    "опубликовано",
    "черновик",
    "отменено",
}

VALID_SOURCE_TYPES = {
    "ручной ввод",
    "импорт",
    "RSS",
    "API",
    "страница",
}

VALID_AGE_LIMITS = {0, 6, 12, 16, 18}


@dataclass
class EventRecord:
    id: str
    title: str
    normalized_title: str
    category: str
    tags: List[str]
    date_start: str
    date_end: Optional[str]
    duration_min: Optional[int]
    city: str
    venue: str
    address: str
    lat: Optional[float]
    lon: Optional[float]
    description: str
    price_min: int
    price_max: int
    is_free: bool
    age_limit: int
    language: Optional[str]
    org_link: Optional[str]
    source_url: str
    source_type: str
    source_hash: str
    verified_at: str
    confidence: float
    status: str
    dedup_key: str
    is_disputed: bool
    dispute_reason: Optional[str]
    created_at: str
    updated_at: str
    raw_event_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "EventRecord":
        raw_tags = row.get("tags", "[]")
        if isinstance(raw_tags, str):
            try:
                tags = json.loads(raw_tags)
            except Exception:
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        else:
            tags = list(raw_tags)
        return cls(
            id=str(row["id"]),
            raw_event_id=row.get("raw_event_id"),
            title=row["title"],
            normalized_title=row["normalized_title"],
            category=row["category"],
            tags=tags,
            date_start=row["date_start"],
            date_end=row.get("date_end"),
            duration_min=row.get("duration_min"),
            city=row["city"],
            venue=row["venue"],
            address=row["address"],
            lat=float(row["lat"]) if row.get("lat") is not None else None,
            lon=float(row["lon"]) if row.get("lon") is not None else None,
            description=row["description"],
            price_min=int(row.get("price_min") or 0),
            price_max=int(row.get("price_max") or 0),
            is_free=bool(row.get("is_free")),
            age_limit=int(row.get("age_limit") or 0),
            language=row.get("language"),
            org_link=row.get("org_link"),
            source_url=row["source_url"],
            source_type=row["source_type"],
            source_hash=row["source_hash"],
            verified_at=row["verified_at"],
            confidence=float(row.get("confidence", 1.0)),
            status=row["status"],
            dedup_key=row["dedup_key"],
            is_disputed=bool(row.get("is_disputed")),
            dispute_reason=row.get("dispute_reason"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class SearchFilters:
    query_text: Optional[str] = None
    city: Optional[str] = None
    venue: Optional[str] = None
    category: Optional[str] = None
    date_preset: Optional[str] = None  # today, tomorrow, weekend, week, custom
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    time_after: Optional[str] = None  # HH:MM
    time_before: Optional[str] = None  # HH:MM
    max_duration_min: Optional[int] = None
    max_price: Optional[int] = None
    is_free_only: bool = False
    max_age_limit: Optional[int] = None
    language: Optional[str] = None
    tag: Optional[str] = None
    limit: int = 5
    offset: int = 0

    def is_empty(self) -> bool:
        return not any([
            self.query_text,
            self.city,
            self.venue,
            self.category,
            self.date_preset,
            self.date_from,
            self.date_to,
            self.time_after,
            self.time_before,
            self.max_duration_min is not None,
            self.max_price is not None,
            self.is_free_only,
            self.max_age_limit is not None,
            self.language,
            self.tag,
        ])

    def describe_human(self) -> str:
        parts = []
        if self.city:
            parts.append(f"город: {self.city}")
        if self.category:
            parts.append(f"категория: {self.category}")
        if self.venue:
            parts.append(f"площадка: {self.venue}")
        if self.date_preset:
            preset_names = {
                "today": "сегодня",
                "tomorrow": "завтра",
                "weekend": "выходные",
                "week": "ближайшая неделя",
                "month": "до конца месяца",
            }
            parts.append(f"период: {preset_names.get(self.date_preset, self.date_preset)}")
        elif self.date_from or self.date_to:
            if self.date_from and self.date_to and self.date_from[:10] == self.date_to[:10]:
                parts.append(f"дата: {self.date_from[:10]}")
            else:
                parts.append(f"период: {self.date_from or '...'} — {self.date_to or '...'}")
        if self.time_after:
            parts.append(f"начало после {self.time_after}")
        if self.time_before:
            parts.append(f"окончание до {self.time_before}")
        if self.max_duration_min is not None:
            parts.append(f"длительность до {self.max_duration_min} мин")
        if self.is_free_only:
            parts.append("цена: бесплатно")
        elif self.max_price is not None:
            parts.append(f"цена: до {self.max_price} ₽")
        if self.max_age_limit is not None:
            parts.append(f"возраст: до {self.max_age_limit}+")
        if self.language:
            parts.append(f"язык: {self.language}")
        if self.query_text:
            parts.append(f"текст: «{self.query_text}»")
        return ", ".join(parts) if parts else "все опубликованные события"


@dataclass
class ImportReport:
    report_id: str
    source_file: str
    actor: str
    total_rows: int
    added_count: int
    updated_count: int
    unchanged_count: int
    rejected_count: int
    disputed_count: int
    rejected_reasons: List[Dict[str, Any]] = field(default_factory=list)
    disputed_items: List[Dict[str, Any]] = field(default_factory=list)
