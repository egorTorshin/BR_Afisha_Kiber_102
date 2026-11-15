import csv
import hashlib
import io
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
from src.db.connection import Database
from src.models.schemas import (
    EventRecord,
    ImportReport,
    VALID_AGE_LIMITS,
    VALID_CATEGORIES,
    VALID_SOURCE_TYPES,
    normalize_status,
    EventSource,
)
from src.services.normalizer import (
    compute_dedup_key,
    compute_city_conflict_key,
    compute_source_hash,
    normalize_text,
    parse_iso_datetime,
    parse_price_fields,
    resolve_category,
    resolve_city,
)
from src.services.event_series import matching_series, scheduled_record, series_key


class ImportService:
    """
    Сервис импорта CSV/JSON, ручного ввода, хранения сырых данных,
    валидации, пометки спорных карточек и ведения журнала изменений (Раздел 6.1, 8 ТЗ).
    """

    REQUIRED_FIELDS = ("title", "category", "date_start", "city", "venue")
    MAX_IMPORT_BYTES = 2 * 1024 * 1024
    MAX_IMPORT_ROWS = 2000
    FIELD_LIMITS = {"title": 250, "category": 100, "city": 120, "venue": 250,
                    "address": 500, "description": 10000, "language": 100,
                    "source_url": 2048, "org_link": 2048, "dispute_reason": 1000}

    def __init__(self, db: Database, *, max_import_bytes: int = MAX_IMPORT_BYTES,
                 max_import_rows: int = MAX_IMPORT_ROWS) -> None:
        self.db = db
        self.max_import_bytes = max_import_bytes
        self.max_import_rows = max_import_rows

    @staticmethod
    def _valid_url(value: str, field: str) -> str:
        if len(value) > 2048 or any(c.isspace() or ord(c) < 32 or c in {'<', '>', '"', "'", '\\'} for c in value):
            raise ValueError(f"Некорректный URL в поле {field}")
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError()
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError()
        except ValueError:
            raise ValueError(f"Некорректный URL в поле {field}") from None
        return value

    @staticmethod
    def _search_fields(norm: Dict[str, Any]) -> Dict[str, str]:
        tags = " ".join(norm["tags"])
        return {
            "search_city": normalize_text(norm["city"]),
            "search_venue": normalize_text(norm["venue"]),
            "search_language": normalize_text(norm["language"] or ""),
            "search_tags": normalize_text(tags),
            "search_text": normalize_text(" ".join((norm["title"], norm["description"], norm["venue"],
                                                      norm["category"], tags, norm["language"] or ""))),
        }

    def list_sources(self, event_id: str) -> List[EventSource]:
        return [EventSource(**row) for row in self.db.fetchall(
            "SELECT * FROM event_sources WHERE event_id = ? ORDER BY source_url", (event_id,)
        )]

    def save_source(self, event_id: str, source: Dict[str, Any], actor: str) -> bool:
        previous = self.db.fetchone(
            "SELECT * FROM event_sources WHERE event_id = ? AND source_url = ?",
            (event_id, source["source_url"]),
        )
        values = {key: source[key] for key in ("source_url", "source_type", "source_hash", "verified_at")}
        changed = previous is None or any(
            previous[k] != values[k] for k in values
            if k != "verified_at" or source.get("_verified_explicit")
        )
        if not changed:
            return False
        self.db.execute(
            """INSERT INTO event_sources (event_id, source_url, source_type, source_hash, verified_at)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_id, source_url) DO UPDATE SET
               source_type = excluded.source_type, source_hash = excluded.source_hash,
               verified_at = excluded.verified_at""", (event_id, *values.values()),
        )
        self.log_audit(actor, "update_source" if previous else "add_source", "event", event_id,
                       {"source": {"old": previous, "new": values}})
        return True

    def merge_events(self, target_id: str, duplicate_id: str, actor: str) -> None:
        """Retain old IDs and event snapshots; move sources/references to the survivor."""
        if target_id == duplicate_id:
            return
        with self.db.transaction():
            self.db.lock_events()
            target = self.db.fetchone("SELECT * FROM events WHERE id = ?", (target_id,))
            duplicate = self.db.fetchone("SELECT * FROM events WHERE id = ?", (duplicate_id,))
            if not target or not duplicate or target["merged_into"] or duplicate["merged_into"]:
                raise ValueError("Объединять можно только существующие основные события")
            sources = self.db.fetchall("SELECT * FROM event_sources WHERE event_id = ?", (duplicate_id,))
            # Copy conflicting URL metadata into the audit snapshot before moving it.
            self.db.execute("DELETE FROM event_sources WHERE event_id = ?", (duplicate_id,))
            for source in sources:
                self.save_source(target_id, source, actor)
            self.db.execute("UPDATE feedback SET event_id = ? WHERE event_id = ?", (target_id, duplicate_id))
            now = datetime.now(timezone.utc).isoformat()
            self.db.execute("UPDATE events SET merged_into = ?, updated_at = ? WHERE id = ? OR merged_into = ?",
                            (target_id, now, duplicate_id, duplicate_id))
            priority = {"published": 0, "draft": 1, "cancelled": 2}
            status = max((target["status"], duplicate["status"]), key=priority.__getitem__)
            self.db.execute("UPDATE events SET status = ?, status_locked = ?, updated_at = ? WHERE id = ?",
                            (status, max(target["status_locked"], duplicate["status_locked"]), now, target_id))
            self.log_audit(actor, "merge_event", "event", target_id,
                           {"duplicate": duplicate, "sources": sources,
                            "status": {"old": target["status"], "new": status}})

    def log_audit(
        self,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        changes: Dict[str, Any],
    ) -> None:
        if not actor or not actor.strip():
            raise ValueError("Не указан инициатор изменения")
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """
            INSERT INTO audit_log (id, actor, action, entity_type, entity_id, changes_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                actor,
                action,
                entity_type,
                entity_id,
                json.dumps(changes, ensure_ascii=False),
                now,
            ),
        )

    def _validate_and_normalize(self, raw_item: Dict[str, Any], default_source_type: str = "импорт",
                                default_status: str = "published") -> Dict[str, Any]:
        if not isinstance(raw_item, dict):
            raise ValueError("Запись события должна быть объектом")
        for field_name, max_length in self.FIELD_LIMITS.items():
            value = raw_item.get(field_name)
            if value is not None and len(str(value)) > max_length:
                raise ValueError(f"Поле {field_name} превышает {max_length} символов")
        missing = []
        for req in self.REQUIRED_FIELDS:
            val = raw_item.get(req)
            if val is None or str(val).strip() == "":
                missing.append(req)
        if missing:
            raise ValueError(f"Отсутствуют обязательные поля: {', '.join(missing)}")

        title = str(raw_item["title"]).strip()
        normalized_title = normalize_text(title)
        if len(normalized_title) < 2:
            raise ValueError("Слишком короткое или некорректное название события")

        raw_cat = str(raw_item["category"]).strip()
        category = resolve_category(raw_cat, db=self.db)
        if not category or category not in VALID_CATEGORIES:
            raise ValueError(f"Неизвестная категория '{raw_cat}'. Допустимые: {', '.join(sorted(VALID_CATEGORIES))}")

        date_start = parse_iso_datetime(raw_item["date_start"])
        date_end_raw = raw_item.get("date_end")
        date_end = parse_iso_datetime(date_end_raw) if date_end_raw and str(date_end_raw).strip() else None
        if date_end and date_end < date_start:
            raise ValueError("Дата окончания не может быть раньше начала")

        duration_raw = raw_item.get("duration_min")
        duration_min: Optional[int] = None
        if duration_raw not in (None, ""):
            try:
                duration_min = int(float(duration_raw))
                if not 1 <= duration_min <= 1440:
                    raise ValueError("Длительность должна быть от 1 до 1440 минут")
            except ValueError as e:
                raise ValueError(f"Некорректная длительность duration_min: {duration_raw}") from e

        raw_city = str(raw_item["city"]).strip()
        resolved_city = resolve_city(raw_city, db=self.db) or raw_city
        venue = str(raw_item["venue"]).strip()
        address = str(raw_item["address"]).strip()
        description = str(raw_item["description"]).strip()

        raw_source_url = str(raw_item.get("source_url") or "").strip()
        source_url = self._valid_url(raw_source_url, "source_url") if raw_source_url else ""

        org_link_raw = raw_item.get("org_link")
        org_link = str(org_link_raw).strip() if org_link_raw and str(org_link_raw).strip() else None
        if org_link:
            org_link = self._valid_url(org_link, "org_link")

        price_min, price_max, is_free = parse_price_fields(
            raw_item.get("price_min"),
            raw_item.get("price_max"),
            raw_item.get("is_free"),
        )

        age_raw = raw_item.get("age_limit", 0)
        try:
            age_limit = int(str(age_raw).replace("+", "").strip() or 0)
        except ValueError:
            raise ValueError(f"Некорректное возрастное ограничение age_limit: '{age_raw}'")
        if age_limit not in VALID_AGE_LIMITS:
            # Приводим к ближайшему допустимому порогу 0, 6, 12, 16, 18
            if age_limit < 0 or age_limit > 21:
                raise ValueError(f"Возрастное ограничение вне допустимого диапазона: {age_limit}")
            age_limit = max([a for a in sorted(VALID_AGE_LIMITS) if a <= age_limit] or [0])

        raw_tags = raw_item.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.strip()
            if raw_tags.startswith("["):
                try:
                    tags = [str(x).strip() for x in json.loads(raw_tags) if str(x).strip()]
                except Exception:
                    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            else:
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(x).strip() for x in raw_tags if str(x).strip()]
        else:
            tags = []
        if len(tags) > 20 or any(len(tag) > 100 for tag in tags):
            raise ValueError("Допускается не более 20 тегов длиной до 100 символов")

        lat_raw = raw_item.get("lat")
        lon_raw = raw_item.get("lon")
        lat = float(lat_raw) if lat_raw not in (None, "") else None
        lon = float(lon_raw) if lon_raw not in (None, "") else None
        if lat is not None and (not math.isfinite(lat) or not -90 <= lat <= 90):
            raise ValueError("Некорректная широта")
        if lon is not None and (not math.isfinite(lon) or not -180 <= lon <= 180):
            raise ValueError("Некорректная долгота")

        language_raw = raw_item.get("language")
        language = str(language_raw).strip().lower() if language_raw and str(language_raw).strip() else None

        source_type = str(raw_item.get("source_type") or default_source_type).strip()
        if source_type not in VALID_SOURCE_TYPES:
            source_type = default_source_type

        confidence_raw = raw_item.get("confidence", 1.0)
        try:
            confidence = float(confidence_raw if confidence_raw not in (None, "") else 1.0)
        except ValueError:
            confidence = 0.7
        if not math.isfinite(confidence):
            raise ValueError("Некорректная уверенность извлечения")
        confidence = max(0.0, min(1.0, confidence))

        status = normalize_status(raw_item.get("status") or default_status)

        # Определение спорной карточки (Раздел 6.1: «обязательные поля заполнены, спорные помечены»)
        dispute_reasons: List[str] = []
        if raw_item.get("is_disputed") in (True, 1, "true", "True", "1"):
            if raw_item.get("dispute_reason"):
                dispute_reasons.append(str(raw_item["dispute_reason"]))
            else:
                dispute_reasons.append("Помечено как спорное источником")
        if confidence < 0.8:
            dispute_reasons.append(f"Низкая уверенность извлечения ({confidence:.2f})")
        if price_min is not None and price_min > 0 and not org_link:
            dispute_reasons.append("Платное событие без ссылки на билеты/организатора (org_link)")
        if len(description) < 20:
            dispute_reasons.append("Слишком краткое описание мероприятия (< 20 символов)")
        if not address:
            dispute_reasons.append("Не указан адрес")
        if not source_url:
            dispute_reasons.append("Не указан источник")
        try:
            event_day = datetime.fromisoformat(date_start).date()
        except ValueError:
            event_day = datetime.min.date()
        if event_day <= datetime.now(timezone.utc).date():
            dispute_reasons.append("Дата мероприятия не в будущем")

        # Imported/source records require an explicit successful source verification
        # before publication. Manual administrator entry remains trusted.
        incomplete = not address or len(description) < 20 or not source_url
        if source_type != "ручной ввод" and status == "published" and (
            incomplete or source_type == "страница"
        ):
            status = "draft"
            if source_type == "страница" and not raw_item.get("verified_at"):
                dispute_reasons.append("Внешнее событие требует проверки и модерации")

        is_disputed = len(dispute_reasons) > 0
        dispute_reason = "; ".join(dict.fromkeys(dispute_reasons)) if is_disputed else None

        source_hash = compute_source_hash({
            "title": title,
            "date_start": date_start,
            "city": resolved_city,
            "venue": venue,
            "source_url": source_url,
        })
        dedup_key = compute_dedup_key(title, date_start, resolved_city, venue)
        event_id = str(raw_item.get("id") or uuid.uuid4())
        if len(event_id) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", event_id):
            raise ValueError("Некорректный идентификатор события")
        now_iso = datetime.now(timezone.utc).isoformat()
        verified_at = str(raw_item.get("verified_at") or now_iso)
        try:
            datetime.fromisoformat(verified_at)
        except ValueError:
            raise ValueError("Некорректная дата проверки источника") from None

        return {
            "id": event_id,
            "title": title,
            "normalized_title": normalized_title,
            "category": category,
            "tags": tags,
            "date_start": date_start,
            "date_end": date_end,
            "duration_min": duration_min,
            "city": resolved_city,
            "venue": venue,
            "address": address,
            "lat": lat,
            "lon": lon,
            "description": description,
            "price_min": price_min,
            "price_max": price_max,
            "is_free": is_free,
            "age_limit": age_limit,
            "language": language,
            "org_link": org_link,
            "source_url": source_url,
            "source_type": source_type,
            "source_hash": source_hash,
            "verified_at": verified_at,
            "_verified_explicit": bool(raw_item.get("verified_at")),
            "confidence": confidence,
            "status": status,
            "_status_explicit": bool(raw_item.get("status")),
            "dedup_key": dedup_key,
            "is_disputed": is_disputed,
            "dispute_reason": dispute_reason,
        }

    def _save_raw_event(self, raw_item: Dict[str, Any], source_type: str, source_name: str, source_hash: str) -> str:
        existing = self.db.fetchone(
            "SELECT id FROM raw_events WHERE source_hash = ?",
            (source_hash,),
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        if existing:
            raw_id = str(existing["id"])
            self.db.execute(
                "UPDATE raw_events SET raw_payload_json = ?, last_reprocessed_at = ? WHERE id = ?",
                (json.dumps(raw_item, ensure_ascii=False), now_iso, raw_id),
            )
            return raw_id
        raw_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO raw_events (id, source_type, source_name, source_hash, raw_payload_json, ingested_at, last_reprocessed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_id,
                source_type,
                source_name,
                source_hash,
                json.dumps(raw_item, ensure_ascii=False),
                now_iso,
                now_iso,
            ),
        )
        return raw_id

    def upsert_normalized_event(
        self,
        norm: Dict[str, Any],
        raw_event_id: Optional[str],
        actor: str,
    ) -> str:
        """
        Идемпотентное сохранение нормализованного события.
        Возвращает 'added' | 'updated' | 'unchanged'.
        """
        with self.db.transaction():
            self.db.lock_events()
            return self._upsert_normalized_event(norm, raw_event_id, actor)

    def _upsert_normalized_event(self, norm, raw_event_id, actor):
        matches = self.db.fetchall(
            """SELECT * FROM events WHERE id = ? OR
               (merged_into IS NULL AND (dedup_key = ? OR id IN
                (SELECT es.event_id FROM event_sources es JOIN events se ON se.id = es.event_id
                 WHERE ? <> 'ручной ввод' AND es.source_url = ?
                   AND se.normalized_title = ? AND se.search_venue = ?) OR
                (normalized_title = ? AND date_start = ? AND search_venue = ?)))
               ORDER BY created_at, id""",
            (norm["id"], norm["dedup_key"], norm["source_type"], norm["source_url"], norm["normalized_title"],
             normalize_text(norm["venue"]),
             norm["normalized_title"], norm["date_start"], normalize_text(norm["venue"])),
        )
        targets = {}
        for match in matches:
            while match.get("merged_into"):
                match = self.db.fetchone("SELECT * FROM events WHERE id = ?", (match["merged_into"],))
            targets[match["id"]] = match
        existing = next(iter(targets.values()), None)
        city_conflict = bool(existing and normalize_text(existing["city"]) != normalize_text(norm["city"]))
        if city_conflict:
            # The unique index is retained. Keep distinct-city records separate and
            # send both identities to moderation rather than merging them.
            norm["dedup_key"] = compute_city_conflict_key(
                norm["title"], norm["date_start"], norm["city"], norm["venue"])
            norm["is_disputed"] = True
            norm["dispute_reason"] = "; ".join(filter(None, [norm.get("dispute_reason"),
                f"Совпадает название/дата/площадка с событием в городе {existing['city']}"]))
            self.db.execute("UPDATE events SET is_disputed = 1, dispute_reason = ?, updated_at = ? WHERE id = ?",
                (f"Совпадает название/дата/площадка с событием в городе {norm['city']}",
                 datetime.now(timezone.utc).isoformat(), existing["id"]))
            existing = None
        if existing:
            for other_id in list(targets)[1:]:
                self.merge_events(existing["id"], other_id, actor)
            existing = self.db.fetchone("SELECT * FROM events WHERE id = ?", (existing["id"],))
            norm["id"] = existing["id"]
            if existing["status_locked"] or not norm.get("_status_explicit", True):
                norm["status"] = existing["status"]
            if existing["status_locked"]:
                norm["is_disputed"] = bool(existing["is_disputed"])
                norm["dispute_reason"] = existing["dispute_reason"]
            source_changed = self.save_source(existing["id"], norm, actor)
            # Manual administrator entry is a trusted catalog source. Keep that
            # provenance when later collector imports add corroborating sources.
            if existing["source_type"] == "ручной ввод":
                norm["source_type"] = "ручной ввод"
            # The legacy fields consistently refer to the primary source.
            if norm["source_url"] != existing["source_url"]:
                for field in ("source_url", "source_type", "source_hash", "verified_at"):
                    norm[field] = existing[field]
        else:
            source_changed = False
        now_iso = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(norm["tags"], ensure_ascii=False)
        search_fields = self._search_fields(norm)

        if not existing:
            self.db.execute(
                """
                INSERT INTO events (
                    id, raw_event_id, title, normalized_title, category, tags,
                    date_start, date_end, duration_min, city, venue, address,
                    lat, lon, description, price_min, price_max, is_free,
                    age_limit, language, org_link, source_url, source_type,
                    source_hash, verified_at, confidence, status, dedup_key,
                    is_disputed, dispute_reason, created_at, updated_at,
                    search_city, search_venue, search_language, search_tags, search_text
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    norm["id"],
                    raw_event_id,
                    norm["title"],
                    norm["normalized_title"],
                    norm["category"],
                    tags_json,
                    norm["date_start"],
                    norm["date_end"],
                    norm["duration_min"],
                    norm["city"],
                    norm["venue"],
                    norm["address"],
                    norm["lat"],
                    norm["lon"],
                    norm["description"],
                    norm["price_min"],
                    norm["price_max"],
                    1 if norm["is_free"] else 0,
                    norm["age_limit"],
                    norm["language"],
                    norm["org_link"],
                    norm["source_url"],
                    norm["source_type"],
                    norm["source_hash"],
                    norm["verified_at"],
                    norm["confidence"],
                    norm["status"],
                    norm["dedup_key"],
                    1 if norm["is_disputed"] else 0,
                    norm["dispute_reason"],
                    now_iso,
                    now_iso,
                    *search_fields.values(),
                ),
            )
            self.save_source(norm["id"], norm, actor)
            self.log_audit(
                actor=actor,
                action="create_event",
                entity_type="event",
                entity_id=norm["id"],
                changes={k: v for k, v in norm.items() if not k.startswith("_")},
            )
            return "added"

        # Проверяем, изменились ли содержательные поля (идемпотентность при повторном импорте того же файла)
        values = {k: v for k, v in norm.items() if not k.startswith("_") and k != "id"}
        if (existing["status"] == "published" and existing["source_type"] != "ручной ввод"
                and any(existing.get(key) != value for key, value in values.items()
                        if key not in {"tags", "verified_at", "source_hash", "source_url", "source_type"})):
            values["status"] = "draft"
            values["is_disputed"] = 1
            values["dispute_reason"] = "; ".join(filter(None, [norm.get("dispute_reason"),
                "Изменение внешнего события требует повторной модерации"]))
        values["tags"] = tags_json
        values.update(search_fields)
        for flag in ("is_free", "is_disputed"):
            values[flag] = int(values[flag])
        # Automatically generated verification timestamps do not make a repeat an update.
        compare_fields = [(key, existing.get(key), value) for key, value in values.items()
                          if key != "verified_at" or norm.get("_verified_explicit")]
        diff = {field: {"old": old_v, "new": new_v} for field, old_v, new_v in compare_fields if old_v != new_v}
        if not diff:
            if source_changed:
                self.db.execute("UPDATE events SET updated_at = ? WHERE id = ?", (now_iso, existing["id"]))
            return "updated" if source_changed else "unchanged"

        target_id = str(existing["id"])
        # Keep the primary source's raw pointer consistent with legacy source fields.
        values["raw_event_id"] = existing.get("raw_event_id") or raw_event_id
        values["updated_at"] = now_iso
        self.db.execute(
            "UPDATE events SET " + ", ".join(f"{key} = ?" for key in values) + " WHERE id = ?",
            (*values.values(), target_id),
        )
        self.log_audit(
            actor=actor,
            action="update_event",
            entity_type="event",
            entity_id=target_id,
            changes=diff,
        )
        return "updated"

    def import_records(
        self,
        records: List[Dict[str, Any]],
        source_file: str,
        actor: str = "system",
        source_type: str = "импорт",
        default_status: str = "published",
    ) -> ImportReport:
        if len(records) > self.max_import_rows:
            raise ValueError(f"Импорт ограничен {self.max_import_rows} записями")
        added = 0
        updated = 0
        unchanged = 0
        rejected = 0
        disputed = 0
        rejected_reasons: List[Dict[str, Any]] = []
        disputed_items: List[Dict[str, Any]] = []

        for idx, raw_item in enumerate(records, start=1):
            try:
                norm = self._validate_and_normalize(raw_item, default_source_type=source_type,
                                                    default_status=default_status)
                with self.db.transaction():
                    self.db.lock_events()
                    raw_id = self._save_raw_event(raw_item, source_type, source_file, norm["source_hash"])
                    outcome = self.upsert_normalized_event(norm, raw_id, actor=actor)
                if outcome == "added":
                    added += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    unchanged += 1

                if norm["is_disputed"]:
                    disputed += 1
                    disputed_items.append({
                        "row": idx,
                        "id": norm["id"],
                        "title": norm["title"],
                        "reason": norm["dispute_reason"],
                    })
            except Exception as exc:
                rejected += 1
                # Rejected source rows remain available for repair and audit.
                if isinstance(raw_item, dict) and isinstance(exc, ValueError):
                    # Rejected records can share event identity while differing in
                    # invalid fields. Hash the whole payload so each raw variant survives.
                    raw_hash = hashlib.sha256(
                        json.dumps(raw_item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest()
                    self._save_raw_event(raw_item, source_type, source_file, raw_hash)
                rejected_reasons.append({
                    "row": idx,
                    "title": str(raw_item.get("title", "<без названия>")) if isinstance(raw_item, dict) else "<без названия>",
                    "reason": str(exc),
                })

        report_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        details = {
            "rejected_reasons": rejected_reasons,
            "disputed_items": disputed_items,
        }
        self.db.execute(
            """
            INSERT INTO import_reports (
                id, source_file, actor, total_rows, added_count, updated_count,
                unchanged_count, rejected_count, disputed_count, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                source_file,
                actor,
                len(records),
                added,
                updated,
                unchanged,
                rejected,
                disputed,
                json.dumps(details, ensure_ascii=False),
                now_iso,
            ),
        )
        self.log_audit(
            actor=actor,
            action="import_batch",
            entity_type="import_report",
            entity_id=report_id,
            changes={
                "source_file": source_file,
                "total": len(records),
                "added": added,
                "updated": updated,
                "unchanged": unchanged,
                "rejected": rejected,
                "disputed": disputed,
            },
        )
        return ImportReport(
            report_id=report_id,
            source_file=source_file,
            actor=actor,
            total_rows=len(records),
            added_count=added,
            updated_count=updated,
            unchanged_count=unchanged,
            rejected_count=rejected,
            disputed_count=disputed,
            rejected_reasons=rejected_reasons,
            disputed_items=disputed_items,
        )

    def import_from_file(self, file_path: Path, actor: str = "moderator") -> ImportReport:
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        if file_path.stat().st_size > self.max_import_bytes:
            raise ValueError("Файл импорта слишком большой")
        suffix = file_path.suffix.lower()
        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("Некорректная кодировка файла импорта. Используйте UTF-8.") from None
        return self.import_from_content(content, filename=file_path.name, suffix=suffix, actor=actor)

    def import_from_content(
        self,
        content: str,
        filename: str,
        suffix: str,
        actor: str = "moderator",
        max_rows: Optional[int] = None,
    ) -> ImportReport:
        allowed_rows = min(max_rows, self.max_import_rows) if max_rows is not None else self.max_import_rows
        if allowed_rows < 1:
            raise ValueError("Некорректный лимит числа записей")
        if len(content.encode("utf-8")) > self.max_import_bytes:
            raise ValueError("Файл импорта слишком большой")
        if suffix == ".json":
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "events" in parsed:
                records = parsed["events"]
            elif isinstance(parsed, list):
                records = parsed
            else:
                raise ValueError("JSON-файл должен содержать массив объектов событий или ключ 'events'")
        elif suffix == ".csv":
            reader = csv.DictReader(io.StringIO(content))
            records = []
            for row in reader:
                if len(records) >= allowed_rows:
                    raise ValueError("Превышено число записей импорта")
                records.append(dict(row))
        else:
            raise ValueError(f"Неподдерживаемый формат файла '{suffix}'. Поддерживаются .csv и .json")
        if not isinstance(records, list) or len(records) > allowed_rows:
            raise ValueError("Превышено число записей импорта")
        return self.import_records(records, source_file=filename, actor=actor, source_type="импорт")

    def create_event_manual(self, data: Dict[str, Any], actor: str = "moderator") -> Tuple[str, EventRecord]:
        with self.db.transaction():
            self.db.lock_events()
            return self._create_event_manual(data, actor)

    def _create_event_manual(self, data, actor):
        data = dict(data)
        data["source_type"] = "ручной ввод"
        norm = self._validate_and_normalize(data, default_source_type="ручной ввод")
        raw_id = self._save_raw_event(data, "ручной ввод", "manual_admin_input", norm["source_hash"])
        outcome = self.upsert_normalized_event(norm, raw_id, actor=actor)
        row = self.db.fetchone(
            "SELECT * FROM events WHERE id = ?",
            (norm["id"],),
        )
        record = EventRecord.from_row(row)
        record.sources = self.list_sources(record.id)
        return outcome, record

    def update_event_status(self, event_id: str, new_status: str, actor: str = "moderator") -> EventRecord:
        with self.db.transaction():
            self.db.lock_events()
            row = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
            if not row:
                raise LookupError(f"Событие с ID '{event_id}' не найдено")
            key = series_key(row)
            if key[0] == "single":
                return self._update_event_status(event_id, new_status, actor)
            candidates = self.db.fetchall(
                """SELECT * FROM events WHERE merged_into IS NULL AND normalized_title = ?
                   AND search_city = ? AND search_venue = ?""",
                (row["normalized_title"], row["search_city"], row["search_venue"]),
            )
            ids = [candidate["id"] for candidate in matching_series(candidates, row)]
            for candidate_id in ids:
                self._update_event_status(candidate_id, new_status, actor)
            updated = self.db.fetchall(
                "SELECT * FROM events WHERE id IN (" + ", ".join("?" for _ in ids) + ") ORDER BY date_start, id", tuple(ids))
            return scheduled_record(updated)

    def archive_event(self, event_id: str, actor: str) -> EventRecord:
        """Soft-delete by hiding the event as a locked draft; retain sources and full audit history."""
        with self.db.transaction():
            self.db.lock_events()
            previous = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
            if not previous:
                raise LookupError("Событие не найдено")
            if previous.get("merged_into"):
                raise ValueError("Событие уже объединено с другой карточкой")
            key = series_key(previous)
            if key[0] == "single":
                members = [previous]
            else:
                candidates = self.db.fetchall(
                    """SELECT * FROM events WHERE merged_into IS NULL AND normalized_title = ?
                       AND search_city = ? AND search_venue = ?""",
                    (previous["normalized_title"], previous["search_city"], previous["search_venue"]),
                )
                members = matching_series(candidates, previous)
            now_iso = datetime.now(timezone.utc).isoformat()
            for member in members:
                self.db.execute("UPDATE events SET status = 'draft', status_locked = 1, updated_at = ? WHERE id = ?",
                                (now_iso, member["id"]))
                self.log_audit(actor, "archive_event", "event", member["id"],
                               {"archived": {"old": False, "new": True},
                                "status": {"old": member["status"], "new": "draft"}})
            rows = self.db.fetchall(
                "SELECT * FROM events WHERE id IN (" + ", ".join("?" for _ in members) + ") ORDER BY date_start, id",
                tuple(member["id"] for member in members))
            record = scheduled_record(rows)
            record.sources = self.list_sources(event_id)
            return record

    def update_event_fields(self, event_id: str, changes: Dict[str, Any], actor: str,
                            _propagate: bool = True) -> EventRecord:
        """Validate editable fields and persist them with the standard audit trail."""
        editable = set(self.REQUIRED_FIELDS) | {"address", "description", "source_url", "date_end", "duration_min", "price_min", "price_max", "is_free",
                    "age_limit", "language", "org_link", "tags", "lat", "lon"}
        unsupported = set(changes) - editable
        if unsupported:
            raise ValueError(f"Недопустимые для редактирования поля: {', '.join(sorted(unsupported))}")
        with self.db.transaction():
            self.db.lock_events()
            previous = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
            if not previous:
                raise LookupError("Событие не найдено")
            peers: list[str] = []
            if _propagate and series_key(previous)[0] == "series":
                candidates = self.db.fetchall(
                    """SELECT * FROM events WHERE merged_into IS NULL AND normalized_title = ?
                       AND search_city = ? AND search_venue = ?""",
                    (previous["normalized_title"], previous["search_city"], previous["search_venue"]),
                )
                peers = [item["id"] for item in matching_series(candidates, previous)
                         if item["id"] != event_id]
            payload = dict(previous)
            payload["tags"] = json.loads(payload.get("tags") or "[]")
            payload.update({k: v for k, v in changes.items() if k in editable})
            payload["source_type"] = previous["source_type"]
            norm = self._validate_and_normalize(payload, default_source_type=previous["source_type"])
            fields = ("title", "normalized_title", "category", "tags", "date_start", "date_end", "duration_min",
                      "city", "venue", "address", "lat", "lon", "description", "price_min", "price_max",
                      "is_free", "age_limit", "language", "org_link", "status", "dedup_key")
            values = {k: norm[k] for k in fields}
            values.update(self._search_fields(norm))
            values["tags"] = json.dumps(values["tags"], ensure_ascii=False)
            values["is_free"] = int(values["is_free"])
            diff = {k: {"old": previous[k], "new": values[k]} for k in fields if previous[k] != values[k]}
            if diff:
                values["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.db.execute("UPDATE events SET " + ", ".join(f"{k} = ?" for k in values) + " WHERE id = ?",
                                (*values.values(), event_id))
                self.log_audit(actor, "update_event", "event", event_id, diff)
            shared_changes = {key: value for key, value in changes.items()
                              if key not in {"date_start", "date_end", "source_url"}}
            for peer_id in peers:
                if shared_changes:
                    self.update_event_fields(peer_id, shared_changes, actor, _propagate=False)
            row = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
            record = EventRecord.from_row(row)
            record.sources = self.list_sources(event_id)
            return record

    def _update_event_status(self, event_id, new_status, actor):
        new_status = normalize_status(new_status)
        existing = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        if not existing:
            raise LookupError(f"Событие с ID '{event_id}' не найдено")

        while existing.get("merged_into"):
            existing = self.db.fetchone("SELECT * FROM events WHERE id = ?", (existing["merged_into"],))
        event_id = existing["id"]
        if new_status == "published" and existing["source_type"] != "ручной ввод":
            try:
                parsed_day = datetime.fromisoformat(existing["date_start"]).date()
                self._valid_url(existing["source_url"], "source_url")
            except (TypeError, ValueError):
                raise ValueError("Нельзя опубликовать событие с некорректной датой или source_url") from None
            if parsed_day <= datetime.now(timezone.utc).date() or any(
                not str(existing.get(field) or "").strip()
                for field in ("title", "category", "city", "venue", "address", "description")
            ):
                raise ValueError("Для публикации заполните обязательные поля и укажите будущую дату")
        old_status = existing["status"]
        now_iso = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE events SET status = ?, status_locked = 1, is_disputed = 0, dispute_reason = NULL, updated_at = ? WHERE id = ?",
            (new_status, now_iso, event_id),
        )
        self.log_audit(
            actor=actor,
            action="change_status",
            entity_type="event",
            entity_id=event_id,
            changes={"status": {"old": old_status, "new": new_status},
                     "status_locked": {"old": existing["status_locked"], "new": 1},
                     "is_disputed": {"old": existing["is_disputed"], "new": 0},
                     "dispute_reason": {"old": existing["dispute_reason"], "new": None}},
        )
        updated_row = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        record = EventRecord.from_row(updated_row)
        record.sources = self.list_sources(record.id)
        return record

    def reprocess_raw_events(self, actor: str = "system_reprocessor") -> int:
        """Переигрывание нормализации по сохранённым сырым записям без повторного сбора (Раздел 6.1 ТЗ)."""
        raw_rows = self.db.fetchall("SELECT * FROM raw_events ORDER BY ingested_at ASC")
        reprocessed = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for r in raw_rows:
            payload = json.loads(r["raw_payload_json"])
            norm = self._validate_and_normalize(payload, default_source_type=r["source_type"])
            self.upsert_normalized_event(norm, str(r["id"]), actor=actor)
            self.db.execute(
                "UPDATE raw_events SET last_reprocessed_at = ? WHERE id = ?",
                (now_iso, r["id"]),
            )
            reprocessed += 1
        return reprocessed
