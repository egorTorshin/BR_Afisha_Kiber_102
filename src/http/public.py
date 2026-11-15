"""Public catalog and feedback HTTP routes."""
from typing import Annotated, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.config import Settings
from src.db.connection import Database
from src.http.schemas import (CategoryItem, CityItem, EventPage, EventQuery, FeedbackInput,
                              HealthResponse, PublicEvent, VenueItem, VersionedEventPage,
                              VersionedEventQuery, SubscriptionInput, RecommendationsResponse,
                              WeekendPlanInput, WeekendPlanResponse, EventSubmissionInput)
from src.models.schemas import FeedbackRecord, SearchFilters
from src.services.feedback_service import FeedbackService
from src.services.event_submission_service import EventSubmissionService
from src.http.security import check_browser_origin
from src.services.search_service import SearchService
from src.services.subscription_service import SubscriptionService
from src.services.recommendation_service import RecommendationService
from src.services.user_service import UserService
from src.http.errors import run_service
from src.utils.metrics import DATABASE_UP, DATABASE_LATENCY
import time


def create_public_router(database: Database, settings: Settings | None = None) -> APIRouter:
    router = APIRouter()
    search = SearchService(database)
    feedback = FeedbackService(database)
    submissions = EventSubmissionService(database)
    subscriptions = SubscriptionService(database)
    recommendations = RecommendationService(database)
    users = UserService(database)

    @router.get("/api/v1/recommendations", response_model=RecommendationsResponse)
    def get_recommendations(request: Request, user_id: Optional[str] = Query(default=None, max_length=128),
                            city: Optional[str] = Query(default=None, max_length=120),
                            limit: int = Query(default=5, ge=1, le=20)):
        session = users.session(request.cookies.get("user_session", ""))
        if user_id and not session:
            raise HTTPException(status_code=401, detail="Для персональной подборки требуется вход")
        if user_id and session and user_id != session["id"]:
            raise HTTPException(status_code=403, detail="Чужая история просмотров недоступна")
        principal = session["id"] if session else ""
        items = run_service(recommendations.recommend_for_user, principal, city=city, limit=limit,
                            ref_date=settings.get_reference_date() if settings else None)
        return RecommendationsResponse(items=[{"event": item.event, "score": item.score,
                                               "match_reason": item.match_reason} for item in items])

    @router.post("/api/v1/weekend-plan", response_model=WeekendPlanResponse)
    def weekend_plan(data: WeekendPlanInput):
        result = run_service(
            recommendations.build_weekend_plan,
            city=data.city, date_from=data.date_from.isoformat() if data.date_from else None,
            date_to=data.date_to.isoformat() if data.date_to else None,
            max_budget_total=data.max_budget_total, party_size=data.party_size,
            max_age_limit=data.max_age_limit, max_travel_min=data.max_travel_min,
            target_count=data.target_count,
            ref_date=settings.get_reference_date() if settings else None,
        )
        return {
            "stops": result.stops,
            "total_price": result.total_price,
            "total_travel_min": result.total_travel_min,
            "target_count": result.target_count,
            "conflict_explanation": result.conflict_explanation,
            "recommendations": [{"event": event, "warning": warning}
                                for event, warning in (result.recommendations or [])],
        }

    @router.get("/events", response_model=EventPage)
    def list_events(query: Annotated[EventQuery, Query()]):
        events, total, explanation = search.search(SearchFilters(**query.model_dump(exclude={"sort"})), sort=query.sort)
        return EventPage(events=events, total=total, explanation=explanation)

    @router.get("/api/v1/events", response_model=VersionedEventPage, openapi_extra={
        "x-codeSamples": [{"lang": "curl", "label": "Поиск событий",
                           "source": "curl -G 'https://afisha.example/api/v1/events' --data-urlencode 'city=Уфа' --data-urlencode 'category=концерт'"}],
    }, responses={200: {"content": {"application/json": {"example": {
        "events": [{"id": "evt_123", "title": "Концерт камерного оркестра", "category": "концерт",
                    "tags": ["классика"], "date_start": "2026-12-12T19:00:00", "date_end": None,
                    "duration_min": 90, "city": "Уфа", "venue": "Башкирская государственная филармония",
                    "address": "ул. Гоголя, 58", "lat": None, "lon": None,
                    "description": "Вечер камерной музыки.", "price_min": 500, "price_max": 1500,
                    "is_free": False, "age_limit": 6, "language": None, "org_link": None,
                    "source_url": "https://example.org/event", "source_type": "API",
                    "verified_at": "2026-11-15T12:00:00", "confidence": 1.0, "status": "published",
                    "is_disputed": False, "dispute_reason": None, "sources": [], "schedule": []}],
        "total": 1, "explanation": None, "page": 1, "page_size": 20, "pages": 1}}}}})
    def list_events_v1(query: Annotated[VersionedEventQuery, Query()]):
        filters = SearchFilters(**query.model_dump(exclude={"page", "page_size", "sort", "limit", "offset"}),
                                limit=query.page_size, offset=(query.page - 1) * query.page_size)
        events, total, explanation = search.search(filters, sort=query.sort)
        return VersionedEventPage(events=events, total=total, explanation=explanation,
                                  page=query.page, page_size=query.page_size,
                                  pages=(total + query.page_size - 1) // query.page_size)

    @router.get("/health", response_model=HealthResponse)
    def health():
        started = time.perf_counter()
        kind = "postgresql" if database.is_postgres else "sqlite"
        try:
            database.ping()
            DATABASE_UP.labels(kind).set(1)
        except Exception:
            DATABASE_UP.labels(kind).set(0)
            raise
        finally:
            DATABASE_LATENCY.labels(kind).observe(time.perf_counter() - started)
        return HealthResponse(status="ok")

    @router.get("/api/v1/cities", response_model=list[CityItem],
                responses={200: {"content": {"application/json": {"example": [{"city": "Уфа", "cnt": 12}]}}}})
    def cities():
        return search.list_cities_with_counts()

    @router.get("/api/v1/categories", response_model=list[CategoryItem],
                responses={200: {"content": {"application/json": {"example": [{"category": "концерт", "cnt": 4}]}}}})
    def categories(city: Optional[str] = None):
        return search.list_categories_with_counts(city)

    @router.get("/api/v1/venues", response_model=list[VenueItem],
                responses={200: {"content": {"application/json": {"example": [{"city": "Уфа", "name": "Городской дворец культуры", "address": "ул. Ленина, 1"}]}}}})
    def venues(city: Optional[str] = None):
        return search.list_public_venues(city)

    @router.get("/api/v1/events/{event_id}", response_model=PublicEvent, responses={200: {"content": {"application/json": {"example": {
        "id": "evt_123", "title": "Концерт камерного оркестра", "category": "концерт", "tags": ["классика"],
        "date_start": "2026-12-12T19:00:00", "date_end": None, "duration_min": 90, "city": "Уфа",
        "venue": "Башкирская государственная филармония", "address": "ул. Гоголя, 58", "lat": None, "lon": None,
        "description": "Вечер камерной музыки.", "price_min": 500, "price_max": 1500, "is_free": False,
        "age_limit": 6, "language": None, "org_link": None, "source_url": "https://example.org/event",
        "source_type": "API", "verified_at": "2026-11-15T12:00:00", "confidence": 1.0, "status": "published",
        "is_disputed": False, "dispute_reason": None, "sources": [], "schedule": []}}}}})
    @router.get("/events/{event_id}", response_model=PublicEvent)
    def get_event(event_id: str):
        result = search.get_event_by_id(event_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Событие не найдено")
        return result

    @router.post("/api/v1/feedback", response_model=FeedbackRecord, status_code=201, responses={201: {"content": {"application/json": {"example": {
        "id": "fb_123", "event_id": "evt_123", "message": "Проверьте дату начала события.",
        "status": "pending", "created_at": "2026-11-15T12:00:00"}}}}})
    @router.post("/feedback", response_model=FeedbackRecord, status_code=201)
    def submit_feedback(data: Annotated[FeedbackInput, Body(openapi_examples={"event_feedback": {
            "summary": "Замечание к событию",
            "value": {"event_id": "evt_123", "message": "Проверьте дату начала события."}}})]):
        event_id = data.event_id
        if event_id is not None:
            event = search.get_event_by_id(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Событие не найдено")
            event_id = event.id
        return run_service(feedback.submit, data.message, event_id, actor="api:anonymous")

    @router.post("/api/v1/event-submissions", status_code=201)
    def submit_event(data: EventSubmissionInput, request: Request):
        if settings:
            check_browser_origin(request, settings)
        return run_service(submissions.submit, data.model_dump(exclude_none=True))

    @router.get("/api/v1/subscriptions", responses={200: {"content": {"application/json": {"example": [{
        "id": "sub_123", "subscriber_id": "user_123", "city": "Уфа", "category": "концерт",
        "period": None, "created_at": "2026-11-15T12:00:00"}]}}}})
    def list_subscriptions(subscriber_id: str = Query(min_length=1, max_length=128)):
        return run_service(subscriptions.list_subscriptions, subscriber_id)

    @router.post("/api/v1/subscriptions", status_code=201,
        responses={201: {"content": {"application/json": {"example": {"id": "sub_123", "subscriber_id": "user_123",
        "city": "Уфа", "category": "концерт", "period": {"date_from": "2026-12-01", "date_to": "2026-12-31"},
        "created_at": "2026-11-15T12:00:00"}}}}})
    def create_subscription(data: Annotated[SubscriptionInput, Body(openapi_examples={"concerts_in_ufa": {
            "summary": "Подписка на концерты в Уфе",
            "value": {"subscriber_id": "user_123", "city": "Уфа", "category": "концерт",
                      "period": {"date_from": "2026-12-01", "date_to": "2026-12-31"}}}})]):
        return run_service(subscriptions.subscribe, data.subscriber_id, data.city, data.category,
                           period=data.period)

    @router.delete("/api/v1/subscriptions/{subscription_id}")
    def delete_subscription(subscription_id: str, subscriber_id: str = Query(min_length=1, max_length=128)):
        deleted = run_service(subscriptions.unsubscribe_by_id, subscriber_id, subscription_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Подписка не найдена")
        return {"deleted": deleted}

    @router.delete("/api/v1/subscriptions")
    def delete_all_subscriptions(subscriber_id: str = Query(min_length=1, max_length=128)):
        return {"deleted": run_service(subscriptions.unsubscribe_all, subscriber_id)}

    return router
