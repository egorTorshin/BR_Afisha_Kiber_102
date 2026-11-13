import csv
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from src.db.connection import Database
from src.models.schemas import (
    EventRecord,
    ImportReport,
    VALID_AGE_LIMITS,
    VALID_CATEGORIES,
    VALID_SOURCE_TYPES,
    VALID_STATUSES,
)
from src.services.normalizer import (
    compute_dedup_key,
    compute_source_hash,
    normalize_text,
    parse_iso_datetime,
    parse_price_fields,
    resolve_category,
    resolve_city,
)


class ImportService:
    """
    Сервис импорта CSV/JSON, ручного ввода, хранения сырых данных,
    валидации, пометки спорных карточек и ведения журнала изменений (Раздел 6.1, 8 ТЗ).
    """

    REQUIRED_FIELDS = ("title", "category", "date_start", "city", "venue", "address", "description", "source_url")

    def __init__(self, db: Database) -> None:
        self.db = db

    def log_audit(
        self,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        changes: Dict[str, Any],
    ) -> None:
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

    def _validate_and_normalize(self, raw_item: Dict[str, Any], default_source_type: str = "импорт") -> Dict[str, Any]:
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

        duration_raw = raw_item.get("duration_min")
        duration_min: Optional[int] = None
        if duration_raw not in (None, ""):
            try:
                duration_min = int(float(duration_raw))
                if duration_min <= 0:
                    raise ValueError("Длительность должна быть положительным числом")
            except ValueError as e:
                raise ValueError(f"Некорректная длительность duration_min: {duration_raw}") from e

        raw_city = str(raw_item["city"]).strip()
        resolved_city = resolve_city(raw_city, db=self.db) or raw_city
        venue = str(raw_item["venue"]).strip()
        address = str(raw_item["address"]).strip()
        description = str(raw_item["description"]).strip()

        source_url = str(raw_item["source_url"]).strip()
        if not (source_url.startswith("http://") or source_url.startswith("https://")):
            raise ValueError(f"Поле source_url должно быть валидным URL (http/https): '{source_url}'")

        org_link_raw = raw_item.get("org_link")
        org_link = str(org_link_raw).strip() if org_link_raw and str(org_link_raw).strip() else None

        price_min, price_max, is_free = parse_price_fields(
            raw_item.get("price_min", 0),
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

        lat_raw = raw_item.get("lat")
        lon_raw = raw_item.get("lon")
        lat = float(lat_raw) if lat_raw not in (None, "") else None
        lon = float(lon_raw) if lon_raw not in (None, "") else None

        language_raw = raw_item.get("language")
        language = str(language_raw).strip().lower() if language_raw and str(language_raw).strip() else "русский"

        source_type = str(raw_item.get("source_type") or default_source_type).strip()
        if source_type not in VALID_SOURCE_TYPES:
            source_type = default_source_type

        confidence_raw = raw_item.get("confidence", 1.0)
        try:
            confidence = float(confidence_raw if confidence_raw not in (None, "") else 1.0)
        except ValueError:
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        status = str(raw_item.get("status") or "опубликовано").strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError(f"Недопустимый статус '{status}'. Допустимые: {', '.join(sorted(VALID_STATUSES))}")

        # Определение спорной карточки (Раздел 6.1: «обязательные поля заполнены, спорные помечены»)
        dispute_reasons: List[str] = []
        if raw_item.get("is_disputed") in (True, 1, "true", "True", "1"):
            if raw_item.get("dispute_reason"):
                dispute_reasons.append(str(raw_item["dispute_reason"]))
            else:
                dispute_reasons.append("Помечено как спорное источником")
        if confidence < 0.8:
            dispute_reasons.append(f"Низкая уверенность извлечения ({confidence:.2f})")
        if not is_free and not org_link:
            dispute_reasons.append("Платное событие без ссылки на билеты/организатора (org_link)")
        if len(description) < 20:
            dispute_reasons.append("Слишком краткое описание мероприятия (< 20 символов)")

        is_disputed = len(dispute_reasons) > 0
        dispute_reason = "; ".join(dict.fromkeys(dispute_reasons)) if is_disputed else None

        source_hash = str(raw_item.get("source_hash") or compute_source_hash({
            "title": title,
            "date_start": date_start,
            "city": resolved_city,
            "venue": venue,
            "source_url": source_url,
        }))
        dedup_key = str(raw_item.get("dedup_key") or compute_dedup_key(title, date_start, resolved_city, venue))
        event_id = str(raw_item.get("id") or uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        verified_at = str(raw_item.get("verified_at") or now_iso)

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
            "confidence": confidence,
            "status": status,
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
        existing = self.db.fetchone(
            "SELECT * FROM events WHERE source_hash = ? OR id = ? OR dedup_key = ?",
            (norm["source_hash"], norm["id"], norm["dedup_key"]),
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(norm["tags"], ensure_ascii=False)

        if not existing:
            self.db.execute(
                """
                INSERT INTO events (
                    id, raw_event_id, title, normalized_title, category, tags,
                    date_start, date_end, duration_min, city, venue, address,
                    lat, lon, description, price_min, price_max, is_free,
                    age_limit, language, org_link, source_url, source_type,
                    source_hash, verified_at, confidence, status, dedup_key,
                    is_disputed, dispute_reason, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
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
                ),
            )
            self.log_audit(
                actor=actor,
                action="create_event",
                entity_type="event",
                entity_id=norm["id"],
                changes={"title": norm["title"], "status": norm["status"], "city": norm["city"]},
            )
            return "added"

        # Проверяем, изменились ли содержательные поля (идемпотентность при повторном импорте того же файла)
        compare_fields = [
            ("title", existing["title"], norm["title"]),
            ("category", existing["category"], norm["category"]),
            ("date_start", existing["date_start"], norm["date_start"]),
            ("date_end", existing.get("date_end"), norm["date_end"]),
            ("city", existing["city"], norm["city"]),
            ("venue", existing["venue"], norm["venue"]),
            ("address", existing["address"], norm["address"]),
            ("description", existing["description"], norm["description"]),
            ("price_min", int(existing.get("price_min") or 0), norm["price_min"]),
            ("price_max", int(existing.get("price_max") or 0), norm["price_max"]),
            ("is_free", bool(existing.get("is_free")), norm["is_free"]),
            ("age_limit", int(existing.get("age_limit") or 0), norm["age_limit"]),
            ("status", existing["status"], norm["status"]),
        ]
        diff = {field: {"old": old_v, "new": new_v} for field, old_v, new_v in compare_fields if old_v != new_v}
        if not diff:
            return "unchanged"

        target_id = str(existing["id"])
        self.db.execute(
            """
            UPDATE events SET
                raw_event_id = ?, title = ?, normalized_title = ?, category = ?, tags = ?,
                date_start = ?, date_end = ?, duration_min = ?, city = ?, venue = ?, address = ?,
                lat = ?, lon = ?, description = ?, price_min = ?, price_max = ?, is_free = ?,
                age_limit = ?, language = ?, org_link = ?, source_url = ?, source_type = ?,
                verified_at = ?, confidence = ?, status = ?, dedup_key = ?,
                is_disputed = ?, dispute_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                raw_event_id or existing.get("raw_event_id"),
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
                norm["verified_at"],
                norm["confidence"],
                norm["status"],
                norm["dedup_key"],
                1 if norm["is_disputed"] else 0,
                norm["dispute_reason"],
                now_iso,
                target_id,
            ),
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
    ) -> ImportReport:
        added = 0
        updated = 0
        unchanged = 0
        rejected = 0
        disputed = 0
        rejected_reasons: List[Dict[str, Any]] = []
        disputed_items: List[Dict[str, Any]] = []

        for idx, raw_item in enumerate(records, start=1):
            try:
                norm = self._validate_and_normalize(raw_item, default_source_type=source_type)
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
                rejected_reasons.append({
                    "row": idx,
                    "title": str(raw_item.get("title", "<без названия>")),
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
        suffix = file_path.suffix.lower()
        content = file_path.read_text(encoding="utf-8-sig")
        return self.import_from_content(content, filename=file_path.name, suffix=suffix, actor=actor)

    def import_from_content(
        self,
        content: str,
        filename: str,
        suffix: str,
        actor: str = "moderator",
    ) -> ImportReport:
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
            records = [dict(row) for row in reader]
        else:
            raise ValueError(f"Неподдерживаемый формат файла '{suffix}'. Поддерживаются .csv и .json")
        return self.import_records(records, source_file=filename, actor=actor, source_type="импорт")

    def create_event_manual(self, data: Dict[str, Any], actor: str = "moderator") -> Tuple[str, EventRecord]:
        data = dict(data)
        data["source_type"] = "ручной ввод"
        norm = self._validate_and_normalize(data, default_source_type="ручной ввод")
        raw_id = self._save_raw_event(data, "ручной ввод", "manual_admin_input", norm["source_hash"])
        outcome = self.upsert_normalized_event(norm, raw_id, actor=actor)
        row = self.db.fetchone(
            "SELECT * FROM events WHERE source_hash = ? OR id = ?",
            (norm["source_hash"], norm["id"]),
        )
        return outcome, EventRecord.from_row(row)

    def update_event_status(self, event_id: str, new_status: str, actor: str = "moderator") -> EventRecord:
        new_status = new_status.strip().lower()
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Недопустимый статус '{new_status}'. Допустимые: {', '.join(sorted(VALID_STATUSES))}")
        existing = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        if not existing:
            raise LookupError(f"Событие с ID '{event_id}' не найдено")

        old_status = existing["status"]
        now_iso = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE events SET status = ?, is_disputed = 0, updated_at = ? WHERE id = ?",
            (new_status, now_iso, event_id),
        )
        self.log_audit(
            actor=actor,
            action="change_status",
            entity_type="event",
            entity_id=event_id,
            changes={"status": {"old": old_status, "new": new_status}},
        )
        updated_row = self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        return EventRecord.from_row(updated_row)

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
