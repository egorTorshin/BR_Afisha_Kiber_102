-- Fresh database baseline equivalent to the schema after 006_unified_users.
-- Never apply this baseline to a populated legacy database.
-- 001_initial_schema.sql
-- Реляционная схема данных «Афиша культуры Башкортостана» (Раздел 8 ТЗ)

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Справочник городов Республики Башкортостан (Раздел 8.2)
CREATE TABLE IF NOT EXISTS cities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL UNIQUE,
    aliases_json TEXT NOT NULL DEFAULT '[]'
);

-- Справочник категорий (Раздел 8.1, 8.2)
CREATE TABLE IF NOT EXISTS categories (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL UNIQUE,
    synonyms_json TEXT NOT NULL DEFAULT '[]'
);

-- Справочник площадок (Раздел 8.2)
CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT NOT NULL,
    lat REAL,
    lon REAL,
    UNIQUE(normalized_name, city)
);

-- Сырые данные источника (Раздел 6.1, 8.2): хранятся отдельно от нормализованных
CREATE TABLE IF NOT EXISTS raw_events (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    last_reprocessed_at TEXT
);

-- Основная таблица событий (Раздел 8.1)
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    raw_event_id TEXT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    date_start TEXT NOT NULL,
    date_end TEXT,
    duration_min INTEGER,
    city TEXT NOT NULL,
    venue TEXT NOT NULL,
    address TEXT NOT NULL,
    lat REAL,
    lon REAL,
    description TEXT NOT NULL,
    price_min INTEGER,
    price_max INTEGER,
    is_free INTEGER NOT NULL DEFAULT 0,
    age_limit INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    org_link TEXT,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'published',
    dedup_key TEXT NOT NULL,
    is_disputed INTEGER NOT NULL DEFAULT 0,
    dispute_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status_locked INTEGER NOT NULL DEFAULT 0,
    merged_into TEXT REFERENCES events(id),
    search_city TEXT NOT NULL DEFAULT '',
    search_venue TEXT NOT NULL DEFAULT '',
    search_language TEXT NOT NULL DEFAULT '',
    search_tags TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_status_date ON events(status, date_start);
CREATE INDEX IF NOT EXISTS idx_events_city ON events(city);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_dedup_key ON events(dedup_key);
CREATE INDEX IF NOT EXISTS idx_events_price ON events(price_min, is_free);

-- Отчёты об импорте (Раздел 6.1)
CREATE TABLE IF NOT EXISTS import_reports (
    id TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    actor TEXT NOT NULL,
    total_rows INTEGER NOT NULL,
    added_count INTEGER NOT NULL,
    updated_count INTEGER NOT NULL,
    unchanged_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    disputed_count INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Подписки пользователей (Раздел 6.2, 8.2, 10)
-- Персональные данные минимизированы: только идентификатор пользователя и фильтры подписки
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    city TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    period_from TEXT,
    period_to TEXT,
    CHECK (period_from IS NULL OR period_to IS NULL OR period_from <= period_to),
    UNIQUE(user_id, city, category)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);

-- Журнал изменений (Раздел 6.1, 8.2): кто, когда и что изменил
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);

-- Price nullability and data backfill are applied transactionally by migrations.py.
-- Keep legacy source columns for existing consumers.



CREATE TABLE event_sources (
    event_id TEXT NOT NULL REFERENCES events(id),
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (event_id, source_url),
    UNIQUE (source_hash, source_url)
);
CREATE INDEX idx_event_sources_hash ON event_sources(source_hash);

CREATE TABLE feedback (
    id TEXT PRIMARY KEY,
    event_id TEXT REFERENCES events(id),
    message TEXT NOT NULL CHECK (length(trim(message)) > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'resolved', 'rejected')),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_feedback_status_created ON feedback(status, created_at);

-- Authentication state, shared rate limits and normalized search fields.
CREATE TABLE admin_sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_hash TEXT NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'moderator')),
    credential_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX idx_admin_sessions_expiry ON admin_sessions(expires_at);

CREATE TABLE request_limits (
    key_hash TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL
);

CREATE INDEX idx_events_public_city ON events(status, search_city, date_start)
    WHERE merged_into IS NULL;
CREATE INDEX idx_events_public_category ON events(status, category, date_start)
    WHERE merged_into IS NULL;
CREATE INDEX idx_events_public_price ON events(status, is_free, price_min, date_start)
    WHERE merged_into IS NULL;
CREATE INDEX idx_events_public_age ON events(status, age_limit, date_start)
    WHERE merged_into IS NULL;
CREATE INDEX idx_raw_events_ingested ON raw_events(ingested_at);
CREATE INDEX idx_import_reports_created ON import_reports(created_at);
CREATE INDEX idx_audit_created ON audit_log(created_at);
CREATE INDEX idx_feedback_created ON feedback(created_at);
CREATE INDEX idx_subscriptions_expiry ON subscriptions(expires_at);

CREATE TABLE IF NOT EXISTS agent_dialogue_states (
    conversation_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_dialogue_expires ON agent_dialogue_states(expires_at);

CREATE TABLE IF NOT EXISTS agent_traces (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_traces_conversation ON agent_traces(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS agent_sessions (
    token_hash TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_expires ON agent_sessions(expires_at);

CREATE TABLE IF NOT EXISTS agent_action_receipts (
    conversation_id TEXT NOT NULL,
    action_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, action_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_action_receipts_expires ON agent_action_receipts(expires_at);


-- Профиль Telegram-пользователя без контактных данных.
CREATE TABLE IF NOT EXISTS bot_profiles (
    user_id TEXT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'ru',
    name TEXT,
    city TEXT,
    favorite_categories_json TEXT NOT NULL DEFAULT '[]',
    latitude REAL,
    longitude REAL,
    onboarding_state TEXT NOT NULL DEFAULT 'language',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_profiles_city ON bot_profiles(city);

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

CREATE UNIQUE INDEX idx_events_active_dedup ON events(dedup_key) WHERE merged_into IS NULL;
