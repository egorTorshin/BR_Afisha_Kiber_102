"""Periodic, idempotent collection of the public GKZ event listing."""

import logging
import os
import signal
import time

from src.config import load_settings
from src.db.connection import Database
from src.services.gkz_collector import GKZCollector
from src.services.import_service import ImportService
from src.utils.logger import get_logger, log_event


def main() -> None:
    interval = max(900, int(os.getenv("GKZ_COLLECTION_INTERVAL_SECONDS", "21600")))
    timeout = max(3.0, float(os.getenv("GKZ_COLLECTION_TIMEOUT_SECONDS", "20")))
    retries = min(5, max(0, int(os.getenv("GKZ_COLLECTION_RETRIES", "3"))))
    logger = get_logger("afisha_rb.gkz_scheduler", os.getenv("LOG_LEVEL", "INFO"))
    settings = load_settings()
    db = Database(settings.database_url)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log_event(logger, logging.INFO, "GKZ scheduled collection started",
              interval_seconds=interval, moderation_status="draft")
    try:
        while not stopped:
            collector = GKZCollector(timeout=timeout, retries=retries)
            succeeded = False
            try:
                result = collector.collect(ImportService(db), actor="gkz_scheduler")
                succeeded = result.ok
                report = result.report
                stats = result.stats or {}
                log_event(logger, logging.INFO if result.ok else logging.WARNING,
                          "GKZ collection finished" if result.ok else "GKZ collection failed",
                          detail=result.message,
                          source="gkz",
                          parsed=stats.get("parsed", 0),
                          accepted=stats.get("accepted", 0),
                          added=report.added_count if report else 0,
                          updated=report.updated_count if report else 0,
                          unchanged=report.unchanged_count if report else 0,
                          duplicates=stats.get("duplicates", 0),
                          ambiguous=stats.get("ambiguous", 0),
                          rejected=stats.get("rejected", 0) + (report.rejected_count if report else 0))
            except Exception as exc:
                log_event(logger, logging.ERROR, "GKZ collection crashed", error_type=type(exc).__name__)
            finally:
                collector.close()
            deadline = time.monotonic() + (interval if succeeded else min(interval, 900))
            while not stopped and time.monotonic() < deadline:
                time.sleep(min(1, deadline - time.monotonic()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
