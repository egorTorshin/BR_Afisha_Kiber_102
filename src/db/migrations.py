import json
import re
from datetime import datetime, timezone
from pathlib import Path
from src.db.connection import Database


def apply_migrations(db: Database, migrations_dir: Path) -> int:
    """Идемпотентное применение SQL-миграций. Повторный запуск не ломает схему (Раздел 10 ТЗ)."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied_rows = db.fetchall("SELECT version FROM schema_migrations")
    applied = {r["version"] for r in applied_rows}

    count = 0
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        version = sql_file.name
        if version in applied:
            continue
        with db.transaction():
            if db.is_postgres:
                db.execute("LOCK TABLE schema_migrations IN EXCLUSIVE MODE")
            if db.fetchone("SELECT version FROM schema_migrations WHERE version = ?", (version,)):
                continue
            if version == "002_admin_backend.sql":
                _make_prices_nullable(db)
            db.executescript(sql_file.read_text(encoding="utf-8"))
            if version == "002_admin_backend.sql":
                _backfill_admin_data(db)
            if version == "003_security_search.sql":
                _backfill_search_data(db)
            if version == "006_unified_users.sql":
                _backfill_users(db)
            db.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            count += 1
    return count


def _backfill_users(db: Database) -> None:
    from uuid import uuid4
    ids = {str(row['user_id']) for table in ('bot_profiles', 'subscriptions')
           for row in db.fetchall(f'SELECT DISTINCT user_id FROM {table}')}
    for telegram_id in sorted(ids):
        profile = db.fetchone('SELECT name FROM bot_profiles WHERE user_id = ?', (telegram_id,))
        user_id = str(uuid4())
        db.execute('INSERT INTO users (id, display_name, created_at) VALUES (?, ?, ?)',
                   (user_id, profile['name'] if profile else None, datetime.now(timezone.utc).isoformat()))
        db.execute('INSERT INTO user_identities (provider, provider_id, user_id) VALUES (?, ?, ?)',
                   ('telegram', telegram_id, user_id))


def _backfill_search_data(db: Database) -> None:
    from src.services.normalizer import normalize_text

    for row in db.fetchall("SELECT id, title, description, venue, city, category, tags, language FROM events"):
        try:
            tags = json.loads(row["tags"] or "[]")
        except (TypeError, ValueError):
            tags = []
        tags_text = " ".join(str(tag) for tag in tags)
        db.execute(
            """UPDATE events SET search_city = ?, search_venue = ?, search_language = ?,
               search_tags = ?, search_text = ? WHERE id = ?""",
            (
                normalize_text(row["city"]), normalize_text(row["venue"]),
                normalize_text(row["language"] or ""), normalize_text(tags_text),
                normalize_text(" ".join((row["title"], row["description"], row["venue"],
                                          row["category"], tags_text, row["language"] or ""))),
                row["id"],
            ),
        )


def _make_prices_nullable(db: Database) -> None:
    if db.is_postgres:
        db.lock_events()
        for column in ("price_min", "price_max"):
            db.execute(f"ALTER TABLE events ALTER COLUMN {column} DROP NOT NULL")
            db.execute(f"ALTER TABLE events ALTER COLUMN {column} DROP DEFAULT")
        db.execute("ALTER TABLE events ALTER COLUMN status SET DEFAULT 'published'")
        db.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS events_source_hash_key")
        return
    # SQLite cannot remove NOT NULL in place: copy all columns and preserve indexes
    # and triggers inside one transaction; any failure restores the original table.
    schema = db.fetchone("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'events'")["sql"]
    objects = db.fetchall("SELECT sql FROM sqlite_master WHERE tbl_name = 'events' AND type IN ('index', 'trigger') AND sql IS NOT NULL")
    schema = re.sub(r'CREATE TABLE\s+(?:"events"|events)', 'CREATE TABLE events__002', schema, count=1, flags=re.I)
    for column in ("price_min", "price_max"):
        schema = re.sub(rf'{column}\s+INTEGER\s+NOT NULL\s+DEFAULT 0', f'{column} INTEGER', schema, flags=re.I)
    schema = schema.replace("DEFAULT 'опубликовано'", "DEFAULT 'published'")
    schema = re.sub(r'source_hash\s+TEXT\s+NOT NULL\s+UNIQUE', 'source_hash TEXT NOT NULL', schema, flags=re.I)
    db.execute(schema)
    db.execute("INSERT INTO events__002 SELECT * FROM events")
    db.execute("DROP TABLE events")
    db.execute("ALTER TABLE events__002 RENAME TO events")
    for obj in objects:
        db.execute(obj["sql"])


def _backfill_admin_data(db: Database) -> None:
    from src.models.schemas import normalize_status
    from src.services.normalizer import compute_dedup_key, parse_price_fields
    from src.services.import_service import ImportService

    service = ImportService(db)
    rows = db.fetchall("SELECT * FROM events ORDER BY created_at, id")
    canonical = {}
    for row in rows:
        status = normalize_status(row["status"])
        key = compute_dedup_key(row["title"], row["date_start"], row["city"], row["venue"])
        prices = (row["price_min"], row["price_max"], bool(row["is_free"]))
        raw = db.fetchone("SELECT raw_payload_json FROM raw_events WHERE id = ?", (row["raw_event_id"],))
        if prices[:2] == (0, 0):
            if raw:
                try:
                    payload = json.loads(raw["raw_payload_json"])
                    prices = parse_price_fields(payload.get("price_min"), payload.get("price_max"), payload.get("is_free"))
                except (ValueError, TypeError, AttributeError):
                    pass  # Ambiguous legacy data is preserved, never guessed away.
            elif not prices[2]:
                prices = (None, None, False)
        # Preserve moderator decisions, including ones predating this migration.
        locked = bool(db.fetchone("SELECT id FROM audit_log WHERE entity_type = 'event' AND entity_id = ? AND action = 'change_status'", (row["id"],)))
        db.execute("UPDATE events SET price_min = ?, price_max = ?, is_free = ?, status = ?, dedup_key = ?, status_locked = ? WHERE id = ?",
                   (*prices[:2], int(prices[2]), status, key, int(locked), row["id"]))
        target = canonical.setdefault(key, row["id"])
        service.save_source(target, row, actor="migration:002")
        if target != row["id"]:
            service.merge_events(target, row["id"], actor="migration:002")
        service.log_audit("migration:002", "migrate_event", "event", row["id"], {
            "price_min": {"old": row["price_min"], "new": prices[0]},
            "price_max": {"old": row["price_max"], "new": prices[1]},
            "is_free": {"old": bool(row["is_free"]), "new": prices[2]},
            "status": {"old": row["status"], "new": status},
        })
    # Recover additional sources overwritten by the old importer when raw data exists.
    for raw in db.fetchall("SELECT * FROM raw_events ORDER BY ingested_at, id"):
        try:
            norm = service._validate_and_normalize(json.loads(raw["raw_payload_json"]), raw["source_type"])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        target = canonical.get(norm["dedup_key"])
        if target:
            norm["source_hash"] = raw["source_hash"]
            norm["verified_at"] = raw["last_reprocessed_at"] or raw["ingested_at"]
            service.save_source(target, norm, actor="migration:002")
    # Source uniqueness now lives in event_sources; refresh the compatibility fields.
    for row in db.fetchall("SELECT * FROM events WHERE merged_into IS NULL"):
        source = db.fetchone("SELECT * FROM event_sources WHERE event_id = ? AND source_url = ?",
                             (row["id"], row["source_url"]))
        changes = {key: {"old": row[key], "new": source[key]}
                   for key in ("source_hash", "source_type", "verified_at") if row[key] != source[key]}
        if changes:
            db.execute("UPDATE events SET source_hash = ?, source_type = ?, verified_at = ? WHERE id = ?",
                       (source["source_hash"], source["source_type"], source["verified_at"], row["id"]))
            service.log_audit("migration:002", "update_event", "event", row["id"], changes)
    db.execute("CREATE UNIQUE INDEX idx_events_active_dedup ON events(dedup_key) WHERE merged_into IS NULL")


def seed_dictionaries(db: Database, dictionaries_file: Path) -> None:
    """Идемпотентное заполнение справочников городов, категорий и площадок (Раздел 8.2 ТЗ)."""
    if not dictionaries_file.exists():
        return
    data = json.loads(dictionaries_file.read_text(encoding="utf-8"))

    for city in data.get("cities", []):
        existing = db.fetchone("SELECT id FROM cities WHERE id = ?", (city["id"],))
        if not existing:
            db.execute(
                "INSERT INTO cities (id, name, normalized_name, aliases_json) VALUES (?, ?, ?, ?)",
                (
                    city["id"],
                    city["name"],
                    city["normalized_name"],
                    json.dumps(city.get("aliases", []), ensure_ascii=False),
                ),
            )

    for cat in data.get("categories", []):
        existing = db.fetchone("SELECT code FROM categories WHERE code = ?", (cat["code"],))
        if not existing:
            db.execute(
                "INSERT INTO categories (code, title, synonyms_json) VALUES (?, ?, ?)",
                (
                    cat["code"],
                    cat["title"],
                    json.dumps(cat.get("synonyms", []), ensure_ascii=False),
                ),
            )

    for venue in data.get("venues", []):
        existing = db.fetchone("SELECT id FROM venues WHERE id = ?", (venue["id"],))
        if not existing:
            db.execute(
                "INSERT INTO venues (id, name, normalized_name, city, address, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    venue["id"],
                    venue["name"],
                    venue["name"].strip().lower(),
                    venue["city"],
                    venue["address"],
                    venue.get("lat"),
                    venue.get("lon"),
                ),
            )
