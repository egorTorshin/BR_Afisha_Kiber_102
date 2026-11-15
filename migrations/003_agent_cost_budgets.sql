CREATE TABLE IF NOT EXISTS agent_cost_budgets (
    scope TEXT NOT NULL CHECK (scope IN ('dialogue', 'user_day', 'source_day')),
    scope_id TEXT NOT NULL,
    period TEXT NOT NULL,
    spent_usd NUMERIC NOT NULL DEFAULT 0,
    reserved_usd NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, scope_id, period)
);

CREATE TABLE IF NOT EXISTS agent_cost_reservations (
    request_id TEXT PRIMARY KEY,
    dialogue_scope_id TEXT NOT NULL,
    user_scope_id TEXT NOT NULL,
    source_scope_id TEXT,
    day_period TEXT NOT NULL,
    reserved_usd NUMERIC NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_cost_budgets_period
    ON agent_cost_budgets(scope, period);
