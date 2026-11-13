import os
import secrets
import json
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

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
    admin_username: str = field(default_factory=lambda: os.getenv("ADMIN_USERNAME", "admin"))
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))
    admin_password_hash: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD_HASH", ""))
    admin_secret: str = field(default_factory=lambda: os.getenv("ADMIN_SECRET", ""))
    moderator_username: str = field(default_factory=lambda: os.getenv("MODERATOR_USERNAME", ""))
    moderator_password: str = field(default_factory=lambda: os.getenv("MODERATOR_PASSWORD", ""))
    moderator_password_hash: str = field(default_factory=lambda: os.getenv("MODERATOR_PASSWORD_HASH", ""))
    web_origin: str = field(default_factory=lambda: os.getenv("WEB_ORIGIN", "").rstrip("/"))
    rate_limit_secret: str = field(default_factory=lambda: os.getenv("RATE_LIMIT_SECRET", "") or
                                    os.getenv("ADMIN_SECRET", "") or secrets.token_urlsafe(32))
    # Set true when the public app is served over HTTPS. Local HTTP stays usable.
    admin_cookie_secure: bool = field(default_factory=lambda: os.getenv("ADMIN_COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"})
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))
    subscription_retention_days: int = field(default_factory=lambda: int(os.getenv("SUBSCRIPTION_RETENTION_DAYS", "180")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    reference_date_str: str = field(default_factory=lambda: os.getenv("REFERENCE_DATE", "").strip())
    cors_origins: List[str] = field(default_factory=lambda: [
        value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()
    ])
    public_cache_max_age: int = field(default_factory=lambda: max(0, int(os.getenv("PUBLIC_CACHE_MAX_AGE", "60"))))
    max_import_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_IMPORT_BYTES", str(2 * 1024 * 1024))))
    max_import_rows: int = field(default_factory=lambda: int(os.getenv("MAX_IMPORT_ROWS", "2000")))
    feedback_retention_days: int = field(default_factory=lambda: int(os.getenv("FEEDBACK_RETENTION_DAYS", "365")))
    raw_retention_days: int = field(default_factory=lambda: int(os.getenv("RAW_RETENTION_DAYS", "365")))
    import_report_retention_days: int = field(default_factory=lambda: int(os.getenv("IMPORT_REPORT_RETENTION_DAYS", "365")))
    audit_retention_days: int = field(default_factory=lambda: int(os.getenv("AUDIT_RETENTION_DAYS", "730")))
    ai_mode: str = field(default_factory=lambda: os.getenv("AI_MODE", "no-ai").strip())
    jev_enabled: bool = field(default_factory=lambda: os.getenv("JEV_ENABLED", "false").lower() in {"1", "true", "yes", "on"})
    jev_endpoint: str = field(default_factory=lambda: os.getenv("JEV_ENDPOINT", "").strip())
    jev_base_url: str = field(default_factory=lambda: os.getenv("JEV_BASE_URL", "").strip())
    jev_model: str = field(default_factory=lambda: os.getenv("JEV_MODEL", "qwen2.5-1.5b-router").strip())
    jev_api_key: str = field(default_factory=lambda: os.getenv("JEV_API_KEY", "").strip())
    jev_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("JEV_TIMEOUT_SECONDS", "5")))
    jev_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("JEV_CONFIDENCE_THRESHOLD", "0.7")))
    local_llm_base_url: str = field(default_factory=lambda: os.getenv("LOCAL_LLM_BASE_URL", "").strip())
    light_max_tokens: int = field(default_factory=lambda: int(os.getenv("LIGHT_LLM_MAX_TOKENS", "384")))
    heavy_max_tokens: int = field(default_factory=lambda: int(os.getenv("HEAVY_LLM_MAX_TOKENS", "512")))
    light_model: str = field(default_factory=lambda: os.getenv("LIGHT_LLM_MODEL", "qwen2.5-3b-local").strip())
    light_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("LIGHT_LLM_CONFIDENCE_THRESHOLD", "0.75")))
    light_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LIGHT_LLM_TIMEOUT_SECONDS", "25")))
    heavy_model: str = field(default_factory=lambda: os.getenv("HEAVY_LLM_MODEL", "qwen2.5-3b-local").strip())
    heavy_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("HEAVY_LLM_TIMEOUT_SECONDS", "90")))
    heavy_context_window: int = field(default_factory=lambda: int(os.getenv("HEAVY_LLM_CONTEXT_WINDOW", "4096")))
    heavy_top_n: int = field(default_factory=lambda: int(os.getenv("HEAVY_LLM_TOP_N", "8")))
    dialogue_ttl_days: int = field(default_factory=lambda: max(1, int(os.getenv("AGENT_DIALOGUE_TTL_DAYS", "30"))))
    model_prices: dict = field(default_factory=lambda: json.loads(os.getenv("AGENT_MODEL_PRICES_JSON", "{}")))
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    migrations_dir: Path = field(default_factory=lambda: BASE_DIR / "migrations")

    def __post_init__(self) -> None:
        if self.ai_mode not in {"local-ai", "native-ai", "no-ai"}:
            raise ValueError("AI_MODE must be local-ai, native-ai or no-ai")
        if not isinstance(self.model_prices, dict):
            raise ValueError("AGENT_MODEL_PRICES_JSON must contain a JSON object")
        if self.ai_mode == "native-ai":
            for name, value in (("JEV_BASE_URL", self.jev_base_url),
                                ("LOCAL_LLM_BASE_URL", self.local_llm_base_url)):
                if not value.startswith(("http://", "https://")):
                    raise ValueError(f"{name} must be an HTTP URL in native-ai mode")
        if not (0 <= self.jev_confidence_threshold <= 1 and 0 <= self.light_confidence_threshold <= 1):
            raise ValueError("Порог уверенности модели должен быть от 0 до 1")
        if any(value <= 0 for value in (self.jev_timeout_seconds, self.light_timeout_seconds,
                                        self.heavy_timeout_seconds, self.light_max_tokens,
                                        self.heavy_max_tokens, self.heavy_context_window,
                                        self.heavy_top_n)):
            raise ValueError("Некорректные лимиты агентных моделей")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS должен содержать конкретные адреса, а не *")
        for origin in ([self.web_origin] if self.web_origin else []) + self.cors_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or
                    parsed.password or parsed.path or parsed.query or parsed.fragment):
                raise ValueError("WEB_ORIGIN и CORS_ORIGINS должны содержать только scheme://host[:port]")
        if len(self.rate_limit_secret) < 32:
            raise ValueError("RATE_LIMIT_SECRET должен содержать не менее 32 символов")
        if self.admin_password and self.admin_password_hash:
            raise ValueError("Укажите только один способ задания пароля администратора")
        if self.moderator_password and self.moderator_password_hash:
            raise ValueError("Укажите только один способ задания пароля модератора")
        if self.admin_secret and len(self.admin_secret) < 32:
            raise ValueError("ADMIN_SECRET должен содержать не менее 32 символов")
        if self.moderator_username or self.moderator_password or self.moderator_password_hash:
            if not self.moderator_username or not (self.moderator_password or self.moderator_password_hash):
                raise ValueError("Для модератора требуются имя и пароль")
        if self.admin_password_hash or self.moderator_password_hash:
            from src.services.admin_auth import valid_password_hash
            if (self.admin_password_hash and not valid_password_hash(self.admin_password_hash)) or (
                self.moderator_password_hash and not valid_password_hash(self.moderator_password_hash)
            ):
                raise ValueError("Некорректный хеш пароля администратора или модератора")
        if self.max_import_bytes < 1024 or self.max_import_rows < 1:
            raise ValueError("Некорректные ограничения импорта")
        if self.rate_limit_per_minute < 1 or any(
            getattr(self, name) < 1 for name in (
                "subscription_retention_days", "feedback_retention_days", "raw_retention_days",
                "import_report_retention_days", "audit_retention_days",
            )
        ):
            raise ValueError("Некорректный лимит запросов или срок хранения")
        raw_admins = os.getenv("ADMIN_USER_IDS")
        if raw_admins is not None:
            self.admin_user_ids = [x.strip() for x in raw_admins.split(",") if x.strip()]

    def get_reference_date(self) -> date:
        if self.reference_date_str:
            try:
                return datetime.strptime(self.reference_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        return date.today()

    def model_cost(self, usage: list[dict]) -> Decimal | None:
        """Return model cost in USD; unknown or invalid prices remain unknown."""
        if not usage:
            return None
        total = Decimal(0)
        for item in usage:
            prices = self.model_prices.get(item["model_name"])
            if not isinstance(prices, dict) or "input" not in prices or "output" not in prices:
                return None
            try:
                input_price = Decimal(str(prices["input"]))
                output_price = Decimal(str(prices["output"]))
                if not all(price.is_finite() and price >= 0 for price in (input_price, output_price)):
                    return None
                total += (input_price * item["input_tokens"] +
                          output_price * item["output_tokens"]) / 1_000_000
            except (InvalidOperation, TypeError, ValueError, KeyError):
                return None
        return total


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
