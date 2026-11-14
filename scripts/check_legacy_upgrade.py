"""Read-only preflight before a separately planned legacy database upgrade."""

import argparse
import json

from src.db.connection import Database


def inspect(database_url: str) -> dict:
    db = Database(database_url)
    try:
        if db.is_postgres:
            columns = {row["column_name"] for row in db.fetchall(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'subscriptions'")}
            profile_columns = {row["column_name"] for row in db.fetchall(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'bot_profiles'")}
        else:
            columns = {row["name"] for row in db.fetchall("PRAGMA table_info(subscriptions)")}
            profile_columns = {row["name"] for row in db.fetchall("PRAGMA table_info(bot_profiles)")}
        versions = [row["version"] for row in db.fetchall("SELECT version FROM schema_migrations ORDER BY version")]
        return {
            "versions": versions,
            "ready_for_baseline": "001_baseline.sql" in versions,
            "legacy_006_present": "006_unified_users.sql" in versions,
            "subscription_period_columns": sorted(columns & {"period_from", "period_to"}),
            "legacy_phone_column_present": "phone" in profile_columns,
            "row_counts": {table: db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"]
                           for table in ("events", "subscriptions", "users", "agent_dialogue_states")},
        }
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only legacy database preflight")
    parser.add_argument("database_url", help="SQLite path/URL or PostgreSQL DSN")
    args = parser.parse_args()
    print(json.dumps(inspect(args.database_url), ensure_ascii=False, indent=2))
