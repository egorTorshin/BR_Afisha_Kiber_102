"""Public event proposals, kept separate from the published catalog."""
import json
import uuid
from datetime import datetime, timezone

from src.db.connection import Database
from src.services.import_service import ImportService


class EventSubmissionService:
    def __init__(self, db: Database):
        self.db = db
        self.importer = ImportService(db)

    def submit(self, payload: dict) -> dict:
        self.importer._validate_and_normalize({**payload, "status": "draft"}, default_source_type="ручной ввод")
        submission_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO event_submissions (id, payload_json, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
            (submission_id, json.dumps(payload, ensure_ascii=False), now, now),
        )
        return {"id": submission_id, "status": "pending", "created_at": now}

    def pending(self) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM event_submissions WHERE status = 'pending' ORDER BY created_at ASC LIMIT 100"
        )
        return [{**json.loads(row["payload_json"]), **{key: value for key, value in row.items() if key != "payload_json"}}
                for row in rows]

    def review(self, submission_id: str, status: str, actor: str, note: str | None = None) -> dict:
        if status not in ("approved", "rejected"):
            raise ValueError("Недопустимое решение модерации")
        with self.db.transaction():
            self.db.lock_events()
            row = self.db.fetchone("SELECT * FROM event_submissions WHERE id = ?", (submission_id,))
            if row is None:
                raise LookupError("Заявка не найдена")
            if row["status"] != "pending":
                raise ValueError("Заявка уже рассмотрена")
            event_id = None
            if status == "approved":
                payload = json.loads(row["payload_json"])
                norm = self.importer._validate_and_normalize(
                    {**payload, "status": "published"}, default_source_type="ручной ввод"
                )
                duplicate = self.db.fetchone(
                    "SELECT id FROM events WHERE merged_into IS NULL AND (dedup_key = ? OR source_hash = ?) LIMIT 1",
                    (norm["dedup_key"], norm["source_hash"]),
                )
                if duplicate:
                    raise ValueError("Похожее событие или источник уже есть в афише. Проверьте заявку вручную")
                outcome, event = self.importer.create_event_manual(
                    {**payload, "status": "published"}, actor=actor
                )
                if outcome != "added":
                    raise ValueError("Событие совпало с существующей записью")
                event_id = event.id
            now = datetime.now(timezone.utc).isoformat()
            self.db.execute(
                "UPDATE event_submissions SET status = ?, updated_at = ?, reviewed_by = ?, review_note = ?, event_id = ? WHERE id = ?",
                (status, now, actor, note, event_id, submission_id),
            )
            self.importer.log_audit(actor, "review_submission", "event_submission", submission_id,
                                    {"status": {"old": "pending", "new": status}, "event_id": event_id, "note": note})
            return {"id": submission_id, "status": status, "event_id": event_id}
