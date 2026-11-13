import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from src.db.connection import Database
from src.services.normalizer import normalize_text


class SubscriptionService:
    """
    Управление подписками пользователей на город и категорию (Раздел 6.2, 8.2, 10 ТЗ).
    Персональные данные минимизированы: хранится только технический user_id и фильтры подписки.
    Отписка полностью удаляет запись из базы.
    """

    def __init__(self, db: Database, retention_days: int = 180) -> None:
        self.db = db
        self.retention_days = retention_days

    def find_subscription(self, user_id: str, city: str, category: str) -> Optional[Dict[str, Any]]:
        user_id_str = str(user_id).strip()
        norm_city = normalize_text(city or "Все города")
        norm_cat = normalize_text(category or "Все категории")
        rows = self.db.fetchall("SELECT * FROM subscriptions WHERE user_id = ?", (user_id_str,))
        for r in rows:
            if normalize_text(r["city"]) == norm_city and normalize_text(r["category"]) == norm_cat:
                return r
        return None

    def is_subscribed(self, user_id: str, city: Optional[str], category: Optional[str]) -> bool:
        return self.find_subscription(
            user_id,
            city or "Все города",
            category or "Все категории",
        ) is not None

    def subscribe(self, user_id: str, city: str, category: str) -> Dict[str, Any]:
        user_id_str = str(user_id).strip()
        city_clean = (city or "Все города").strip()
        cat_clean = (category or "Все категории").strip().lower()

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self.retention_days)

        existing = self.find_subscription(user_id_str, city_clean, cat_clean)
        if existing:
            self.db.execute(
                "UPDATE subscriptions SET expires_at = ? WHERE id = ?",
                (expires.isoformat(), existing["id"]),
            )
            return {
                "id": existing["id"],
                "user_id": user_id_str,
                "city": existing["city"],
                "category": existing["category"],
                "created_at": existing["created_at"],
                "expires_at": expires.isoformat(),
                "is_new": False,
            }

        sub_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO subscriptions (id, user_id, city, category, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sub_id, user_id_str, city_clean, cat_clean, now.isoformat(), expires.isoformat()),
        )
        return {
            "id": sub_id,
            "user_id": user_id_str,
            "city": city_clean,
            "category": cat_clean,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "is_new": True,
        }

    def toggle_subscription(self, user_id: str, city: str, category: str) -> Dict[str, Any]:
        existing = self.find_subscription(user_id, city, category)
        if existing:
            self.unsubscribe_by_id(user_id, str(existing["id"]))
            return {
                "subscribed": False,
                "city": existing["city"],
                "category": existing["category"],
            }
        sub = self.subscribe(user_id, city, category)
        return {
            "subscribed": True,
            "city": sub["city"],
            "category": sub["category"],
            "is_new": sub["is_new"],
        }

    def list_subscriptions(self, user_id: str) -> List[Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        return self.db.fetchall(
            "SELECT * FROM subscriptions WHERE user_id = ? AND expires_at >= ? ORDER BY created_at DESC",
            (str(user_id).strip(), now_iso),
        )

    def unsubscribe_all(self, user_id: str) -> int:
        """Отписка одной командой — полностью удаляет подписки пользователя из БД (Раздел 6.2, 10 ТЗ)."""
        return self.db.execute(
            "DELETE FROM subscriptions WHERE user_id = ?",
            (str(user_id).strip(),),
        )

    def unsubscribe_by_id(self, user_id: str, sub_id: str) -> int:
        return self.db.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND (id = ? OR id LIKE ?)",
            (str(user_id).strip(), sub_id.strip(), f"{sub_id.strip()}%"),
        )

    def cleanup_expired(self) -> int:
        now_iso = datetime.now(timezone.utc).isoformat()
        return self.db.execute("DELETE FROM subscriptions WHERE expires_at < ?", (now_iso,))
