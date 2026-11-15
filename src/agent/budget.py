"""Atomic USD budget reservations for agent conversations and users."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import hmac

from src.config import Settings
from src.db.connection import Database


class _BudgetExceeded(Exception):
    def __init__(self, scope: str):
        self.scope = scope


class SqlAgentBudgetRepository:
    """Reserve worst-case model cost before inference and settle actual trace cost."""

    def __init__(self, db: Database, principal_secret: str):
        self.db = db
        self.secret = principal_secret.encode("utf-8")

    def _scope_id(self, value: str) -> str:
        return hmac.new(self.secret, str(value).encode("utf-8"), hashlib.sha256).hexdigest()

    def reserve(self, *, request_id: str, conversation_id: str, user_id: str,
                amount: Decimal, dialogue_limit: Decimal, daily_limit: Decimal,
                source_id: str | None = None,
                now: datetime | None = None) -> str | None:
        amount = Decimal(amount)
        moment = now or datetime.now(timezone.utc)
        day = moment.astimezone(timezone.utc).date().isoformat()
        dialogue_scope_id, user_scope_id = self._scope_id(conversation_id), self._scope_id(user_id)
        source_scope_id = self._scope_id(source_id) if source_id else None
        scopes = [
            ("dialogue", dialogue_scope_id, "all", Decimal(dialogue_limit)),
            ("user_day", user_scope_id, day, Decimal(daily_limit)),
        ]
        if source_id:
            scopes.append(("source_day", self._scope_id(source_id), day, Decimal(daily_limit)))
        try:
            with self.db.transaction():
                if self.db.fetchone("SELECT request_id FROM agent_cost_reservations WHERE request_id = ?",
                                    (request_id,)):
                    raise _BudgetExceeded("duplicate")
                for scope, scope_id, period, limit in scopes:
                    self.db.execute(
                        "INSERT INTO agent_cost_budgets (scope, scope_id, period) VALUES (?, ?, ?) "
                        "ON CONFLICT(scope, scope_id, period) DO NOTHING",
                        (scope, scope_id, period),
                    )
                    updated = self.db.execute(
                        "UPDATE agent_cost_budgets SET reserved_usd = "
                        "CAST(reserved_usd AS NUMERIC) + CAST(? AS NUMERIC) "
                        "WHERE scope = ? AND scope_id = ? AND period = ? "
                        "AND CAST(spent_usd AS NUMERIC) + CAST(reserved_usd AS NUMERIC) "
                        "+ CAST(? AS NUMERIC) <= CAST(? AS NUMERIC)",
                        (str(amount), scope, scope_id, period, str(amount), str(limit)),
                    )
                    if updated != 1:
                        raise _BudgetExceeded(scope)
                self.db.execute(
                    "INSERT INTO agent_cost_reservations "
                    "(request_id, dialogue_scope_id, user_scope_id, source_scope_id, day_period, reserved_usd, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (request_id, dialogue_scope_id, user_scope_id, source_scope_id, day,
                     str(amount), moment.isoformat()),
                )
        except _BudgetExceeded as exc:
            return exc.scope
        return None

    def settle(self, request_id: str, actual_cost: Decimal) -> None:
        actual = Decimal(actual_cost)
        if not actual.is_finite() or actual < 0:
            raise ValueError("Invalid actual model cost")
        with self.db.transaction():
            hold = self.db.fetchone(
                "SELECT * FROM agent_cost_reservations WHERE request_id = ?", (request_id,)
            )
            if hold is None:
                return
            for scope, scope_id, period in (
                ("dialogue", hold["dialogue_scope_id"], "all"),
                ("user_day", hold["user_scope_id"], hold["day_period"]),
            ):
                self.db.execute(
                    "UPDATE agent_cost_budgets SET "
                    "reserved_usd = CAST(reserved_usd AS NUMERIC) - CAST(? AS NUMERIC), "
                    "spent_usd = CAST(spent_usd AS NUMERIC) + CAST(? AS NUMERIC) "
                    "WHERE scope = ? AND scope_id = ? AND period = ?",
                    (str(hold["reserved_usd"]), str(actual), scope, scope_id, period),
                )
            if hold["source_scope_id"]:
                self.db.execute(
                    "UPDATE agent_cost_budgets SET "
                    "reserved_usd = CAST(reserved_usd AS NUMERIC) - CAST(? AS NUMERIC), "
                    "spent_usd = CAST(spent_usd AS NUMERIC) + CAST(? AS NUMERIC) "
                    "WHERE scope = 'source_day' AND scope_id = ? AND period = ?",
                    (str(hold["reserved_usd"]), str(actual), hold["source_scope_id"],
                     hold["day_period"]),
                )
            self.db.execute("DELETE FROM agent_cost_reservations WHERE request_id = ?", (request_id,))

    def release(self, request_id: str) -> None:
        self.settle(request_id, Decimal(0))

    def balances(self, *, conversation_id: str, user_id: str,
                 now: datetime | None = None) -> dict[str, Decimal]:
        day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
        scopes = {
            "dialogue": (self._scope_id(conversation_id), "all"),
            "user_day": (self._scope_id(user_id), day),
        }
        result = {}
        for scope, (scope_id, period) in scopes.items():
            row = self.db.fetchone(
                "SELECT spent_usd, reserved_usd FROM agent_cost_budgets "
                "WHERE scope = ? AND scope_id = ? AND period = ?",
                (scope, scope_id, period),
            )
            result[scope] = Decimal(str(row["spent_usd"])) if row else Decimal(0)
            result[f"{scope}_reserved"] = Decimal(str(row["reserved_usd"])) if row else Decimal(0)
        return result


def estimate_request_cost(settings: Settings, message: str) -> Decimal | None:
    """Upper-bound plausible inference usage from configured model token limits."""
    if settings.ai_mode == "no-ai":
        return Decimal(0)
    candidates: list[tuple[str, int, int]] = []
    if settings.jev_enabled:
        candidates.append((settings.jev_model, 256, 96))
    candidates.append((settings.light_model, settings.heavy_context_window,
                       settings.light_max_tokens))
    if settings.adaptive_agent_enabled:
        candidates.append((settings.light_model, settings.heavy_context_window,
                           settings.light_max_tokens))
    # Heavy planning allows eight model steps, each with at most one repair call.
    candidates.extend((settings.heavy_model, settings.heavy_context_window,
                       settings.heavy_max_tokens) for _ in range(16))
    total = Decimal(0)
    for model, input_tokens, output_tokens in candidates:
        prices = settings.model_prices.get(model)
        if not isinstance(prices, dict) or "input" not in prices or "output" not in prices:
            # Local inference has no provider charge. Remote usage must be priced
            # before it can pass a configured spend budget.
            if (settings.ai_mode == "local-ai" and not settings.jev_enabled
                    and not settings.model_prices and model in {
                        settings.light_model, settings.heavy_model}):
                continue
            return None
        try:
            input_price, output_price = Decimal(str(prices["input"])), Decimal(str(prices["output"]))
            if not all(item.is_finite() and item >= 0 for item in (input_price, output_price)):
                return None
            total += (input_price * input_tokens + output_price * output_tokens) / Decimal(1_000_000)
        except (InvalidOperation, TypeError, ValueError):
            return None
    # Round reservations upward to the same 12 decimal places persisted by PostgreSQL.
    return total.quantize(Decimal("0.000000000001"), rounding=ROUND_CEILING)
