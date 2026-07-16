-- Adds products.normalized_name (read-time brand grouping key).
-- schema.sql already contains this column for fresh databases; this file
-- brings an EXISTING database up to date. Idempotent - safe to re-run.
-- Apply:  psql "$DATABASE_PUBLIC_URL" -f migrations/001_products_normalized_name.sql
-- Then re-run load_batch.py to populate the column.

BEGIN;

ALTER TABLE products ADD COLUMN IF NOT EXISTS normalized_name TEXT;
CREATE INDEX IF NOT EXISTS idx_products_normalized_name ON products(normalized_name);

COMMIT;
