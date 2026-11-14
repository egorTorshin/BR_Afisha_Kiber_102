"""Database queries for the administrative catalog and audit views."""

import json

from src.db.connection import Database
from src.models.schemas import EventRecord, EventSource


class AdminCatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_events(self, query: str = "", status: str = "", page: int = 1) -> dict:
        clauses, params = [], []
        if query:
            clauses.append("(title LIKE ? OR city LIKE ? OR venue LIKE ?)")
            params.extend([f"%{query}%"] * 3)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = self.db.fetchone("SELECT COUNT(*) AS n FROM events" + where, params)["n"]
        rows = self.db.fetchall(
            "SELECT * FROM events" + where + " ORDER BY updated_at DESC LIMIT 30 OFFSET ?",
            (*params, (page - 1) * 30),
        )
        return {"events": [EventRecord.from_row(row) for row in rows], "total": total, "page": page}

    def disputed_events(self) -> list[EventRecord]:
        rows = self.db.fetchall(
            "SELECT * FROM events WHERE (is_disputed = 1 OR (raw_event_id IS NOT NULL AND status = 'draft')) "
            "AND merged_into IS NULL ORDER BY updated_at DESC LIMIT 100"
        )
        result = [EventRecord.from_row(row) for row in rows]
        for event in result:
            event.sources = [EventSource(**source)
                             for source in self.db.fetchall(
                                 "SELECT * FROM event_sources WHERE event_id = ? ORDER BY source_url", (event.id,))]
        return result

    def overview(self) -> dict:
        statuses = {row['status']: row['count'] for row in self.db.fetchall(
            "SELECT status, COUNT(*) AS count FROM events WHERE merged_into IS NULL GROUP BY status")}
        return {
            "events": sum(statuses.values()), "published": statuses.get("published", 0),
            "draft": statuses.get("draft", 0), "cancelled": statuses.get("cancelled", 0),
            "moderation": self.db.fetchone("SELECT COUNT(*) AS n FROM events WHERE merged_into IS NULL AND "
                                            "(is_disputed = 1 OR (raw_event_id IS NOT NULL AND status = 'draft'))")["n"],
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
