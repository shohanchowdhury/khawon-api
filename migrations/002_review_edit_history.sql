-- 002: edit history for both review stacks.
--
-- Reviews are editable and UNIQUE (user_id, target), so an edit overwrote the
-- previous rating/body in place and it was gone. A review quietly rewritten
-- after the fact is exactly the pattern that erodes trust in ratings, so the
-- superseded version is now kept.
--
-- Each *_review_edits row holds a PREVIOUS version -- the values as they stood
-- until `superseded_at`. The live review row is always the current version, so
-- every existing read (ratings, pooling, listings) is unaffected.
--
-- Capture is by TRIGGER, not application code: there are already two write
-- paths (routers/reviews.py, routers/restaurants.py) and moderation will add
-- more. A trigger cannot be forgotten and also covers raw SQL updates.
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS restaurant_review_edits (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES restaurant_reviews(id) ON DELETE CASCADE,
    rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body            TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    superseded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_restaurant_review_edits_review
    ON restaurant_review_edits(review_id, superseded_at);

CREATE TABLE IF NOT EXISTS product_review_edits (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES product_reviews(id) ON DELETE CASCADE,
    rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body            TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    superseded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_review_edits_review
    ON product_review_edits(review_id, superseded_at);

-- Only rating/body/status count as an edit. Vote counters (helpful_count etc.)
-- fire UPDATE constantly and must NOT create history rows -- IS DISTINCT FROM
-- also handles NULL bodies, which plain <> would not.

CREATE OR REPLACE FUNCTION log_restaurant_review_edit() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.rating IS DISTINCT FROM NEW.rating
       OR OLD.body IS DISTINCT FROM NEW.body
       OR OLD.status IS DISTINCT FROM NEW.status
    THEN
        INSERT INTO restaurant_review_edits (review_id, rating, body, status, superseded_at)
        VALUES (OLD.id, OLD.rating, OLD.body, OLD.status, NOW());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION log_product_review_edit() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.rating IS DISTINCT FROM NEW.rating
       OR OLD.body IS DISTINCT FROM NEW.body
       OR OLD.status IS DISTINCT FROM NEW.status
    THEN
        INSERT INTO product_review_edits (review_id, rating, body, status, superseded_at)
        VALUES (OLD.id, OLD.rating, OLD.body, OLD.status, NOW());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_restaurant_review_edit ON restaurant_reviews;
CREATE TRIGGER trg_restaurant_review_edit
    AFTER UPDATE ON restaurant_reviews
    FOR EACH ROW EXECUTE FUNCTION log_restaurant_review_edit();

DROP TRIGGER IF EXISTS trg_product_review_edit ON product_reviews;
CREATE TRIGGER trg_product_review_edit
    AFTER UPDATE ON product_reviews
    FOR EACH ROW EXECUTE FUNCTION log_product_review_edit();

COMMIT;
