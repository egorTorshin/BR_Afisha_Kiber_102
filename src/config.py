import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            f"sqlite:///{BASE_DIR / 'data' / 'afisha_local.db'}"
        ).strip()
    )
    admin_user_ids: List[str] = field(default_factory=list)
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))
    subscription_retention_days: int = field(default_factory=lambda: int(os.getenv("SUBSCRIPTION_RETENTION_DAYS", "180")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    reference_date_str: str = field(default_factory=lambda: os.getenv("REFERENCE_DATE", "2026-11-13").strip())
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    migrations_dir: Path = field(default_factory=lambda: BASE_DIR / "migrations")

    def __post_init__(self) -> None:
        raw_admins = os.getenv("ADMIN_USER_IDS", "123456789")
        self.admin_user_ids = [x.strip() for x in raw_admins.split(",") if x.strip()]

    def get_reference_date(self) -> date:
        if self.reference_date_str:
            try:
                return datetime.strptime(self.reference_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        return date.today()


def load_settings() -> Settings:
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())
    return Settings()
