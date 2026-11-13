"""Explicit database preparation and scheduled retention maintenance."""

import argparse
import time
from datetime import datetime, timedelta, timezone

from src.config import Settings, load_settings
from src.db.connection import Database
from src.db.migrations import apply_migrations, seed_dictionaries
from src.db.roles import provision_roles
from src.services.import_service import ImportService
from src.services.subscription_service import SubscriptionService


def prepare(db: Database, settings: Settings, *, seed_demo: bool = False) -> int:
    applied = apply_migrations(db, settings.migrations_dir)
    seed_dictionaries(db, settings.data_dir / "dictionaries.json")
    if seed_demo:
        seed_file = settings.data_dir / "seed_events.json"
        if seed_file.exists():
            ImportService(db).import_from_file(seed_file, actor="startup_seed")
    provision_roles(db)
    return applied


def prune(db: Database, settings: Settings) -> dict[str, int]:
    now = datetime.now(timezone.utc)

    def cutoff(days: int) -> str:
        if days < 1:
            raise ValueError("Срок хранения должен быть положительным")
        return (now - timedelta(days=days)).isoformat()

    feedback_before = cutoff(settings.feedback_retention_days)
    raw_before = cutoff(settings.raw_retention_days)
    report_before = cutoff(settings.import_report_retention_days)
    audit_before = cutoff(settings.audit_retention_days)
    with db.transaction():
        result = {
            "subscriptions": SubscriptionService(db, settings.subscription_retention_days).cleanup_expired(),
            "feedback_audit": db.execute(
                "DELETE FROM audit_log WHERE entity_type = 'feedback' AND created_at < ?", (feedback_before,)
            ),
            "feedback": db.execute("DELETE FROM feedback WHERE created_at < ?", (feedback_before,)),
            "raw_events": 0,
            "import_reports": 0,
            "audit_log": 0,
            "sessions": 0,
            "rate_limits": 0,
            "agent_dialogues": 0,
            "agent_sessions": 0,
            "agent_action_receipts": 0,
        }
        db.execute(
            "UPDATE events SET raw_event_id = NULL WHERE raw_event_id IN "
            "(SELECT id FROM raw_events WHERE ingested_at < ?)", (raw_before,)
        )
        result["raw_events"] = db.execute("DELETE FROM raw_events WHERE ingested_at < ?", (raw_before,))
        result["import_reports"] = db.execute("DELETE FROM import_reports WHERE created_at < ?", (report_before,))
        result["audit_log"] = db.execute("DELETE FROM audit_log WHERE created_at < ?", (audit_before,))
        result["sessions"] = db.execute(
            "DELETE FROM admin_sessions WHERE expires_at < ? OR revoked_at IS NOT NULL", (now.isoformat(),)
        )
        result["rate_limits"] = db.execute(
            "DELETE FROM request_limits WHERE window_start < ?", (int(now.timestamp()) - 3600,)
        )
        result["agent_dialogues"] = db.execute(
            "DELETE FROM agent_dialogue_states WHERE expires_at < ?", (now.isoformat(),)
        )
        result["agent_sessions"] = db.execute(
            "DELETE FROM agent_sessions WHERE expires_at < ?", (now.isoformat(),)
        )
        result["agent_action_receipts"] = db.execute(
            "DELETE FROM agent_action_receipts WHERE expires_at < ?", (now.isoformat(),)
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Подготовка и обслуживание БД афиши")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").add_argument("--seed-demo", action="store_true")
    sub.add_parser("prune").add_argument("--loop", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    db = Database(settings.database_url)
    try:
        if args.command == "prepare":
            print(f"Применено миграций: {prepare(db, settings, seed_demo=args.seed_demo)}")
        else:
            while True:
                print(f"Очистка завершена: {prune(db, settings)}", flush=True)
                if not args.loop:
                    break
                time.sleep(24 * 60 * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
