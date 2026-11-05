CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,            -- kleingeschrieben, getrimmt
  name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
  kind TEXT NOT NULL CHECK (kind IN ('local', 'oidc')),
  password_hash TEXT,                    -- Argon2id-PHC, nur bei local
  totp_enc TEXT, totp_pending_enc TEXT,  -- SecretBox
  totp_last_step INTEGER,
  oidc_iss TEXT, oidc_sub TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER,
  created_at INTEGER NOT NULL,
  last_login INTEGER,
  UNIQUE (oidc_iss, oidc_sub)
);
CREATE TABLE recovery_codes (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash TEXT NOT NULL, used_at INTEGER, PRIMARY KEY (user_id, code_hash)) WITHOUT ROWID;
CREATE TABLE sessions (id_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK (stage IN ('mfa', 'enroll', 'full')), csrf TEXT NOT NULL,
  created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL, ip TEXT) WITHOUT ROWID;
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT, ip TEXT, details TEXT);
CREATE INDEX audit_ts ON audit_log(ts);
