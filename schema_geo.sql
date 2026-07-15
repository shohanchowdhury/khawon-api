-- =============================================================================
-- Optional geo add-on for "near me" (radius / nearest-restaurant) queries.
--
-- Apply this ONLY on a PostGIS-enabled Postgres, AFTER schema.sql. It adds a
-- generated GEOGRAPHY column derived from restaurants.latitude/longitude plus a
-- GiST index. The core schema (schema.sql) is fully functional without it -
-- everything except geo distance runs on plain Postgres (e.g. default Railway,
-- which has no PostGIS).
--
-- Idempotent-ish: uses IF NOT EXISTS so a re-run is a no-op. To use near-me:
--   ST_DWithin(geog, ST_MakePoint(:lng, :lat)::geography, 2000)  -- 2km radius
--   ORDER BY geog <-> ST_MakePoint(:lng, :lat)::geography         -- nearest first
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

-- Generated geography point (auto-derived from lat/long, always in sync).
ALTER TABLE restaurants
    ADD COLUMN IF NOT EXISTS geog GEOGRAPHY(Point, 4326)
    GENERATED ALWAYS AS (
        ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
    ) STORED;

-- Radius / nearest queries. The plain lat/long btree can't serve these.
CREATE INDEX IF NOT EXISTS idx_restaurants_geog ON restaurants USING GIST(geog);

COMMIT;
