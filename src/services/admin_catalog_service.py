"""Database queries for the administrative catalog and audit views."""

import json

from src.db.connection import Database
from src.models.schemas import EventRecord


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
            "SELECT * FROM events WHERE is_disputed = 1 AND merged_into IS NULL ORDER BY updated_at DESC LIMIT 100"
        )
        return [EventRecord.from_row(row) for row in rows]

    def recent_audit(self) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200")
        for row in rows:
            row["changes"] = json.loads(row.pop("changes_json"))
        return rows
