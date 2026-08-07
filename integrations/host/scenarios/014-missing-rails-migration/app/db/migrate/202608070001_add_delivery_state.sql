BEGIN;

ALTER TABLE checkout_confirmations
  ADD COLUMN IF NOT EXISTS delivery_state text;

UPDATE checkout_confirmations
SET delivery_state = 'confirmed'
WHERE delivery_state IS NULL;

ALTER TABLE checkout_confirmations
  ALTER COLUMN delivery_state SET DEFAULT 'confirmed',
  ALTER COLUMN delivery_state SET NOT NULL;

INSERT INTO schema_migrations (version)
VALUES ('202608070001')
ON CONFLICT (version) DO NOTHING;

COMMIT;
