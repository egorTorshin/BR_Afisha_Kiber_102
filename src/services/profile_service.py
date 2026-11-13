"""Minimal, consent-based storage for Telegram bot profiles."""
import json
from datetime import datetime, timezone
from typing import Any, Optional

from src.db.connection import Database


class ProfileService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get(self, user_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM bot_profiles WHERE user_id = ?", (str(user_id),))
        if row:
            try:
                row["favorite_categories"] = json.loads(row.pop("favorite_categories_json") or "[]")
            except (TypeError, ValueError):
                row["favorite_categories"] = []
        return row

    def ensure(self, user_id: str, language: str = "ru") -> dict[str, Any]:
        profile = self.get(user_id)
        if profile:
            return profile
        now = self._now()
        self.db.execute(
            """INSERT INTO bot_profiles (user_id, language, onboarding_state, created_at, updated_at)
               VALUES (?, ?, 'name', ?, ?)""",
            (str(user_id), language, now, now),
        )
        return self.get(user_id) or {}

    def update(self, user_id: str, **values: Any) -> dict[str, Any]:
        allowed = {"language", "name", "phone", "city", "latitude", "longitude", "onboarding_state"}
        fields, params = [], []
        for key, value in values.items():
            if key in allowed:
                fields.append(f"{key} = ?")
                params.append(value)
            elif key == "favorite_categories":
                fields.append("favorite_categories_json = ?")
                params.append(json.dumps(value or [], ensure_ascii=False))
        if not fields:
            return self.get(user_id) or {}
        fields.append("updated_at = ?")
        params.extend([self._now(), str(user_id)])
        self.db.execute(f"UPDATE bot_profiles SET {', '.join(fields)} WHERE user_id = ?", params)
        return self.get(user_id) or {}

    def delete(self, user_id: str) -> int:
        """Delete the consent-based bot profile and all its personal fields."""
        return self.db.execute("DELETE FROM bot_profiles WHERE user_id = ?", (str(user_id),))
