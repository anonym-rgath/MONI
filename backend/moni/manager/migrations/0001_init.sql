CREATE TABLE servers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  url TEXT,                      -- NULL beim lokalen Server
  is_local INTEGER NOT NULL DEFAULT 0,
  token_enc TEXT,                -- SecretBox-Token
  headers_enc TEXT,              -- SecretBox-Token eines JSON-Objekts
  header_names TEXT NOT NULL DEFAULT '[]',  -- JSON-Liste, nur Namen (für die API)
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',   -- unknown|ok|unreachable|error
  failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_seen INTEGER,             -- Unix-Sekunden des letzten erfolgreichen Abrufs
  last_ts INTEGER,               -- Zeitstempel des jüngsten gespeicherten Punkts
  last_snapshot TEXT,            -- JSON des jüngsten Snapshots
  agent_info TEXT,               -- JSON von /api/v1/info
  created_at INTEGER NOT NULL
);
CREATE TABLE series (
  id INTEGER PRIMARY KEY,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  labels TEXT NOT NULL,          -- kanonisches JSON: Liste von [key, value]
  UNIQUE (server_id, name, labels)
);
CREATE TABLE samples_raw (series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, value REAL NOT NULL, PRIMARY KEY (series_id, ts)) WITHOUT ROWID;
CREATE TABLE samples_1m (series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, min REAL NOT NULL, avg REAL NOT NULL, max REAL NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)) WITHOUT ROWID;
CREATE TABLE samples_1h (series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, min REAL NOT NULL, avg REAL NOT NULL, max REAL NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)) WITHOUT ROWID;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
