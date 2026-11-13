import asyncio
import logging
import time
from src.bot.router import BotRouter
from src.bot.telegram_runner import TelegramPollingRunner
from src.config import load_settings
from src.db.connection import Database
from src.db.migrations import apply_migrations, seed_dictionaries
from src.services.import_service import ImportService
from src.utils.logger import get_logger, log_event


def bootstrap() -> tuple:
    settings = load_settings()
    logger = get_logger("afisha_rb.main", settings.log_level)
    log_event(logger, logging.INFO, "Bootstrapping Afisha Bashkortostan service (Level 1)")

    # Ожидание готовности БД и применение миграций
    db = Database(settings.database_url)
    for attempt in range(1, 15):
        try:
            applied = apply_migrations(db, settings.migrations_dir)
            log_event(logger, logging.INFO, "Database migrations verified", applied_count=applied)
            break
        except Exception as exc:
            log_event(logger, logging.WARNING, "Waiting for database readiness...", attempt=attempt, error=str(exc))
            time.sleep(2)
    else:
        applied = apply_migrations(db, settings.migrations_dir)

    seed_dictionaries(db, settings.data_dir / "dictionaries.json")

    imp = ImportService(db)
    seed_events_path = settings.data_dir / "seed_events.json"
    if seed_events_path.exists():
        rep = imp.import_from_file(seed_events_path, actor="startup_seed")
        log_event(
            logger,
            logging.INFO,
            "Prepared seed events loaded idempotently",
            added=rep.added_count,
            unchanged=rep.unchanged_count,
            disputed=rep.disputed_count,
        )

    router = BotRouter(db, settings)
    return settings, logger, db, router


async def async_main() -> None:
    settings, logger, db, router = bootstrap()
    token = settings.telegram_bot_token
    if not token or token == "your_telegram_bot_token_here":
        log_event(
            logger,
            logging.WARNING,
            "TELEGRAM_BOT_TOKEN is not configured or uses placeholder. Service initialized DB & seed data and is running in local standby mode. Use 'python -m src.cli' for interactive CLI queries or set TELEGRAM_BOT_TOKEN in .env.",
        )
        while True:
            await asyncio.sleep(60)
    else:
        runner = TelegramPollingRunner(token=token, router=router, log_level=settings.log_level)
        await runner.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
