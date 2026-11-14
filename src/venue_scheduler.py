"""Periodic collection of official Ufa venue pages."""

import logging
import os
import signal
import time

from src.config import load_settings
from src.db.connection import Database
from src.services.import_service import ImportService
from src.services.venue_collector import VENUES, VenueCollector
from src.utils.logger import get_logger, log_event


def main() -> None:
    interval = max(900, int(os.getenv("VENUE_COLLECTION_INTERVAL_SECONDS", "21600")))
    logger = get_logger("afisha_rb.venue_scheduler", os.getenv("LOG_LEVEL", "INFO"))
    db = Database(load_settings().database_url)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            all_succeeded = True
            for venue in VENUES:
                if stopped:
                    break
                collector = VenueCollector(venue)
                try:
                    result = collector.run(ImportService(db))
                    all_succeeded = all_succeeded and result.ok
                    report = result.report
                    stats = dict(result.stats)
                    stats["rejected"] += report.rejected_count if report else 0
                    log_event(logger, logging.INFO if result.ok else logging.WARNING,
                              "Venue collection finished" if result.ok else "Venue collection failed",
                              detail=result.message, added=report.added_count if report else 0,
                              updated=report.updated_count if report else 0,
                              unchanged=report.unchanged_count if report else 0, **stats)
                except Exception as exc:
                    all_succeeded = False
                    log_event(logger, logging.ERROR, "Venue collection crashed", source=venue.code,
                              error_type=type(exc).__name__)
                finally:
                    collector.close()
                if not stopped:
                    time.sleep(1)
            deadline = time.monotonic() + (interval if all_succeeded else min(interval, 900))
            while not stopped and time.monotonic() < deadline:
                time.sleep(min(1, deadline - time.monotonic()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
