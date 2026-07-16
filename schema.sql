-- =============================================================================
-- Food Database Schema (v2)
--
-- Dataset snapshot:
--   451 restaurants (Dhanmondi, Gulshan, Uttara - Dhaka)
--   16,918 products (7 non-food items dropped from the raw 16,925 scraped)
--   100% food_type coverage (0 unmatched)
--   Combo/Set-Menu items are standalone products (food_type = 'Set Menu',
--   sub_type = primary component); the mined multi-component breakdown
--   (contains_food_types) is intentionally NOT imported - owner's call,
--   keep the schema simple, a Set Menu row is its name/price/primary type.
--
-- Target: PostgreSQL 14+
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- fuzzy/substring name search (dish search is the core feature)
-- PostGIS ("near me" geo) is NOT required by this core schema and lives in the
-- optional add-on schema_geo.sql (adds the extension, a generated `geog`
-- column, and its GiST index). This keeps schema.sql portable to Postgres
-- hosts without PostGIS (e.g. default Railway); apply schema_geo.sql only on a
-- PostGIS-enabled DB when building the near-me feature. Raw latitude/longitude
-- stay here for display/export regardless.

-- ---------------------------------------------------------------------------
-- Lookup / reference tables (optional normalization for filtering & analytics)
-- ---------------------------------------------------------------------------

