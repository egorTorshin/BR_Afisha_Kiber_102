from datetime import date

from src.models.schemas import SearchFilters
from src.services.admin_catalog_service import AdminCatalogService
from src.services.import_service import ImportService
from src.services.recommendation_service import RecommendationService
from src.services.search_service import SearchService
from tests.test_backend import db, empty_db, event


def test_collected_performances_have_one_card_and_one_moderation_action(db):
    importer = ImportService(db)
    records = [
        event(title="Одна постановка", date_start="2026-12-20T10:00:00",
              source_url="https://example.com/show-1"),
        event(title="Одна постановка", date_start="2026-12-20T13:00:00",
              source_url="https://example.com/show-2"),
        event(title="Одна постановка", date_start="2026-12-21T10:00:00",
              source_url="https://example.com/show-3"),
    ]
    assert importer.import_records(records, source_file="official", source_type="страница",
                                   default_status="draft").added_count == 3
    admin = AdminCatalogService(db)
    moderation = admin.disputed_events()
    assert len(moderation) == 1
    assert len(moderation[0].schedule) == 3
    assert admin.overview()["moderation"] == 1
    importer.update_event_status(moderation[0].id, "published", actor="test")
    assert {row["status"] for row in db.fetchall("SELECT status FROM events")} == {"published"}
    page, total, _ = SearchService(db).search(SearchFilters())
    assert total == 1
    assert len(page[0].schedule) == 3
    assert len(page[0].sources) == 3
    profile_page, profile_total, _ = SearchService(db).search_for_profile(SearchFilters(), 54.7, 55.9)
    assert profile_total == 1
    assert len(profile_page[0].schedule) == 3
    assert len(profile_page[0].sources) == 3
    recommendations = RecommendationService(db).recommend_for_user("", ref_date=date(2026, 12, 1))
    assert len(recommendations) == 1
    assert len(recommendations[0].event.schedule) == 3
    importer.update_event_fields(page[0].id, {"title": "Новая редакция постановки"}, actor="test")
    assert {row["title"] for row in db.fetchall("SELECT title FROM events")} == {"Новая редакция постановки"}
    importer.archive_event(page[0].id, actor="test")
    assert {row["status"] for row in db.fetchall("SELECT status FROM events")} == {"draft"}


def test_same_title_with_different_description_stays_separate(db):
    importer = ImportService(db)
    records = [
        event(title="Вечер музыки", date_start="2026-12-20T10:00:00",
              source_url="https://example.com/a"),
        event(title="Вечер музыки", date_start="2026-12-21T10:00:00",
              description="Новая программа с другим составом и репертуаром.",
              source_url="https://example.com/b"),
    ]
    importer.import_records(records, source_file="official", source_type="страница",
                            default_status="draft")
    assert len(AdminCatalogService(db).disputed_events()) == 2


def test_date_introduction_joins_existing_schedule(db):
    importer = ImportService(db)
    description = ("Щелкунчик рассказывает историю Маши и её путешествия в волшебную страну. "
                   "Музыка Чайковского сопровождает спектакль для всей семьи. ") * 12
    dates = ["2026-12-12T19:00:00", "2026-12-28T20:00:00",
             "2026-12-29T20:00:00", "2027-01-03T18:00:00",
             "2027-01-04T18:00:00", "2027-01-05T18:00:00",
             "2027-01-06T18:00:00"]
    records = [event(title="Балет «Щелкунчик»", category="спектакль",
                     venue="ГКЗ «Башкортостан»", date_start=when,
                     description=("12 декабря состоится балет «Щелкунчик»! " if index == 0 else "") + description,
                     source_url=f"https://example.com/nutcracker-{index}")
               for index, when in enumerate(dates)]
    assert importer.import_records(records, "official", source_type="страница",
                                   default_status="draft").added_count == 7
    moderation = AdminCatalogService(db).disputed_events()
    assert len(moderation) == 1
    assert [show["date_start"] for show in moderation[0].schedule] == dates
    importer.update_event_status(moderation[0].id, "published", actor="test")
    assert {row["status"] for row in db.fetchall("SELECT status FROM events")} == {"published"}
    search = SearchService(db)
    page, total, _ = search.search(SearchFilters())
    assert total == 1
    assert len(page[0].schedule) == 7
    assert len(search.get_event_by_id(page[0].id).schedule) == 7
    filtered, total, _ = search.search(SearchFilters(date_from="2027-01-01T00:00:00"))
    assert total == 1
    assert len(filtered[0].schedule) == 7


def test_similar_title_with_different_program_stays_separate(db):
    importer = ImportService(db)
    common = "Музыкальный фестиваль в исполнении приглашённых артистов. " * 4
    records = [
        event(title="Музыкальный фестиваль", description=common + "Выступает оркестр народных инструментов. " * 8,
              source_url="https://example.com/first"),
        event(title="Музыкальный фестиваль", date_start="2026-11-21T18:00:00",
              description=common + "Выступает струнный квартет с другой программой. " * 8,
              source_url="https://example.com/second"),
    ]
    importer.import_records(records, "official", source_type="страница", default_status="draft")
    assert len(AdminCatalogService(db).disputed_events()) == 2
