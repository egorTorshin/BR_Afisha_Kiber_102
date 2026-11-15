from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from src.agent.budget import SqlAgentBudgetRepository, estimate_request_cost
from src.agent.light_llm import LightDecision
from src.agent.schemas import ConstraintSet, ToolCall
from src.config import Settings
from src.db.connection import Database
from src.db.migrations import apply_migrations
from src.services.agent_service import AgentService
from src.services.import_service import ImportService
from tests.test_agent_tools import db  # noqa: F401
from tests.test_backend import event


def budget_repo(db):
    return SqlAgentBudgetRepository(db, "stage63-budget-test-secret-32-chars")


def test_budget_boundary_allows_exact_limit_and_blocks_overage(db):
    repo = budget_repo(db)
    assert repo.reserve(request_id="at-limit", conversation_id="c", user_id="u",
                        amount=Decimal("1"), dialogue_limit=Decimal("1"),
                        daily_limit=Decimal("1")) is None
    repo.settle("at-limit", Decimal("0.25"))
    assert repo.reserve(request_id="to-boundary", conversation_id="c", user_id="u",
                        amount=Decimal("0.75"), dialogue_limit=Decimal("1"),
                        daily_limit=Decimal("1")) is None
    repo.release("to-boundary")
    assert repo.reserve(request_id="over-limit", conversation_id="c", user_id="u",
                        amount=Decimal("0.750001"), dialogue_limit=Decimal("1"),
                        daily_limit=Decimal("1")) == "dialogue"


def test_budget_settings_accept_zero_and_reject_negative_or_nonfinite_values():
    assert Settings(agent_dialogue_cost_limit_usd=Decimal(0),
                    agent_daily_user_cost_limit_usd="0").agent_dialogue_cost_limit_usd == 0
    with pytest.raises(ValueError):
        Settings(agent_dialogue_cost_limit_usd=Decimal("-0.01"))
    with pytest.raises(ValueError):
        Settings(agent_daily_user_cost_limit_usd=Decimal("NaN"))


def test_parallel_reservations_cannot_both_spend_the_same_remaining_budget(db):
    repo = budget_repo(db)

    def reserve(index):
        return repo.reserve(request_id=f"parallel-{index}", conversation_id="c", user_id="u",
                            amount=Decimal("0.60"), dialogue_limit=Decimal("1"),
                            daily_limit=Decimal("1"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert results.count(None) == 1
    assert results.count("dialogue") == 7


def test_anonymous_daily_budget_also_covers_a_stable_network_scope(db):
    repo = budget_repo(db)
    kwargs = dict(amount=Decimal("1"), dialogue_limit=Decimal("5"),
                  daily_limit=Decimal("1"), source_id="127.0.0.1")
    assert repo.reserve(request_id="anonymous-a", conversation_id="a", user_id="visitor-a",
                        **kwargs) is None
    repo.settle("anonymous-a", Decimal("1"))
    assert repo.reserve(request_id="anonymous-b", conversation_id="b", user_id="visitor-b",
                        **kwargs) == "source_day"


def cost_settings(**changes):
    values = dict(
        ai_mode="local-ai", light_model="light-mock", heavy_model="heavy-mock",
        model_prices={
            "light-mock": {"input": "100", "output": "100"},
            "heavy-mock": {"input": "100", "output": "100"},
        },
        agent_dialogue_cost_limit_usd=Decimal("10"),
        agent_daily_user_cost_limit_usd=Decimal("10"),
    )
    values.update(changes)
    return Settings(**values)


def test_local_router_and_models_have_no_provider_charge():
    local = Settings(ai_mode="local-ai", jev_enabled=True, model_prices={})
    assert estimate_request_cost(local, "Когда идет Щелкунчик?") == Decimal(0)
    remote = Settings(ai_mode="native-ai", jev_enabled=True, model_prices={},
                      jev_base_url="http://127.0.0.1:8081/v1",
                      local_llm_base_url="http://127.0.0.1:8082/v1")
    assert estimate_request_cost(remote, "Когда идет Щелкунчик?") is None


def make_service(db, cfg, *, called=None):
    def light_factory(settings, registry):
        class MockLight:
            def parse(self, message, state):
                if called is not None:
                    called.append(message)
                return LightDecision(
                    requires_heavy=False,
                    proposed_call=ToolCall(name="search_events", arguments={"city": "Уфа"}),
                    constraints=ConstraintSet(city="Уфа"),
                    model_name=settings.light_model,
                    latency_ms=0,
                    input_tokens=10,
                    output_tokens=4,
                    schema_valid=True,
                )
        return MockLight()

    return AgentService(db, cfg, light_factory=light_factory)


def test_trace_cost_and_budget_ledger_use_the_same_actual_charge(tmp_path):
    path = tmp_path / "budget-trace.db"
    database = Database(str(path))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    ImportService(database).create_event_manual(event())
    cfg = cost_settings()
    svc = make_service(database, cfg)

    result = svc.chat("Концерты в Уфе", "dialogue-1", user_id="user-1", request_id="usage-1")
    trace = svc.get_trace(result.request_id)
    balance = svc.budget_repo.balances(conversation_id="dialogue-1", user_id="user-1")

    expected = cfg.model_cost(trace.model_usage)
    assert trace.cost == expected and expected > 0
    assert sum(item["input_tokens"] for item in trace.model_usage) == trace.input_tokens
    assert sum(item["output_tokens"] for item in trace.model_usage) == trace.output_tokens
    assert balance["dialogue"] == trace.cost
    assert balance["user_day"] == trace.cost
    assert balance["dialogue_reserved"] == Decimal(0)
    database.close()


def test_dialogue_and_daily_limits_fallback_before_model_call(tmp_path):
    path = tmp_path / "budget-fallback.db"
    database = Database(str(path))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    ImportService(database).create_event_manual(event())
    cfg = cost_settings(agent_dialogue_cost_limit_usd=Decimal("0"),
                        agent_daily_user_cost_limit_usd=Decimal("0"))
    called = []
    svc = make_service(database, cfg, called=called)

    result = svc.chat("Концерты в Уфе", "dialogue-1", user_id="user-1", request_id="blocked-1")
    trace = svc.get_trace(result.request_id)

    assert not called
    assert "Лимит расходов достигнут" in result.response.message
    assert trace.fallback_reason == "agent_budget_limit_exceeded"
    assert trace.cost in {None, Decimal(0)}
    database.close()


@pytest.mark.parametrize("limited_scope", ["dialogue", "user_day"])
def test_dialogue_and_daily_limits_block_next_request_before_model_call(tmp_path, limited_scope):
    path = tmp_path / "budget-scopes.db"
    database = Database(str(path))
    apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    ImportService(database).create_event_manual(event())
    provisional = cost_settings()
    estimate = estimate_request_cost(provisional, "Концерты в Уфе")
    cfg = cost_settings(
        agent_dialogue_cost_limit_usd=estimate if limited_scope == "dialogue" else estimate * 10,
        agent_daily_user_cost_limit_usd=estimate if limited_scope == "user_day" else estimate * 10,
    )
    called = []
    svc = make_service(database, cfg, called=called)

    first = svc.chat("Концерты в Уфе", "dialogue-a", user_id="user-a", request_id="scope-1")
    next_conversation = "dialogue-a" if limited_scope == "dialogue" else "dialogue-b"
    second = svc.chat("Концерты в Уфе", next_conversation, user_id="user-a", request_id="scope-2")

    assert svc.get_trace(first.request_id).cost > 0
    assert svc.get_trace(second.request_id).fallback_reason == "agent_budget_limit_exceeded"
    assert len(called) == 1
    database.close()
