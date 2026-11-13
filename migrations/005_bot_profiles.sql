-- Профиль Telegram-пользователя. Контакт и координаты сохраняются только после
-- явной передачи пользователем в Telegram.
CREATE TABLE IF NOT EXISTS bot_profiles (
    user_id TEXT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'ru',
    name TEXT,
    phone TEXT,
    city TEXT,
    favorite_categories_json TEXT NOT NULL DEFAULT '[]',
    latitude REAL,
    longitude REAL,
    onboarding_state TEXT NOT NULL DEFAULT 'language',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_profiles_city ON bot_profiles(city);
