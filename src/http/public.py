"""Public catalog and feedback HTTP routes."""
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from src.config import Settings
from src.db.connection import Database
from src.http.schemas import (CategoryItem, CityItem, EventPage, EventQuery, FeedbackInput,
                              HealthResponse, PublicEvent, VenueItem, VersionedEventPage,
                              VersionedEventQuery, SubscriptionInput, RecommendationsResponse,
                              WeekendPlanInput, WeekendPlanResponse)
from src.models.schemas import FeedbackRecord, SearchFilters
from src.services.feedback_service import FeedbackService
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
        return run_service(
            recommendations.build_weekend_plan,
            city=data.city, date_from=data.date_from.isoformat() if data.date_from else None,
            date_to=data.date_to.isoformat() if data.date_to else None,
            max_budget_total=data.max_budget_total, party_size=data.party_size,
            max_age_limit=data.max_age_limit, max_travel_min=data.max_travel_min,
            target_count=data.target_count,
            ref_date=settings.get_reference_date() if settings else None,
        )

    @router.get("/events", response_model=EventPage)
    def list_events(query: Annotated[EventQuery, Query()]):
        events, total, explanation = search.search(SearchFilters(**query.model_dump(exclude={"sort"})), sort=query.sort)
        return EventPage(events=events, total=total, explanation=explanation)

    @router.get("/api/v1/events", response_model=VersionedEventPage)
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

    @router.get("/api/v1/cities", response_model=list[CityItem])
    def cities():
        return search.list_cities_with_counts()

    @router.get("/api/v1/categories", response_model=list[CategoryItem])
    def categories(city: Optional[str] = None):
        return search.list_categories_with_counts(city)

    @router.get("/api/v1/venues", response_model=list[VenueItem])
    def venues(city: Optional[str] = None):
        return search.list_public_venues(city)

    @router.get("/api/v1/events/{event_id}", response_model=PublicEvent)
    @router.get("/events/{event_id}", response_model=PublicEvent)
    def get_event(event_id: str):
        result = search.get_event_by_id(event_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Событие не найдено")
        return result

    @router.post("/api/v1/feedback", response_model=FeedbackRecord, status_code=201)
    @router.post("/feedback", response_model=FeedbackRecord, status_code=201)
    def submit_feedback(data: FeedbackInput):
        event_id = data.event_id
        if event_id is not None:
            event = search.get_event_by_id(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Событие не найдено")
            event_id = event.id
        return run_service(feedback.submit, data.message, event_id, actor="api:anonymous")

    @router.get("/api/v1/subscriptions")
    def list_subscriptions(subscriber_id: str = Query(min_length=1, max_length=128)):
        return run_service(subscriptions.list_subscriptions, subscriber_id)

    @router.post("/api/v1/subscriptions", status_code=201)
    def create_subscription(data: SubscriptionInput):
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
