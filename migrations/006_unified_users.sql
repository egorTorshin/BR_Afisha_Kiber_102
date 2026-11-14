CREATE TABLE users (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'moderator', 'admin')),
    display_name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE user_identities (
    provider TEXT NOT NULL CHECK (provider IN ('telegram', 'max')),
    provider_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    PRIMARY KEY (provider, provider_id),
    UNIQUE (provider, user_id)
);
CREATE INDEX idx_user_identities_user ON user_identities(user_id);
CREATE TABLE user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX idx_user_sessions_user ON user_sessions(user_id);
CREATE TABLE event_views (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    channel TEXT NOT NULL CHECK (channel IN ('telegram', 'web')),
    viewed_at TEXT NOT NULL
);
CREATE INDEX idx_event_views_user_date ON event_views(user_id, viewed_at DESC, id DESC);
