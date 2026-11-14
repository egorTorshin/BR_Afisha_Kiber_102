from datetime import date

from src.services.normalizer import parse_free_text_query


def test_explicit_russian_year_is_preserved_and_consumed():
    filters = parse_free_text_query("Найди концерты в Уфе 1 января 2099 года", date(2026, 11, 14))
    assert filters.date_from == "2099-01-01T00:00:00"
    assert filters.date_to == "2099-01-01T23:59:59"
    assert "2099" not in (filters.query_text or "")
