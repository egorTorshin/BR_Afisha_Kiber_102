-- Price nullability and data backfill are applied transactionally by migrations.py.
-- Keep legacy source columns for existing consumers.
ALTER TABLE events ADD COLUMN status_locked INTEGER NOT NULL DEFAULT 0;
ALTER TABLE events ADD COLUMN merged_into TEXT REFERENCES events(id);

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
