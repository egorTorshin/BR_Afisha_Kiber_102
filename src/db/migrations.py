import json
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
        sql_text = sql_file.read_text(encoding="utf-8")
        db.executescript(sql_text)
        db.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
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
