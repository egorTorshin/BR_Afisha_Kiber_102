"""Group performances of the same collected production into one visible card."""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from src.models.schemas import EventRecord, EventSource
from src.services.normalizer import normalize_text
def series_key(row: dict[str, Any]) -> tuple[str, ...]:
    # Only collected page records are grouped. Venue and category prevent
    # productions with a shared title at different places from being combined.
    if row.get("source_type") not in {"страница", "ручной ввод"} or not row.get("raw_event_id"):
        return ("single", str(row["id"]))
    return ("series", row["normalized_title"], row["search_city"],
            row["search_venue"], row["category"], row.get("language") or "",
            row["source_url"] if row["source_type"] == "ручной ввод" else "")


def same_series(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if series_key(left) != series_key(right):
        return False
    first = normalize_text(left.get("description") or "")
    second = normalize_text(right.get("description") or "")
    if first == second:
        return True
    # A date-specific introduction or a small copy edit must not split the
    # schedule. Short descriptions need an exact match to avoid false merges.
    return min(len(first), len(second)) >= 80 and SequenceMatcher(
        None, first, second, autojunk=False).ratio() >= 0.9


def group_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    buckets: dict[tuple[str, ...], list[list[dict[str, Any]]]] = defaultdict(list)
    for row in sorted(rows, key=lambda item: (item["date_start"], item["id"])):
        groups = buckets[series_key(row)]
        group = next((group for group in groups if all(same_series(member, row) for member in group)), None)
        if group is None:
            groups.append([row])
        else:
            group.append(row)
    return [group for groups in buckets.values() for group in groups]


def matching_series(rows: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    return next((group for group in group_rows(rows) if any(row["id"] == target["id"] for row in group)), [target])


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
