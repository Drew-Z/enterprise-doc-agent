CREATE TABLE IF NOT EXISTS monitor_state (
  monitor_key TEXT PRIMARY KEY,
  last_tick INTEGER NOT NULL DEFAULT -1,
  observed_at INTEGER,
  failures INTEGER NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 3),
  successes INTEGER NOT NULL DEFAULT 0 CHECK (successes BETWEEN 0 AND 2),
  incident_open INTEGER NOT NULL DEFAULT 0 CHECK (incident_open IN (0, 1)),
  reason TEXT NOT NULL DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS notification_events (
  id TEXT PRIMARY KEY,
  monitor_key TEXT NOT NULL REFERENCES monitor_state(monitor_key),
  tick INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('failure', 'recovery')),
  reason TEXT NOT NULL,
  delivery_status TEXT NOT NULL CHECK (delivery_status IN ('pending', 'suppressed', 'attempting', 'accepted', 'unknown')),
  attempted_at INTEGER,
  completed_at INTEGER,
  provider_message_id TEXT,
  UNIQUE (monitor_key, tick)
);
CREATE INDEX IF NOT EXISTS notification_pending ON notification_events (monitor_key, delivery_status, tick);
CREATE TABLE IF NOT EXISTS external_heartbeats (
  source_key TEXT PRIMARY KEY,
  source_at INTEGER NOT NULL CHECK (source_at >= 0),
  observed_at INTEGER NOT NULL CHECK (observed_at >= 0),
  healthy INTEGER NOT NULL CHECK (healthy IN (0, 1)),
  artifact_sha256 TEXT
);
