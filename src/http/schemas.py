"""HTTP request and response models."""
from typing import Optional, Literal
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.schemas import FeedbackRecord


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Optional[str] = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=10000)


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscriber_id: str = Field(min_length=1, max_length=128)
    city: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=100)
    period: Optional[dict[Literal["date_from", "date_to"], date]] = None

    @model_validator(mode="after")
    def validate_subscription_period(self):
        if self.period and self.period.get("date_from") and self.period.get("date_to"):
            if self.period["date_from"] > self.period["date_to"]:
                raise ValueError("Начало периода позже окончания")
        return self


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class EventStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["published", "draft", "cancelled", "опубликовано", "черновик", "отменено"]


class FeedbackStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "in_progress", "resolved", "rejected"]


class AdminEventPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=2, max_length=250)
    category: Optional[str] = Field(default=None, max_length=100)
    date_start: Optional[str] = Field(default=None, max_length=40)
    date_end: Optional[str] = Field(default=None, max_length=40)
    city: Optional[str] = Field(default=None, max_length=120)
    venue: Optional[str] = Field(default=None, max_length=250)
    address: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10000)
    source_url: Optional[str] = Field(default=None, max_length=2048)
    org_link: Optional[str] = Field(default=None, max_length=2048)
    duration_min: Optional[int] = Field(default=None, ge=1, le=1440)
    price_min: Optional[int] = Field(default=None, ge=0)
    price_max: Optional[int] = Field(default=None, ge=0)
    is_free: Optional[bool] = None
    age_limit: Optional[int] = Field(default=None, ge=0, le=18)
    language: Optional[str] = Field(default=None, max_length=100)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    status: Optional[Literal["published", "draft", "cancelled"]] = None


class EventSubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=2, max_length=250)
    category: Literal["концерт", "спектакль", "выставка", "фестиваль", "кино", "экскурсия", "другое"]
    date_start: str = Field(min_length=16, max_length=40)
    date_end: Optional[str] = Field(default=None, max_length=40)
    city: str = Field(min_length=2, max_length=120)
    venue: str = Field(min_length=2, max_length=250)
    address: str = Field(min_length=2, max_length=500)
    description: str = Field(min_length=20, max_length=10000)
    source_url: str = Field(min_length=10, max_length=2048)
    org_link: Optional[str] = Field(default=None, max_length=2048)
    price_min: Optional[int] = Field(default=None, ge=0)
    price_max: Optional[int] = Field(default=None, ge=0)
    is_free: bool = False
    age_limit: Literal[0, 6, 12, 16, 18] = 0


class SubmissionReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["approved", "rejected"]
    note: Optional[str] = Field(default=None, max_length=1000)


class PublicEventSource(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_url: str
    source_type: str
    verified_at: str


class PublicEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    category: str
    tags: list[str]
    date_start: str
    date_end: Optional[str]
    duration_min: Optional[int]
    city: str
    venue: str
    address: str
    lat: Optional[float]
    lon: Optional[float]
    description: str
    price_min: Optional[int]
    price_max: Optional[int]
    is_free: bool
    age_limit: int
    language: Optional[str]
    org_link: Optional[str]
    source_url: str
    source_type: str
    verified_at: str
    confidence: float
    status: str
    is_disputed: bool
    dispute_reason: Optional[str]
    sources: list[PublicEventSource]


class RecommendationItem(BaseModel):
    event: PublicEvent
    score: float = Field(ge=0, le=1)
    match_reason: str


class RecommendationsResponse(BaseModel):
    items: list[RecommendationItem]


class WeekendPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: Optional[str] = Field(default=None, max_length=120)
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    max_budget_total: Optional[int] = Field(default=None, ge=0)
    party_size: int = Field(default=1, ge=1, le=20)
    max_age_limit: Optional[int] = Field(default=None, ge=0, le=18)
    max_travel_min: int = Field(default=25, ge=0, le=600)
    target_count: int = Field(default=3, ge=2, le=4)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class ItineraryStopItem(BaseModel):
    event: PublicEvent
    start_time: datetime
    end_time: datetime
    travel_from_previous_min: int
    unit_price: int
    party_price: int


class WeekendPlanResponse(BaseModel):
    stops: list[ItineraryStopItem]
    total_price: int
    total_travel_min: int
    target_count: int
    conflict_explanation: Optional[str] = None


class AdminEvent(PublicEvent):
    normalized_title: str
    source_hash: str
    dedup_key: str
    raw_event_id: Optional[str]
    created_at: str
    updated_at: str


class AdminEventPage(BaseModel):
    events: list[AdminEvent]
    total: int
    page: int


class AuditEntry(BaseModel):
    id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    changes: dict
    created_at: str


class EventPage(BaseModel):
    events: list[PublicEvent]
    total: int
    explanation: Optional[str] = None


class EventFilters(BaseModel):
    query_text: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=120)
    venue: Optional[str] = Field(default=None, max_length=250)
    category: Optional[str] = Field(default=None, max_length=100)
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    time_after: Optional[str] = None
    time_before: Optional[str] = None
    max_duration_min: Optional[int] = Field(default=None, ge=1)
    max_price: Optional[int] = Field(default=None, ge=0)
    is_free_only: bool = False
    max_age_limit: Optional[int] = Field(default=None, ge=0, le=18)
    language: Optional[str] = Field(default=None, max_length=100)
    tag: Optional[str] = Field(default=None, max_length=100)
    sort: Literal["date_asc", "date_desc", "price_asc"] = "date_asc"

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_date(cls, value, info):
        if value is None:
            return value
        try:
            if len(value) == 10:
                day = date.fromisoformat(value)
                return day.isoformat() + ("T23:59:59" if info.field_name == "date_to" else "T00:00:00")
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo("Asia/Yekaterinburg")).replace(tzinfo=None)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            raise ValueError("Укажите дату ISO 8601: YYYY-MM-DD или дату со временем") from None

    @field_validator("time_after", "time_before")
    @classmethod
    def validate_time(cls, value):
        if value is not None:
            try:
                parsed = datetime.strptime(value, "%H:%M")
                if parsed.strftime("%H:%M") != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("Время должно иметь формат HH:MM") from None
        return value

    @model_validator(mode="after")
    def validate_period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Начало периода должно быть не позже окончания")
        if self.time_after and self.time_before and self.time_after > self.time_before:
            raise ValueError("Начало интервала времени должно быть не позже окончания")
        return self


class EventQuery(EventFilters):
    limit: int = Field(default=5, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class VersionedEventQuery(EventFilters):
    page: int = Field(default=1, ge=1, le=5000)
    page_size: int = Field(default=20, ge=1, le=100)


class VersionedEventPage(EventPage):
    page: int
    page_size: int
    pages: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
    detail: str  # Compatibility with clients reading the old HTTPException detail.


class CityItem(BaseModel):
    city: str
    cnt: int


class CategoryItem(BaseModel):
    category: str
    cnt: int


class VenueItem(BaseModel):
    city: str
    name: str
    address: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


