import asyncio
import logging
from src.bot.router import BotRouter
from src.bot.telegram_runner import TelegramPollingRunner
from src.bot.max.runner import MaxPollingRunner
from src.bot.max.webhook import MaxWebhookRunner
from src.config import load_settings
from src.db.connection import Database
from src.utils.logger import get_logger, log_event


def bootstrap() -> tuple:
    settings = load_settings()
    logger = get_logger("afisha_rb.main", settings.log_level)
    log_event(logger, logging.INFO, "Bootstrapping Afisha Bashkortostan bot and agent service")

    db = Database(settings.database_url)
    db.fetchone("SELECT 1 AS ready")

    router = BotRouter(db, settings)
    return settings, logger, db, router


async def async_main() -> None:
    settings, logger, db, router = bootstrap()
    token = settings.telegram_bot_token
    runners = []
    if not token or token == "your_telegram_bot_token_here":
        log_event(
            logger,
            logging.WARNING,
            "TELEGRAM_BOT_TOKEN is not configured or uses placeholder. Service initialized DB & seed data and is running in local standby mode. Use 'python -m src.cli' for interactive CLI queries or set TELEGRAM_BOT_TOKEN in .env.",
        )
    else:
        runner = TelegramPollingRunner(token=token, router=router, log_level=settings.log_level)
        runners.append(runner.run_forever())
    if settings.max_bot_enabled:
        if not settings.max_bot_token:
            raise RuntimeError("MAX_BOT_ENABLED=true requires MAX_BOT_TOKEN")
        max_router = BotRouter(db, settings, platform="max")
        if settings.max_webhook_url:
            if not settings.max_webhook_secret:
                raise RuntimeError("MAX_WEBHOOK_URL requires MAX_WEBHOOK_SECRET")
            runners.append(MaxWebhookRunner(settings.max_bot_token, settings.max_webhook_url,
                                            settings.max_webhook_secret, max_router,
                                            settings.log_level).run_forever())
        else:
            runners.append(MaxPollingRunner(settings.max_bot_token, max_router, settings.log_level).run_forever())
    if not runners:
        while True:
            await asyncio.sleep(60)
    await asyncio.gather(*runners)


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
