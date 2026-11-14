"""Group performances of the same collected production into one visible card."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.models.schemas import EventRecord, EventSource
def series_key(row: dict[str, Any]) -> tuple[str, ...]:
    # Only collected page records are grouped. Matching description and venue
    # protect unrelated productions with a common title from being combined.
    if row.get("source_type") not in {"страница", "ручной ввод"} or not row.get("raw_event_id"):
        return ("single", str(row["id"]))
    return ("series", row["normalized_title"], row["search_city"],
            row["search_venue"], row["description"],
            row["category"], row.get("language") or "",
            row["source_url"] if row["source_type"] == "ручной ввод" else "")


def series_partition_sql() -> str:
    """Equivalent SQL grouping key for bounded public pagination."""
    collected = "source_type IN ('страница', 'ручной ввод') AND raw_event_id IS NOT NULL"
    fields = ("normalized_title", "search_city", "search_venue", "description",
              "category", "COALESCE(language, '')")
    return ", ".join([f"CASE WHEN {collected} THEN 'series' ELSE id END",
                      *(f"CASE WHEN {collected} THEN {field} ELSE '' END" for field in fields),
                      "CASE WHEN source_type = 'ручной ввод' AND raw_event_id IS NOT NULL "
                      "THEN source_url ELSE '' END"])


def group_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[series_key(row)].append(row)
    return [sorted(group, key=lambda row: (row["date_start"], row["id"]))
            for group in grouped.values()]


def scheduled_record(group: list[dict[str, Any]], *, representative: dict[str, Any] | None = None) -> EventRecord:
    record = EventRecord.from_row(representative or group[0])
    seen_dates: set[str] = set()
    for row in group:
        date_start = row["date_start"]
        if date_start in seen_dates:
            continue
        seen_dates.add(date_start)
        record.schedule.append({"date_start": date_start, "date_end": row.get("date_end"),
                                "source_url": row["source_url"]})
    return record


def attach_sources(db, records: list[EventRecord], groups: list[list[dict[str, Any]]]) -> None:
    if not records:
        return
    ids = [row["id"] for group in groups for row in group]
    placeholders = ", ".join("?" for _ in ids)
    source_rows = db.fetchall(
        f"SELECT * FROM event_sources WHERE event_id IN ({placeholders}) ORDER BY source_url", tuple(ids))
    by_id = {row["id"]: index for index, group in enumerate(groups) for row in group}
    seen: list[set[str]] = [set() for _ in records]
    for source in source_rows:
        index = by_id[source["event_id"]]
        if source["source_url"] not in seen[index]:
            records[index].sources.append(EventSource(**source))
            seen[index].add(source["source_url"])
