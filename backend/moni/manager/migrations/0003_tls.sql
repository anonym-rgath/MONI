-- Paket 4: mTLS-Kopplung. Bestehende Server bleiben im Token-Modus.
ALTER TABLE servers ADD COLUMN security TEXT NOT NULL DEFAULT 'token' CHECK (security IN ('token','mtls'));
ALTER TABLE servers ADD COLUMN cert_not_after INTEGER;     -- Unix-Sekunden, Ablauf des Agent-Zertifikats
ALTER TABLE servers ADD COLUMN cert_renew_tried INTEGER;   -- Unix-Sekunden des letzten Erneuerungsversuchs
