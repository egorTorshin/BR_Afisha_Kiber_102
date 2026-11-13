import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Optional

from src.db.connection import Database
from src.models.schemas import FeedbackRecord
from src.services.import_service import ImportService


class FeedbackService:
    """Organizer requests; status changes are for authenticated internal callers."""

    STATUSES = {"pending", "in_progress", "resolved", "rejected"}

    def __init__(self, db: Database):
        self.db = db
        self.audit = ImportService(db)

    def submit(self, message: str, event_id: Optional[str] = None, *, actor: str = "organizer") -> FeedbackRecord:
        message = message.strip()
        if not message or len(message) > 10000:
            raise ValueError("Сообщение должно содержать от 1 до 10000 символов")
        with self.db.transaction():
            self.db.lock_events()
            if event_id is not None:
                event = self.db.fetchone("SELECT id, merged_into FROM events WHERE id = ?", (event_id,))
                if not event:
                    raise LookupError("Событие не найдено")
                while event["merged_into"]:
                    event = self.db.fetchone("SELECT id, merged_into FROM events WHERE id = ?", (event["merged_into"],))
                event_id = event["id"]
            record = FeedbackRecord(str(uuid.uuid4()), event_id, message, "pending", datetime.now(timezone.utc).isoformat())
            self.db.execute("INSERT INTO feedback (id, event_id, message, status, created_at) VALUES (?, ?, ?, ?, ?)",
                            tuple(asdict(record).values()))
            self.audit.log_audit(actor, "create_feedback", "feedback", record.id, asdict(record))
            return record

    def list_requests(self, status: Optional[str] = None, *, limit: int = 50, offset: int = 0) -> List[FeedbackRecord]:
        if status is not None and status not in self.STATUSES:
            raise ValueError("Недопустимый статус заявки")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Некорректная пагинация")
        where = " WHERE status = ?" if status is not None else ""
        params = [status] if status is not None else []
        rows = self.db.fetchall("SELECT * FROM feedback" + where + " ORDER BY created_at, id LIMIT ? OFFSET ?",
                                (*params, limit, offset))
        return [FeedbackRecord(**row) for row in rows]

    def update_status(self, feedback_id: str, status: str, *, actor: str) -> FeedbackRecord:
        if status not in self.STATUSES or not actor.strip():
            raise ValueError("Укажите допустимый статус и инициатора")
        with self.db.transaction():
            suffix = " FOR UPDATE" if self.db.is_postgres else ""
            previous = self.db.fetchone("SELECT * FROM feedback WHERE id = ?" + suffix, (feedback_id,))
            if not previous:
                raise LookupError("Заявка не найдена")
            if previous["status"] != status:
                self.db.execute("UPDATE feedback SET status = ? WHERE id = ?", (status, feedback_id))
                self.audit.log_audit(actor, "change_feedback_status", "feedback", feedback_id,
                                     {"status": {"old": previous["status"], "new": status}})
            return FeedbackRecord(**{**previous, "status": status})
