CREATE TABLE IF NOT EXISTS users (
  cid TEXT PRIMARY KEY,
  first_seen_date TEXT NOT NULL,
  last_seen_date TEXT NOT NULL,
  current_ver TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'unknown',
  first_via TEXT NOT NULL DEFAULT 'new_install',
  os TEXT NOT NULL DEFAULT 'unknown',
  shell_ver TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_events (
  day TEXT NOT NULL,
  cid TEXT NOT NULL,
  event TEXT NOT NULL,
  ver TEXT NOT NULL,
  prev_ver TEXT NOT NULL DEFAULT '',
  upgrade_via TEXT NOT NULL DEFAULT 'none',
  channel TEXT NOT NULL DEFAULT 'unknown',
  shell_ver TEXT NOT NULL DEFAULT '',
  os TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL,
  PRIMARY KEY (day, cid, event, ver)
);

CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users (last_seen_date);
CREATE INDEX IF NOT EXISTS idx_users_current_ver ON users (current_ver);
CREATE INDEX IF NOT EXISTS idx_events_day_event ON daily_events (day, event, upgrade_via);
CREATE INDEX IF NOT EXISTS idx_events_ver_event ON daily_events (ver, event, upgrade_via);
