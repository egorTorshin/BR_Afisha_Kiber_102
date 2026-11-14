import json
import re
from datetime import datetime, timezone
from pathlib import Path
from src.db.connection import Database

LEGACY_006_HISTORY = {
    "001_initial_schema.sql",
    "002_admin_backend.sql",
    "003_agent_persistence.sql",
    "003_security_search.sql",
    "004_agent_persistence.sql",
    "005_bot_profiles.sql",
    "006_unified_users.sql",
}


def _table_columns(db: Database, table: str) -> set[str]:
    if db.is_postgres:
        return {row["column_name"] for row in db.fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?", (table,))}
    return {row["name"] for row in db.fetchall(f"PRAGMA table_info({table})")}


def _upgrade_legacy_history(db: Database, applied: set[str]) -> None:
    """Recognize the known 006 schema and record it as the new baseline atomically."""
    if applied & LEGACY_006_HISTORY != LEGACY_006_HISTORY:
        raise RuntimeError("Legacy migration history detected; only the verified 006 schema can be upgraded automatically")
    unknown = applied - LEGACY_006_HISTORY - {"001_baseline.sql", "002_legacy_compat.sql", "002_event_submissions.sql"}
    if unknown:
        raise RuntimeError("Unknown legacy migration history; refusing automatic upgrade")
    required = {"events", "subscriptions", "event_views", "users", "user_identities"}
    for table in required:
        if not _table_columns(db, table):
            raise RuntimeError(f"Legacy schema is missing required table: {table}")
    subscription_columns = _table_columns(db, "subscriptions")
    if not {"id", "user_id", "city", "category", "created_at", "expires_at"}.issubset(subscription_columns):
        raise RuntimeError("Legacy subscriptions table does not match the verified 006 schema")
    if "001_baseline.sql" not in applied:
        db.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                   ("001_baseline.sql", datetime.now(timezone.utc).isoformat()))


def _apply_legacy_compat(db: Database) -> None:
    """Apply additive schema changes while preserving every existing row and column."""
    columns = _table_columns(db, "subscriptions")
    for column in ("period_from", "period_to"):
        if column not in columns:
            db.execute(f"ALTER TABLE subscriptions ADD COLUMN {column} TEXT")

    if db.is_postgres:
        constraints = db.fetchall("""SELECT conname, pg_get_constraintdef(oid) AS definition
                                    FROM pg_constraint
                                    WHERE conrelid = 'event_views'::regclass AND contype = 'c'""")
        channel_constraints = [row for row in constraints if "channel" in row["definition"].lower()]
        if not any("max" in row["definition"].lower() for row in channel_constraints):
            for row in channel_constraints:
                db.execute(f'ALTER TABLE event_views DROP CONSTRAINT "{row["conname"]}"')
            db.execute("ALTER TABLE event_views ADD CONSTRAINT event_views_channel_check "
                       "CHECK (channel IN ('telegram', 'max', 'web'))")
        return

    expected_event_view_columns = {"id", "user_id", "event_id", "channel", "viewed_at"}
    if _table_columns(db, "event_views") != expected_event_view_columns:
        raise RuntimeError("Legacy event_views table does not match the verified schema; refusing table rebuild")
    definition = db.fetchone("SELECT sql FROM sqlite_master WHERE type='table' AND name='event_views'")
    if not definition or "'max'" in (definition["sql"] or "").lower():
        return
    db.execute("""CREATE TABLE event_views__upgrade (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        event_id TEXT NOT NULL REFERENCES events(id),
        channel TEXT NOT NULL CHECK (channel IN ('telegram', 'max', 'web')),
        viewed_at TEXT NOT NULL
    )""")
    db.execute("""INSERT INTO event_views__upgrade (id, user_id, event_id, channel, viewed_at)
                 SELECT id, user_id, event_id, channel, viewed_at FROM event_views""")
    db.execute("DROP TABLE event_views")
    db.execute("ALTER TABLE event_views__upgrade RENAME TO event_views")
    db.execute("CREATE INDEX IF NOT EXISTS idx_event_views_user_date ON event_views(user_id, viewed_at DESC, id DESC)")


def apply_migrations(db: Database, migrations_dir: Path) -> int:
    """Apply fresh schema or the verified, additive 006-to-current compatibility upgrade."""
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        return 0
    with db.transaction():
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        if db.is_postgres:
            db.execute("LOCK TABLE schema_migrations IN EXCLUSIVE MODE")
        applied = {row["version"] for row in db.fetchall("SELECT version FROM schema_migrations")}
        names = {path.name for path in files}
        legacy = bool(applied & LEGACY_006_HISTORY)
        if legacy:
            _upgrade_legacy_history(db, applied)
            applied.add("001_baseline.sql")
        if applied - names - LEGACY_006_HISTORY:
            raise RuntimeError("Legacy migration history detected; use the data-preserving upgrade procedure")
        if not applied:
            if db.is_postgres:
                existing = db.fetchall("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename <> 'schema_migrations'")
            else:
                existing = db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name <> 'schema_migrations'")
            if existing:
                raise RuntimeError("Existing database has no baseline history; refusing to overwrite data")
        count = 0
        for path in files:
            if path.name in applied:
                continue
            if path.name == "002_legacy_compat.sql":
                _apply_legacy_compat(db)
            else:
                db.executescript(path.read_text(encoding="utf-8"))
            db.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                       (path.name, datetime.now(timezone.utc).isoformat()))
            count += 1
        return count


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
