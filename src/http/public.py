"""Public catalog and feedback HTTP routes."""
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query

from src.db.connection import Database
from src.http.schemas import (CategoryItem, CityItem, EventPage, EventQuery, FeedbackInput,
                              HealthResponse, PublicEvent, VenueItem, VersionedEventPage,
                              VersionedEventQuery)
from src.models.schemas import FeedbackRecord, SearchFilters
from src.services.feedback_service import FeedbackService
from src.services.search_service import SearchService
from src.http.errors import run_service
from src.utils.metrics import DATABASE_UP, DATABASE_LATENCY
import time


def create_public_router(database: Database) -> APIRouter:
    router = APIRouter()
    search = SearchService(database)
    feedback = FeedbackService(database)

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

    @router.post("/feedback", response_model=FeedbackRecord, status_code=201)
    def submit_feedback(data: FeedbackInput):
        event_id = data.event_id
        if event_id is not None:
            event = search.get_event_by_id(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Событие не найдено")
            event_id = event.id
        return run_service(feedback.submit, data.message, event_id, actor="api:anonymous")

    return router