CREATE TABLE cuisines (
    id          SMALLSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE flavor_tags (
    id          SMALLSERIAL PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,   -- e.g. cheesy, smoky_bbq
    label       TEXT NOT NULL
);

CREATE TABLE food_categories (
    id          SMALLSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE    -- normalized category from JSON "category" field
);

CREATE TABLE food_types (
    id          SMALLSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE    -- e.g. Fries, Bread, Set Menu (standalone listing)
);

CREATE TABLE food_sub_types (
    id          SMALLSERIAL PRIMARY KEY,
    food_type_id SMALLINT NOT NULL REFERENCES food_types(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    UNIQUE (food_type_id, name)
);

-- ---------------------------------------------------------------------------
-- Canonical dishes - the cross-restaurant COMPARISON identity, distinct from
-- food_type/sub_type (which is a small, deliberately-coarse browsing
-- classification). Bootstrapped by normalizing product names, grouping by
-- (food_type, normalized_name) - sub_type is deliberately NOT part of the key
-- because the per-product classifier disagrees on sub_type for the same dish
-- (e.g. Beef Tehari tagged Tehari at one restaurant, Biryani at another) and
-- would fragment the group - and promoting a group to a canonical dish only
-- when it spans 2+ DIFFERENT restaurants - a name that
-- only ever appears at one restaurant isn't proof of being a shared dish,
-- so it stays unlinked (products.canonical_dish_id nullable) and is still
-- perfectly browsable via food_type, just not "compared."
--
-- This is a name-normalization match only (strips prefixes/sizes/
-- punctuation) - it does NOT unify genuine spelling variants (Biryani vs
-- Biriyani, Sharbat vs Sherbet). That's real future work (fuzzy matching /
-- LLM-assisted), deliberately deferred - aliases[] exists so it can be
-- filled in incrementally without a schema change.
-- ---------------------------------------------------------------------------

CREATE TABLE canonical_dishes (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,              -- display name = most common raw spelling seen
    aliases         TEXT[] NOT NULL DEFAULT '{}', -- other raw spellings observed, for search
    -- Authoritative attributes = majority vote of the member products (the
    -- per-product classifier can disagree across restaurants; these give a
    -- stable value for grouping/filtering so a dish doesn't flicker in and
    -- out of a cuisine/category filter). Products keep their own raw values.
    food_type_id    SMALLINT REFERENCES food_types(id) ON DELETE SET NULL,
    food_sub_type_id SMALLINT REFERENCES food_sub_types(id) ON DELETE SET NULL,
    cuisine_id      SMALLINT REFERENCES cuisines(id) ON DELETE SET NULL,
    category_id     SMALLINT REFERENCES food_categories(id) ON DELETE SET NULL,
    image_url       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_canonical_dishes_food_type ON canonical_dishes(food_type_id);
CREATE INDEX idx_canonical_dishes_aliases ON canonical_dishes USING GIN(aliases);
CREATE INDEX idx_canonical_dishes_name_trgm ON canonical_dishes USING GIN(name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Restaurant domain
-- ---------------------------------------------------------------------------

CREATE TABLE restaurant_chains (
    id          SERIAL PRIMARY KEY,
    chain_code  TEXT NOT NULL UNIQUE,   -- source chain id, e.g. ck6kl
    name        TEXT NOT NULL
);

CREATE TABLE restaurants (
    id                      SERIAL PRIMARY KEY,
    source_restaurant_code  TEXT NOT NULL UNIQUE,   -- natural key from source, e.g. acks
    name                    TEXT NOT NULL,
    address                 TEXT,
    latitude                NUMERIC(10, 8),   -- kept raw for display / export
    longitude               NUMERIC(11, 8),
    -- The generated `geog GEOGRAPHY` column + GiST index for "near me" live in
    -- the optional schema_geo.sql (needs PostGIS). Not part of the core schema.
    rating                  NUMERIC(2, 1) CHECK (rating IS NULL OR rating BETWEEN 0 AND 5),
    review_count            INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
    old_rating              NUMERIC(2, 1) CHECK (old_rating IS NULL OR old_rating BETWEEN 0 AND 5),
    old_review_count        INTEGER CHECK (old_review_count IS NULL OR old_review_count >= 0),
    budget_tier             SMALLINT CHECK (budget_tier BETWEEN 1 AND 3),  -- 1=cheap, 3=expensive
    phone                   TEXT,
    city                    TEXT NOT NULL DEFAULT 'Dhaka',
    area                    TEXT,                   -- e.g. Dhanmondi/Gulshan/Uttara (derived from scrape batch)
    chain_id                INTEGER REFERENCES restaurant_chains(id) ON DELETE SET NULL,
    hero_image_url          TEXT,
    logo_image_url          TEXT,
    google_place_id         TEXT,           -- set by match_google_places.py pipeline
    match_status            TEXT NOT NULL DEFAULT 'unmatched'
                            CHECK (match_status IN ('unmatched', 'auto_matched', 'needs_review', 'manually_matched', 'rejected')),
                            -- Google Places matching state (not user-review state)
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_restaurants_city ON restaurants(city);
CREATE INDEX idx_restaurants_area ON restaurants(area);
CREATE INDEX idx_restaurants_rating ON restaurants(rating DESC);
-- idx_restaurants_geog (GiST on geog) lives in schema_geo.sql (needs PostGIS).
CREATE INDEX idx_restaurants_chain ON restaurants(chain_id);
CREATE INDEX idx_restaurants_name_trgm ON restaurants USING GIN(name gin_trgm_ops);

-- Many-to-many: restaurant <-> cuisine
CREATE TABLE restaurant_cuisines (
    restaurant_id   INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    cuisine_id      SMALLINT NOT NULL REFERENCES cuisines(id) ON DELETE CASCADE,
    PRIMARY KEY (restaurant_id, cuisine_id)
);

-- External / scrape metadata (maps JSON _dormant block)
CREATE TABLE restaurant_sources (
    id              SERIAL PRIMARY KEY,
    restaurant_id   INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    source_name     TEXT NOT NULL,          -- e.g. foodpanda
    source_url      TEXT,
    last_scraped_at TIMESTAMPTZ,
    raw_metadata    JSONB,                  -- preserve extra fields without schema churn
    UNIQUE (restaurant_id, source_name)
);

-- ---------------------------------------------------------------------------
-- Product / menu domain
-- ---------------------------------------------------------------------------

CREATE TABLE products (
    id                      SERIAL PRIMARY KEY,
    source_product_id       BIGINT NOT NULL UNIQUE,     -- product_id from JSON (verified globally unique in real data)
    restaurant_id           INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    description             TEXT,
    base_price_bdt          NUMERIC(10, 2) NOT NULL CHECK (base_price_bdt >= 0),
    image_url               TEXT,
    is_sold_out             BOOLEAN NOT NULL DEFAULT FALSE,
    category_id             SMALLINT REFERENCES food_categories(id) ON DELETE SET NULL,
    cuisine_id              SMALLINT REFERENCES cuisines(id) ON DELETE SET NULL,
    food_type_id            SMALLINT REFERENCES food_types(id) ON DELETE SET NULL,
    food_sub_type_id        SMALLINT REFERENCES food_sub_types(id) ON DELETE SET NULL,
    canonical_dish_id       INTEGER REFERENCES canonical_dishes(id) ON DELETE SET NULL,
    -- Match key for read-time brand grouping: the API collapses a chain's
    -- branches into one card via (chain_id, food_type_id, normalized_name).
    -- Written by load_batch using canonical_match_key(), the SAME function the
    -- canonical bootstrap groups with, so both layers agree on what "the same
    -- dish name" means (and brand dedupe inherits its spelling map).
    normalized_name         TEXT,
    -- Menu lifecycle: a re-scrape that no longer sees this item must set
    -- is_active = FALSE, never DELETE the row - product_reviews cascades on
    -- delete, so a hard delete silently destroys user reviews on a dish
    -- that's just temporarily off the menu.
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rating                  NUMERIC(2, 1) CHECK (rating IS NULL OR rating BETWEEN 0 AND 5),
    review_count            INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_restaurant ON products(restaurant_id);
CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_food_type ON products(food_type_id);
CREATE INDEX idx_products_canonical_dish ON products(canonical_dish_id);
CREATE INDEX idx_products_price ON products(base_price_bdt);
CREATE INDEX idx_products_active ON products(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_products_sold_out ON products(is_sold_out) WHERE is_sold_out = FALSE;
CREATE INDEX idx_products_rating ON products(rating DESC);
CREATE INDEX idx_products_name_trgm ON products USING GIN(name gin_trgm_ops);
CREATE INDEX idx_products_normalized_name ON products(normalized_name);  -- brand grouping

-- Size / option pricing (maps JSON variations[])
-- label defaults to 'Regular' rather than allowing NULL - Postgres treats
-- NULL as distinct-from-NULL under UNIQUE, so UNIQUE(product_id, NULL) does
-- NOT actually prevent duplicate "default option" rows. A non-null default
-- sidesteps that gotcha entirely instead of needing a partial index.
CREATE TABLE product_variations (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    label           TEXT NOT NULL DEFAULT 'Regular',
    price_bdt       NUMERIC(10, 2) NOT NULL CHECK (price_bdt >= 0),
    sort_order      SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (product_id, label)
);

CREATE INDEX idx_product_variations_product ON product_variations(product_id);

-- Many-to-many: product <-> flavor tag
CREATE TABLE product_flavor_tags (
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    flavor_tag_id   SMALLINT NOT NULL REFERENCES flavor_tags(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, flavor_tag_id)
);

-- ---------------------------------------------------------------------------
-- Users & reviews
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    email           TEXT UNIQUE,
    phone           TEXT UNIQUE,
    password_hash   TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (email IS NOT NULL OR phone IS NOT NULL)
);

CREATE TABLE restaurant_reviews (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    restaurant_id       INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    rating              SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body                TEXT,                   -- optional written review
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
    is_verified_visit   BOOLEAN NOT NULL DEFAULT FALSE,
    helpful_count       INTEGER NOT NULL DEFAULT 0 CHECK (helpful_count >= 0),
    not_helpful_count   INTEGER NOT NULL DEFAULT 0 CHECK (not_helpful_count >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, restaurant_id)           -- one review per user; edits update this row
);

CREATE INDEX idx_restaurant_reviews_restaurant ON restaurant_reviews(restaurant_id);
CREATE INDEX idx_restaurant_reviews_user ON restaurant_reviews(user_id);
CREATE INDEX idx_restaurant_reviews_status ON restaurant_reviews(status);

CREATE TABLE restaurant_review_photos (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES restaurant_reviews(id) ON DELETE CASCADE,
    image_url       TEXT NOT NULL,
    sort_order      SMALLINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_restaurant_review_photos_review ON restaurant_review_photos(review_id);

CREATE TABLE restaurant_review_votes (
    review_id       INTEGER NOT NULL REFERENCES restaurant_reviews(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_helpful      BOOLEAN NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (review_id, user_id)
);

CREATE TABLE product_reviews (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    rating              SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body                TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
    is_verified_order   BOOLEAN NOT NULL DEFAULT FALSE,
    helpful_count       INTEGER NOT NULL DEFAULT 0 CHECK (helpful_count >= 0),
    not_helpful_count   INTEGER NOT NULL DEFAULT 0 CHECK (not_helpful_count >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, product_id)
);

CREATE INDEX idx_product_reviews_product ON product_reviews(product_id);
CREATE INDEX idx_product_reviews_user ON product_reviews(user_id);
CREATE INDEX idx_product_reviews_status ON product_reviews(status);

CREATE TABLE product_review_photos (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES product_reviews(id) ON DELETE CASCADE,
    image_url       TEXT NOT NULL,
    sort_order      SMALLINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_product_review_photos_review ON product_review_photos(review_id);

CREATE TABLE product_review_votes (
    review_id       INTEGER NOT NULL REFERENCES product_reviews(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_helpful      BOOLEAN NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (review_id, user_id)
);

-- restaurants.rating / review_count and products.rating / review_count
-- are app aggregates from approved user reviews (updated by application logic).

-- ---------------------------------------------------------------------------
-- Convenience views
-- ---------------------------------------------------------------------------

CREATE VIEW v_restaurant_summary AS
SELECT
    r.id,
    r.source_restaurant_code,
    r.name,
    r.city,
    r.area,
    r.rating,
    r.review_count,
    r.old_rating,
    r.old_review_count,
    r.budget_tier,
    rc.name AS chain_name,
    ARRAY_AGG(DISTINCT c.name ORDER BY c.name) FILTER (WHERE c.name IS NOT NULL) AS cuisines,
    COUNT(DISTINCT p.id) FILTER (WHERE p.is_active) AS product_count,
    MIN(p.base_price_bdt) FILTER (WHERE p.is_active) AS min_price_bdt,
    MAX(p.base_price_bdt) FILTER (WHERE p.is_active) AS max_price_bdt
FROM restaurants r
LEFT JOIN restaurant_chains rc ON rc.id = r.chain_id
LEFT JOIN restaurant_cuisines rcs ON rcs.restaurant_id = r.id
LEFT JOIN cuisines c ON c.id = rcs.cuisine_id
LEFT JOIN products p ON p.restaurant_id = r.id
GROUP BY r.id, rc.name;

CREATE VIEW v_product_detail AS
SELECT
    p.id,
    p.source_product_id,
    p.name,
    p.description,
    p.base_price_bdt,
    p.is_sold_out,
    p.is_active,
    p.rating,
    p.review_count,
    fc.name AS category,
    ft.name AS food_type,
    fst.name AS food_sub_type,
    cu.name AS cuisine,
    cd.id AS canonical_dish_id,
    cd.name AS canonical_dish_name,
    r.source_restaurant_code,
    r.name AS restaurant_name,
    ARRAY_AGG(DISTINCT fl.slug ORDER BY fl.slug) FILTER (WHERE fl.slug IS NOT NULL) AS flavor_tags
FROM products p
JOIN restaurants r ON r.id = p.restaurant_id
LEFT JOIN food_categories fc ON fc.id = p.category_id
LEFT JOIN food_types ft ON ft.id = p.food_type_id
LEFT JOIN food_sub_types fst ON fst.id = p.food_sub_type_id
LEFT JOIN cuisines cu ON cu.id = p.cuisine_id
LEFT JOIN canonical_dishes cd ON cd.id = p.canonical_dish_id
LEFT JOIN product_flavor_tags pft ON pft.product_id = p.id
LEFT JOIN flavor_tags fl ON fl.id = pft.flavor_tag_id
GROUP BY
    p.id, p.source_product_id, p.name, p.description, p.base_price_bdt, p.is_sold_out, p.is_active,
    p.rating, p.review_count,
    fc.name, ft.name, fst.name, cu.name, cd.id, cd.name,
    r.source_restaurant_code, r.name;

-- Cross-restaurant comparison - the core "search a dish, compare it" query.
-- Only canonical_dish_id is required; everything else is presentation.
CREATE VIEW v_canonical_dish_comparison AS
SELECT
    cd.id AS canonical_dish_id,
    cd.name AS dish_name,
    p.id AS product_id,
    p.base_price_bdt,
    p.rating,
    p.review_count,
    p.is_sold_out,
    r.id AS restaurant_id,
    r.name AS restaurant_name,
    r.area,
    r.rating AS restaurant_rating
FROM canonical_dishes cd
JOIN products p ON p.canonical_dish_id = cd.id AND p.is_active = TRUE
JOIN restaurants r ON r.id = p.restaurant_id AND r.is_active = TRUE;

COMMIT;
