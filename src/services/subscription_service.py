import uuid
from datetime import date, datetime, timedelta, timezone
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

    @staticmethod
    def _user_id(value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned or len(cleaned) > 128 or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Некорректный идентификатор пользователя")
        return cleaned

    @staticmethod
    def _label(value: str, fallback: str, max_length: int) -> str:
        cleaned = str(value or fallback).strip()
        if not cleaned or len(cleaned) > max_length or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Некорректный параметр подписки")
        return cleaned

    def find_subscription(self, user_id: str, city: str, category: str) -> Optional[Dict[str, Any]]:
        user_id_str = self._user_id(user_id)
        norm_city = normalize_text(self._label(city, "Все города", 120))
        norm_cat = normalize_text(self._label(category, "Все категории", 100))
        rows = self.db.fetchall("SELECT * FROM subscriptions WHERE user_id = ?", (user_id_str,))
        for r in rows:
            if normalize_text(r["city"]) == norm_city and normalize_text(r["category"]) == norm_cat:
                return r
        return None

    def is_subscribed(self, user_id: str, city: Optional[str], category: Optional[str],
                      event_date: date | None = None) -> bool:
        subscription = self.find_subscription(
            user_id,
            city or "Все города",
            category or "Все категории",
        )
        if subscription is None:
            return False
        if event_date is None:
            return True
        day = event_date.isoformat()
        return ((subscription.get("period_from") is None or day >= subscription["period_from"])
                and (subscription.get("period_to") is None or day <= subscription["period_to"]))

    def subscribe(self, user_id: str, city: str, category: str, period=None,
                  replace_subscription_id: str | None = None) -> Dict[str, Any]:
        user_id_str = self._user_id(user_id)
        city_clean = self._label(city, "Все города", 120)
        cat_clean = self._label(category, "Все категории", 100).lower()
        start = period.get("date_from") if isinstance(period, dict) else getattr(period, "date_from", None)
        end = period.get("date_to") if isinstance(period, dict) else getattr(period, "date_to", None)
        if start is not None and not isinstance(start, date) or end is not None and not isinstance(end, date):
            raise ValueError("Некорректный период событий")
        if start and end and start > end:
            raise ValueError("Начало периода позже окончания")
        period_from = start.isoformat() if start else None
        period_to = end.isoformat() if end else None

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self.retention_days)

        if replace_subscription_id is not None:
            target = self.db.fetchone("SELECT * FROM subscriptions WHERE id = ? AND user_id = ?",
                                      (replace_subscription_id, user_id_str))
            if target is None:
                raise LookupError("Subscription not found")
            duplicate = self.find_subscription(user_id_str, city_clean, cat_clean)
            if duplicate and duplicate["id"] != target["id"]:
                raise ValueError("Subscription with these filters already exists")
            self.db.execute("""UPDATE subscriptions SET city = ?, category = ?, period_from = ?,
                           period_to = ?, expires_at = ? WHERE id = ? AND user_id = ?""",
                           (city_clean, cat_clean, period_from, period_to, expires.isoformat(),
                            target["id"], user_id_str))
            return {"id": target["id"], "user_id": user_id_str, "city": city_clean,
                    "category": cat_clean, "created_at": target["created_at"],
                    "expires_at": expires.isoformat(), "is_new": False,
                    "period_from": period_from, "period_to": period_to}

        existing = self.find_subscription(user_id_str, city_clean, cat_clean)
        if existing:
            self.db.execute(
                "UPDATE subscriptions SET expires_at = ?, period_from = ?, period_to = ? WHERE id = ?",
                (expires.isoformat(), period_from, period_to, existing["id"]),
            )
            return {
                "id": existing["id"],
                "user_id": user_id_str,
                "city": existing["city"],
                "category": existing["category"],
                "created_at": existing["created_at"],
                "expires_at": expires.isoformat(),
                "is_new": False,
                "period_from": period_from,
                "period_to": period_to,
            }

        sub_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO subscriptions (id, user_id, city, category, created_at, expires_at, period_from, period_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sub_id, user_id_str, city_clean, cat_clean, now.isoformat(), expires.isoformat(), period_from, period_to),
        )
        return {
            "id": sub_id,
            "user_id": user_id_str,
            "city": city_clean,
            "category": cat_clean,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "is_new": True,
            "period_from": period_from,
            "period_to": period_to,
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
            (self._user_id(user_id), now_iso),
        )

    def unsubscribe_all(self, user_id: str) -> int:
        """Отписка одной командой — полностью удаляет подписки пользователя из БД (Раздел 6.2, 10 ТЗ)."""
        return self.db.execute(
            "DELETE FROM subscriptions WHERE user_id = ?",
            (self._user_id(user_id),),
        )

    def unsubscribe_by_id(self, user_id: str, sub_id: str) -> int:
        return self.db.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND (id = ? OR id LIKE ?)",
            (self._user_id(user_id), sub_id.strip(), f"{sub_id.strip()}%"),
        )

    def cleanup_expired(self) -> int:
        now_iso = datetime.now(timezone.utc).isoformat()
        return self.db.execute("DELETE FROM subscriptions WHERE expires_at < ?", (now_iso,))
