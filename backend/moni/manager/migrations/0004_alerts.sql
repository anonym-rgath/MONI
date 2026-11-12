-- Paket 5: Alarmierung (Regeln, Instanzen, Verlauf, Kanäle, Outbox) und Stummschaltung je Server.
ALTER TABLE servers ADD COLUMN muted_until INTEGER;   -- Unix-Sekunden oder NULL

CREATE TABLE alert_rules (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, metric TEXT NOT NULL,
  func TEXT NOT NULL DEFAULT 'value' CHECK (func IN ('value','increase')), window_seconds INTEGER,
  op TEXT NOT NULL CHECK (op IN ('>','>=','<','<=')), warning REAL, critical REAL,
  for_seconds INTEGER NOT NULL DEFAULT 0, key_labels TEXT NOT NULL DEFAULT '[]', label_filter TEXT NOT NULL DEFAULT '{}',
  require_prior_ok INTEGER NOT NULL DEFAULT 0, server_id INTEGER REFERENCES servers(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  CHECK (warning IS NOT NULL OR critical IS NOT NULL));

CREATE TABLE alert_instances (
  rule_id INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  key TEXT NOT NULL,                    -- kanonisches JSON der key_labels-Werte
  state TEXT NOT NULL CHECK (state IN ('ok','pending','firing')), severity TEXT, value REAL,
  pending_since INTEGER, fired_at INTEGER, last_notified INTEGER, seen_ok INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (rule_id, server_id, key)) WITHOUT ROWID;

CREATE TABLE alert_events (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, rule_id INTEGER, rule_name TEXT NOT NULL,
  server_id INTEGER, server_name TEXT NOT NULL, key TEXT NOT NULL, event TEXT NOT NULL
  CHECK (event IN ('firing','resolved','changed','reminder')), severity TEXT, value REAL, threshold REAL,
  reason TEXT, muted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX alert_events_ts ON alert_events(ts);

CREATE TABLE alert_channels (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN ('webhook','email')),
  config TEXT NOT NULL,                 -- JSON ohne Geheimnisse
  secret_enc TEXT,                      -- SecretBox: JSON {"url"?, "hmac"?, "password"?}
  min_severity TEXT NOT NULL DEFAULT 'warning' CHECK (min_severity IN ('warning','critical')),
  enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);

CREATE TABLE alert_outbox (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES alert_events(id) ON DELETE CASCADE,
  channel_id INTEGER NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
  attempts INTEGER NOT NULL DEFAULT 0, next_try INTEGER NOT NULL, last_error TEXT, created_at INTEGER NOT NULL);
CREATE INDEX alert_outbox_due ON alert_outbox(status, next_try);

INSERT INTO alert_rules (name, metric, func, window_seconds, op, warning, critical, for_seconds, key_labels, require_prior_ok, created_at, updated_at)
SELECT name, metric, func, window_seconds, op, warning, critical, for_seconds, key_labels, require_prior_ok, strftime('%s','now'), strftime('%s','now')
FROM (
  SELECT 'Server nicht erreichbar' AS name, 'moni_server_up' AS metric, 'value' AS func, NULL AS window_seconds, '<' AS op, NULL AS warning, 1 AS critical, 0 AS for_seconds, '[]' AS key_labels, 0 AS require_prior_ok
  UNION ALL SELECT 'CPU-Auslastung', 'moni_host_cpu_usage_percent', 'value', NULL, '>', 75, 85, 300, '[]', 0
  UNION ALL SELECT 'Arbeitsspeicher', 'moni_host_memory_usage_percent', 'value', NULL, '>', 75, 85, 300, '[]', 0
  UNION ALL SELECT 'Dateisystem voll', 'moni_filesystem_usage_percent', 'value', NULL, '>', 75, 85, 300, '["mountpoint"]', 0
  UNION ALL SELECT 'Temperatur', 'moni_host_temperature_celsius', 'value', NULL, '>', 65, 75, 120, '["zone","id"]', 0
  UNION ALL SELECT 'Container gestoppt', 'moni_container_up', 'value', NULL, '<', NULL, 1, 60, '["name"]', 1
  UNION ALL SELECT 'Container unhealthy', 'moni_container_healthy', 'value', NULL, '<', 1, NULL, 120, '["name"]', 0
  UNION ALL SELECT 'Restart-Schleife', 'moni_container_restarts', 'increase', 600, '>=', NULL, 3, 0, '["name"]', 0
  UNION ALL SELECT 'Docker-API nicht erreichbar', 'moni_docker_up', 'value', NULL, '<', NULL, 1, 60, '[]', 0
);
