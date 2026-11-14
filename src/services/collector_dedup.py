"""Shared duplicate checks for independently collected venue listings."""

from __future__ import annotations

from src.services.import_service import ImportService
from src.services.normalizer import normalize_text


def find_existing(importer: ImportService, normalized: dict) -> tuple[str | None, dict | None]:
    """Return an exact event, or a same-title/time/city match needing review."""
    exact = importer.db.fetchone(
        "SELECT id, source_url FROM events WHERE merged_into IS NULL AND dedup_key = ? LIMIT 1",
        (normalized["dedup_key"],),
    )
    if exact:
        return "exact", exact
    ambiguous = importer.db.fetchone(
        """SELECT id FROM events WHERE merged_into IS NULL
           AND normalized_title = ? AND date_start = ? AND search_city = ? LIMIT 1""",
        (normalized["normalized_title"], normalized["date_start"],
         normalize_text(normalized["city"])),
    )
    return ("ambiguous", ambiguous) if ambiguous else (None, None)
