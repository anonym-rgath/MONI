-- Vorherige Kanäle eines Vorfalls (P5-R18) werden über event_id gesucht
CREATE INDEX alert_outbox_event_id ON alert_outbox(event_id);
