import asyncio
import logging
from src.bot.router import BotRouter
from src.bot.telegram_runner import TelegramPollingRunner
from src.config import load_settings
from src.db.connection import Database
from src.utils.logger import get_logger, log_event


def bootstrap() -> tuple:
    settings = load_settings()
    logger = get_logger("afisha_rb.main", settings.log_level)
    log_event(logger, logging.INFO, "Bootstrapping Afisha Bashkortostan service (Level 1)")

    db = Database(settings.database_url)
    db.fetchone("SELECT 1 AS ready")

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
