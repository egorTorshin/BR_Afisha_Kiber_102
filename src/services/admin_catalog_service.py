"""Database queries for the administrative catalog and audit views."""

import json

from src.db.connection import Database
from src.models.schemas import EventRecord
from src.services.event_series import attach_sources, group_rows, scheduled_record


class AdminCatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_events(self, query: str = "", status: str = "", page: int = 1) -> dict:
        clauses, params = ["merged_into IS NULL"], []
        if query:
            clauses.append("(title LIKE ? OR city LIKE ? OR venue LIKE ?)")
            params.extend([f"%{query}%"] * 3)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.fetchall("SELECT * FROM events" + where, params)
        groups = group_rows(rows)
        groups.sort(key=lambda group: max(row["updated_at"] for row in group), reverse=True)
        selected = groups[(page - 1) * 30:page * 30]
        events = [scheduled_record(group) for group in selected]
        attach_sources(self.db, events, selected)
        return {"events": events, "total": len(groups), "page": page}

    def disputed_events(self) -> list[EventRecord]:
        rows = self.db.fetchall(
            "SELECT * FROM events WHERE (is_disputed = 1 OR (raw_event_id IS NOT NULL AND status = 'draft')) "
            "AND merged_into IS NULL ORDER BY updated_at DESC"
        )
        groups = group_rows(rows)
        groups.sort(key=lambda group: max(row["updated_at"] for row in group), reverse=True)
        result = [scheduled_record(group) for group in groups[:100]]
        attach_sources(self.db, result, groups[:100])
        return result

    def overview(self) -> dict:
        groups = group_rows(self.db.fetchall("SELECT * FROM events WHERE merged_into IS NULL"))
        statuses: dict[str, int] = {}
        for group in groups:
            status = group[0]["status"]
            statuses[status] = statuses.get(status, 0) + 1
        return {
            "events": sum(statuses.values()), "published": statuses.get("published", 0),
            "draft": statuses.get("draft", 0), "cancelled": statuses.get("cancelled", 0),
            "moderation": sum(any(row["is_disputed"] or (row["raw_event_id"] is not None and row["status"] == "draft")
                                   for row in group) for group in groups)
                          + self.db.fetchone("SELECT COUNT(*) AS n FROM event_submissions WHERE status = 'pending'")["n"],
            "feedback": self.db.fetchone("SELECT COUNT(*) AS n FROM feedback WHERE status IN ('pending','in_progress')")["n"],
            "users": self.db.fetchone("SELECT COUNT(*) AS n FROM users")["n"],
        }

    def recent_audit(self) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200")
        for row in rows:
            row["changes"] = json.loads(row.pop("changes_json"))
        return rows

    def recent_traces(self) -> list[dict]:
        return self.db.fetchall("SELECT request_id, created_at FROM agent_traces ORDER BY created_at DESC LIMIT 100")
