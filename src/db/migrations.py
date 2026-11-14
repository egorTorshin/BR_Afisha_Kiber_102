import json
import re
from datetime import datetime, timezone
from pathlib import Path
from src.db.connection import Database


def apply_migrations(db: Database, migrations_dir: Path) -> int:
    """Apply the fresh baseline atomically and idempotently.

    A database with legacy versions requires the separate data-preserving upgrade
    procedure; it must never be mistaken for an empty installation.
    """
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        return 0
    with db.transaction():
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        if db.is_postgres:
            db.execute("LOCK TABLE schema_migrations IN EXCLUSIVE MODE")
        applied = {row["version"] for row in db.fetchall("SELECT version FROM schema_migrations")}
        names = {path.name for path in files}
        if applied - names:
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
