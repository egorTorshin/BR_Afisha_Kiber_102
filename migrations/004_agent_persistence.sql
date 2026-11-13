CREATE TABLE agent_dialogue_states (
    conversation_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX idx_agent_dialogue_expires ON agent_dialogue_states(expires_at);

CREATE TABLE agent_traces (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_agent_traces_conversation ON agent_traces(conversation_id, created_at);

CREATE TABLE agent_sessions (
    token_hash TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX idx_agent_sessions_expires ON agent_sessions(expires_at);

CREATE TABLE agent_action_receipts (
    conversation_id TEXT NOT NULL,
    action_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, action_key)
);
CREATE INDEX idx_agent_action_receipts_expires ON agent_action_receipts(expires_at);

