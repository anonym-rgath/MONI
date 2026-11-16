-- N8: Frist für die 2FA-Einrichtung nach Anlegen, Passwort- oder 2FA-Reset durch einen Admin; NULL = keine Frist
ALTER TABLE users ADD COLUMN enroll_until INTEGER;
