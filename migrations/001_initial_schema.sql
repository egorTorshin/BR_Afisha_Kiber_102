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
    price_min INTEGER NOT NULL DEFAULT 0,
    price_max INTEGER NOT NULL DEFAULT 0,
    is_free INTEGER NOT NULL DEFAULT 0,
    age_limit INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    org_link TEXT,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    verified_at TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'опубликовано',
    dedup_key TEXT NOT NULL,
    is_disputed INTEGER NOT NULL DEFAULT 0,
    dispute_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
