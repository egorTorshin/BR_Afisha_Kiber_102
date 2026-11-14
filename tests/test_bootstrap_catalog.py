from pathlib import Path

from src.bootstrap import prepare
from src.config import Settings
from src.db.connection import Database


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_catalog_imports_all_records_and_restarts_without_duplicates(tmp_path):
    url = f"sqlite:///{tmp_path / 'catalog.db'}"
    settings = Settings(database_url=url, data_dir=ROOT / "data", migrations_dir=ROOT / "migrations")
    db = Database(url)
    try:
        assert prepare(db, settings, seed_demo=True) == 3
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 120
        assert db.fetchone("SELECT COUNT(*) AS n FROM events WHERE status='published'")["n"] == 118
        assert db.fetchone("SELECT COUNT(*) AS n FROM event_sources")["n"] == 120
        assert prepare(db, settings, seed_demo=True) == 0
        assert db.fetchone("SELECT COUNT(*) AS n FROM events")["n"] == 120
        assert db.fetchone("SELECT COUNT(*) AS n FROM event_sources")["n"] == 120
    finally:
        db.close()
