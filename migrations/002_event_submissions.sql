-- Publicly proposed events stay separate from published catalog records until review.
CREATE TABLE IF NOT EXISTS event_submissions (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_by TEXT,
    review_note TEXT,
    event_id TEXT REFERENCES events(id),
    CHECK (status IN ('pending', 'approved', 'rejected'))
);
CREATE INDEX IF NOT EXISTS idx_event_submissions_status_created
    ON event_submissions (status, created_at);
