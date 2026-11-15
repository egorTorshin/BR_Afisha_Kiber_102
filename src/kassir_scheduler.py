"""Periodic collection of KASSIR.RU Ufa and regional listings."""

import logging
import os
import signal
import time

from src.config import load_settings
from src.db.connection import Database
from src.services.import_service import ImportService
from src.services.kassir_collector import KassirCollector
from src.utils.logger import get_logger, log_event


def main() -> None:
    interval = max(3600, int(os.getenv("KASSIR_COLLECTION_INTERVAL_SECONDS", "21600")))
    timeout = max(3.0, float(os.getenv("KASSIR_COLLECTION_TIMEOUT_SECONDS", "20")))
    retries = min(5, max(0, int(os.getenv("KASSIR_COLLECTION_RETRIES", "2"))))
    logger = get_logger("afisha_rb.kassir_scheduler", os.getenv("LOG_LEVEL", "INFO"))
    db = Database(load_settings().database_url)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            collector = KassirCollector(timeout=timeout, retries=retries)
            succeeded = False
            try:
                result = collector.run(ImportService(db))
                succeeded = result.ok
                report = result.report
                log_event(logger, logging.INFO if result.ok else logging.WARNING,
                          "KASSIR collection finished" if result.ok else "KASSIR collection incomplete",
                          detail=result.message, added=report.added_count if report else 0,
                          updated=report.updated_count if report else 0,
                          unchanged=report.unchanged_count if report else 0, **result.stats)
            except Exception as exc:
                log_event(logger, logging.ERROR, "KASSIR collection crashed",
                          error_type=type(exc).__name__, detail=str(exc)[:300])
            finally:
                collector.close()
            deadline = time.monotonic() + (interval if succeeded else min(interval, 900))
            while not stopped and time.monotonic() < deadline:
                time.sleep(min(1, deadline - time.monotonic()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
