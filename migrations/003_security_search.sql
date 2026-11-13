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

ALTER TABLE events ADD COLUMN search_city TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN search_venue TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN search_language TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN search_tags TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN search_text TEXT NOT NULL DEFAULT '';
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
