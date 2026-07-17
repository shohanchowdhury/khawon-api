# Khawon Backend — Database, Schema & Architecture Reference

**Written:** 2026-07-17 · **Audience:** co-creators who already know the product idea.
**What this is:** the complete technical explanation of how the backend is structured — every table, every column, every API type, and the reasoning behind the design. Everything below was verified against the source code and the live Railway database on the date above, not written from memory.

**Companion docs:**
- [`HANDOFF.md`](HANDOFF.md) — the operational handoff: how to run things, the 7 debugging traps (§9 there — read them before touching `load_batch.py`), pipeline re-run commands.
- [`docs/superpowers/specs/2026-07-15-chain-brand-model-design.md`](superpowers/specs/2026-07-15-chain-brand-model-design.md) — the brand-model design decisions (D1–D14) with the data that motivated each one.

---

## Table of contents

1. [The system at a glance](#1-the-system-at-a-glance)
2. [Core concepts & vocabulary](#2-core-concepts--vocabulary)
3. [Design principles — why it's built this way](#3-design-principles)
4. [The database — all 20 tables](#4-the-database)
5. [The Python layer — `database.py`, `models.py`, `schemas.py`](#5-the-python-layer)
6. [How data gets IN — the pipeline](#6-how-data-gets-in--the-pipeline)
7. [How data gets OUT — read-time assembly](#7-how-data-gets-out--read-time-assembly)
8. [API endpoint reference](#8-api-endpoint-reference)
9. [Auth](#9-auth)
10. [External services & environment variables](#10-external-services--environment-variables)
11. [Testing](#11-testing)
12. [Known gaps & deferred work](#12-known-gaps--deferred-work)

---

## 1. The system at a glance

```
  foodpanda scrape (one-time / re-runnable)
        │
        ▼
  ┌────────────── OFFLINE PIPELINE (deterministic, re-runnable) ──────────────┐
  │ strip_restaurants.py   raw scrape → restaurants_<area>_partN.json         │
  │ classify_batch.py      → *_products.json + *_restaurants.json (taxonomy)  │
  │ consolidate_variants.py→ consolidated.json  (size-row merge)              │
  │ bootstrap_chains.py    → chains.json        (BRAND identity)              │
  │ bootstrap_canonical_dishes.py → canonical_dishes.json (COMPARE layer)     │
  └──────────────────────────────┬────────────────────────────────────────────┘
                                 │  load_batch.py (idempotent upsert)
                                 ▼
                     PostgreSQL 18 on Railway
                     (schema.sql = source of truth; pg_trgm; no PostGIS)
                                 │
                                 ▼
                     FastAPI  (main.py + routers/*)
                     — all aggregation happens at READ time —
                                 ▼
                     React 19 + Vite  (khawon-web, mirrors schemas.py in TS)
```

The database is a **derived artifact**: everything except users and reviews can be rebuilt from the pipeline's JSON outputs. That single fact explains most of the design below.

---

## 2. Core concepts & vocabulary

Five nouns cover the whole data model. Get these straight and everything else reads cleanly.

| Term | DB table | What it is |
|---|---|---|
| **Brand** | `restaurant_chains` | The identity a diner recognizes: *Domino's Pizza*, *Bella Italia*. **In the API, "restaurant" means brand.** |
| **Branch** (location) | `restaurants` | One physical outlet: *Domino's Dhanmondi*. Has coords, address, its own foodpanda rating. |
| **Product** | `products` | One menu item **at one branch**: *Margherita at Domino's Dhanmondi*. |
| **Variation** | `product_variations` | A size/option price point on one product: *Margherita — Large — 649tk*. |
| **Canonical dish** | `canonical_dishes` | A cross-brand comparison identity: *"Chicken Biryani" as served by 9 different brands*. |

And one derived (not stored) concept:

| Term | Built by | What it is |
|---|---|---|
| **Brand dish card** | `brand_dishes.py`, at read time | A brand's branches collapsed into one menu entry: *Margherita at Domino's, 199–348tk, at 3 of 3 branches*. There is deliberately **no table** for this — see §3.3. |

### 2.1 The two-layer model

Two grouping layers that look similar but do different jobs. They **stack**:

| Layer | Job | Scope | Example |
|---|---|---|---|
| **Chain grouping** (brand cards) | Dedupe *within* one brand | intra-brand | Domino's Margherita at 3 branches → **1 card** |
| **Canonical dish** | Compare *across* brands | inter-brand | Chicken Biryani at **9 different brands** |

They were originally conflated: canonical promotion counted *branches*, so a chain-exclusive item sold at 3 branches of one chain looked "comparable across restaurants" when nobody else makes it. Fixing promotion to count **brands** dropped canonical dishes 2,527 → 1,431. Those 1,096 dishes weren't lost — they're the chain layer's job and are still fully searchable; they just stopped pretending to be comparisons.

### 2.2 The load-bearing invariant: every restaurant is a brand

Every `restaurants` row has a `chain_id` — no exceptions. A standalone restaurant is *a brand of one* (`branch_count == 1`). Live numbers: **451 branches → 378 brands** (53 multi-location, 325 solo).

Why this matters: every query in the codebase is a plain `GROUP BY chain_id`. There is **zero** `if chain else standalone` branching anywhere — a standalone restaurant's brand card, brand page, and menu are shape-identical to a chain's. When you add a feature, keep it that way.

---

## 3. Design principles

These five principles explain nearly every structural choice. When you're unsure how to build something new, check it against these.

### 3.1 SQL-first: `schema.sql` is the source of truth, not the ORM

The schema uses Postgres-native features the ORM can't express: trigram (`pg_trgm`) GIN indexes for fuzzy search, `TEXT[]` alias arrays with GIN indexes, partial indexes, a generated `GEOGRAPHY` column in the geo add-on. So:

- `schema.sql` **creates** the database. `models.py` is a thin read/write *mapping* over tables that already exist.
- `main.py` must **never** call `create_all()` or run ad-hoc migrations. (It used to; that was removed deliberately.)
- Schema changes go in **both** `schema.sql` (fresh DBs) **and** a numbered file in `migrations/` (existing DBs). See §4.10.

### 3.2 The pipeline owns catalogue data; the DB is derived

Curated data (taxonomy rules, spelling maps, brand overrides) lives in **pipeline code**, never as hand-edited DB rows. The DB gets reloaded from pipeline output; anything typed directly into the database is overwritten or orphaned on the next load. If you want to fix a wrong classification, fix it in `classify_batch.py`'s rules and re-run — don't `UPDATE` the row.

The exceptions — data the pipeline must never clobber — are `users`, both review stacks, and user-contributed branches (created via `POST /branches/`, recognizable by `source_restaurant_code` starting with `user-`).

### 3.3 Aggregate at read time; store no derived state

Brand cards, pooled ratings, price ranges, availability badges, food-type stats — all computed per request from base rows. Nothing is denormalized. This is why:

- There is **no `chain_dishes` table**. The card is a grouping over `products` rows, which must exist anyway (per-branch reviews, availability, future map pins).
- `products.rating`/`review_count` and `restaurants.rating`/`review_count` columns exist but are **reserved and NOT maintained**. Don't read them; compute from approved reviews instead. They're there so that *if* review volume ever makes live aggregation too slow, denormalization is a migration away, not a redesign.

The payoff: correctness by construction. There is no cache to invalidate, no stale aggregate, no "rebuild the rollup" job. At 16k products and pre-launch traffic this is comfortably fast; revisit only with evidence.

### 3.4 Natural keys in URLs; serial ids never leave POST bodies

Pipeline reloads churn serial ids (canonical dishes are fully rebuilt each load; products/restaurants keep ids only because they upsert). Two serial sequences (`restaurants.id`, `restaurant_chains.id`) also **overlap numerically** — a numeric `/restaurants/296` once silently served the *wrong restaurant* with no 404. So:

| Key | Used for |
|---|---|
| `chain_code` (brand **slug**, e.g. `bella-italia`) | every `/restaurants/*` URL |
| `(food_type_id, dish_slug)` | brand-dish URLs |
| `source_product_id`, `source_restaurant_code` | pipeline upsert identity |
| serial `id`s | POST bodies (`branch_id`, `dish_id`) and internal FKs only |

### 3.5 Soft delete for products; union-with-badge for availability

- A re-scrape that no longer sees a product sets `is_active = FALSE`, **never** `DELETE` — `product_reviews` cascades on delete, so a hard delete would silently destroy user reviews of a dish that's merely off the menu this week.
- A brand's menu is the **union** of its branches' menus, badged "at 2 of 3 branches". Intersection would silently hide ~⅓ of a chain's menu; union without the badge would send someone across town for a dish their branch doesn't serve.

---

## 4. The database

**20 tables + 3 views.** PostgreSQL 18 on Railway. Extension: `pg_trgm`. PostGIS is **not** available on Railway — geo lives in an optional add-on (§4.10).

### 4.0 Live row counts (verified 2026-07-17 against Railway)

| Table | Rows | Table | Rows |
|---|---:|---|---:|
| `restaurants` | 451 | `cuisines` | 11 |
| `restaurant_chains` | 378 | `food_categories` | 6 |
| `products` | 16,402 (16,385 active) | `flavor_tags` | 9 |
| `product_variations` | 20,643 | `product_flavor_tags` | 14,857 |
| `canonical_dishes` | 1,431 | `restaurant_cuisines` | 409 |
| `food_types` | 28 | `restaurant_sources` | 0 *(defined, unused)* |
| `food_sub_types` | 111 | `users` + all 6 review tables | 0 *(pre-launch)* |

Derived at read time: **13,653 brand-dish cards** from 16,385 active products — ~17% of the raw catalogue was chain duplication, now collapsed per request.

### 4.1 Conventions (read once, apply everywhere)

- **`id`** — `SERIAL` (or `SMALLSERIAL` for small lookups) primary key. Internal only; churns on reload for rebuilt tables (§3.4).
- **Natural keys** — `UNIQUE` columns from the source data (`source_product_id`, `source_restaurant_code`, `chain_code`). These are the upsert identity and are stable across reloads.
- **`created_at` / `updated_at`** — `TIMESTAMPTZ DEFAULT NOW()`; `updated_at` is maintained by the ORM (`onupdate`), not a DB trigger — raw-SQL writes won't touch it.
- **`status`** on reviews — `pending | approved | rejected` (CHECK constraint). All public reads filter `approved`; post-moderation inserts `approved` directly (§4.7).
- **Lookup FKs** — `ON DELETE SET NULL` for classification (losing a food type shouldn't delete products); `ON DELETE CASCADE` for ownership (deleting a product deletes its variations and reviews).
- **Money** — `NUMERIC(10,2)`, BDT, column names end `_bdt`.

### 4.2 Taxonomy & lookup tables

The browsing taxonomy has **four independent dimensions**. Every product carries a nullable single FK to each — deliberately single-valued, not many-to-many, so classification is one decision per dimension per product:

1. **Category** (`food_categories`, 6) — meal role: Breakfast / Main Dish / Appetizer / Sides / Dessert / Drinks.
2. **Food Type** (`food_types`, 28) — deliberately **coarse** browsing umbrellas: Rice, Curry, Pizza, Burger, Beverages, Set Menu… Coarse is a feature: 28 tiles fit on a browse screen; 300 wouldn't.
3. **Food Sub Type** (`food_sub_types`, 111) — the *natural differentiator within each type*, which differs per type: protein for Curry, format for Rice (Biryani / Fried Rice / Tehari), preparation for Dumpling (Fried / Steamed), drink kind for Beverages.
4. **Cuisine** (`cuisines`, 11) — Bangladeshi / Indian / Chinese / Italian…; "Asian" is a catch-all only.

The classifier's key rule (**"format wins over garnish"**): *Tandoori Chicken Pizza* → Pizza, not Grill; *Chicken Seekh Kabab Roll* → Wraps & Rolls, not Kebab. Implemented by **rule order** (first match wins) in `classify_batch.py`'s `FOOD_TYPE_RULES`. **Set Menu** is its own food type; its `sub_type` is the primary component.

#### `cuisines`
| Column | Type | Notes |
|---|---|---|
| `id` | SMALLSERIAL PK | |
| `name` | TEXT NOT NULL UNIQUE | e.g. `Bangladeshi`. Rows created on demand by `load_batch` from pipeline output. |

#### `food_categories`
| Column | Type | Notes |
|---|---|---|
| `id` | SMALLSERIAL PK | |
| `name` | TEXT NOT NULL UNIQUE | Meal role (6 values). Created on demand by `load_batch`. |

#### `food_types`
| Column | Type | Notes |
|---|---|---|
| `id` | SMALLSERIAL PK | Part of the brand-card grouping key and brand-dish URLs. |
| `name` | TEXT NOT NULL UNIQUE | e.g. `Pizza`, `Set Menu`. |

Deliberately a **bare** `(id, name)` lookup. The v1 schema had image/description/parent columns; they were dropped when the "rich browsable entity" role moved to `canonical_dishes`. The API still *accepts* description/image on food-type create/update for backward compatibility but does not persist them, and the photo endpoint returns **501** (see §8, food-types).

#### `food_sub_types`
| Column | Type | Notes |
|---|---|---|
| `id` | SMALLSERIAL PK | |
| `food_type_id` | SMALLINT NOT NULL FK → food_types, CASCADE | Sub-types are **scoped under** a type; the same name can exist under two types. |
| `name` | TEXT NOT NULL | `UNIQUE (food_type_id, name)`. |

#### `flavor_tags`
| Column | Type | Notes |
|---|---|---|
| `id` | SMALLSERIAL PK | |
| `slug` | TEXT NOT NULL UNIQUE | e.g. `cheesy`, `smoky_bbq` — from the classifier. |
| `label` | TEXT NOT NULL | Display text; `load_batch` derives it from the slug (`smoky_bbq` → `Smoky Bbq`). |

9 tags exist with 14,857 product links — populated by the classifier via `load_batch`. (An older note claiming flavor tags were unpopulated is stale.) The API's `FlavorTagOut` exposes `label` under the field name `name`.

### 4.3 Brand & branch tables

#### `restaurant_chains` — the BRAND
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | `chain_id` in code. POST bodies only — never in URLs. |
| `chain_code` | TEXT NOT NULL UNIQUE | **The brand slug and API URL key** (`bella-italia`). Written by `bootstrap_chains.py` → `load_batch`. All 378 match `^[a-z0-9-]+$` and survive reloads. User-created brands get `user-<slug>-<8 hex>`. *(Named for the foodpanda-era "chain code" it once held; it has meant "brand slug" since the brand model landed.)* |
| `name` | TEXT NOT NULL | Display name, derived from **branch names** with location stripped (case-preservingly): `KOI Thé`, `Domino's Pizza`. The scraped `chain_name` is deliberately unused — it was contaminated (named branches that don't exist in the data). |

Orphan rows (no restaurant points at them) are deleted at the end of every load (`delete_orphan_chains`), because brands are re-derived each load.

#### `restaurants` — the BRANCH (location)
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The `branch_id` in POST bodies and `/branches/*` URLs. |
| `source_restaurant_code` | TEXT NOT NULL UNIQUE | Natural key from the scrape (e.g. `acks`); upsert identity. `user-…` prefix = user-contributed. |
| `name` | TEXT NOT NULL | Branch name as scraped, often with area suffix (`Waffle Up - Dhanmondi`). |
| `address` | TEXT NULL | |
| `latitude` / `longitude` | NUMERIC(10,8) / NUMERIC(11,8) | Populated for all 451. Kept raw for display/export; the derived `geog` column lives in the geo add-on only (§4.10). |
| `rating` / `review_count` | NUMERIC(2,1) / INTEGER | **Reserved, not maintained** (§3.3). Khawon's own rating is computed live from `restaurant_reviews`. |
| `old_rating` / `old_review_count` | NUMERIC(2,1) / INTEGER | The **foodpanda scraped** rating — the cold-start fallback in `display_rating` (§7.5). |
| `budget_tier` | SMALLINT CHECK 1–3 | 1 = cheap, 3 = expensive, from the source. |
| `phone` | TEXT NULL | |
| `city` | TEXT NOT NULL DEFAULT 'Dhaka' | |
| `area` | TEXT NULL | Dhanmondi / Gulshan / Uttara — derived from the scrape batch filename, not geocoding. |
| `chain_id` | INTEGER FK → restaurant_chains, SET NULL | Nullable in the DDL but **always populated** in practice — the every-restaurant-is-a-brand invariant (§2.2). |
| `hero_image_url` / `logo_image_url` | TEXT NULL | From the scrape; hero is also settable via the branch admin endpoints (Cloudinary). |
| `google_place_id` | TEXT NULL | Currently **NULL for all 451** — the `match_google_places.py` output was never merged; deferred to the map feature. |
| `match_status` | TEXT CHECK | `unmatched \| auto_matched \| needs_review \| manually_matched \| rejected` — Google-Places matching state, **not** review moderation. |
| `is_active` | BOOLEAN DEFAULT TRUE | All queries filter on it. |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

#### `restaurant_cuisines` — M:N branch ↔ cuisine
| Column | Type | Notes |
|---|---|---|
| `restaurant_id` | INTEGER FK CASCADE, PK part | |
| `cuisine_id` | SMALLINT FK CASCADE, PK part | Rebuilt (delete + insert) for the batch's restaurants on every load. |

Cuisine is attached at **branch** level (that's how the source provides it) and additionally at product level as a single FK. Brand-level cuisine lists are unioned at read time.

#### `restaurant_sources` — scrape provenance *(defined, currently unused — 0 rows)*
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `restaurant_id` | INTEGER NOT NULL FK CASCADE | |
| `source_name` | TEXT NOT NULL | e.g. `foodpanda`. `UNIQUE (restaurant_id, source_name)`. |
| `source_url` | TEXT NULL | |
| `last_scraped_at` | TIMESTAMPTZ NULL | |
| `raw_metadata` | JSONB NULL | Escape hatch to preserve extra source fields without schema churn. |

Designed for multi-source provenance; `load_batch` doesn't populate it yet. Harmless to leave empty; use it when a second data source appears.

### 4.4 Menu tables

#### `products` — one menu item at ONE branch
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The `dish_id` in dish URLs/POST bodies. Stable *in practice* (upsert), but never persist it externally. |
| `source_product_id` | BIGINT NOT NULL UNIQUE | Natural key from the scrape; upsert identity. Verified globally unique in real data. |
| `restaurant_id` | INTEGER NOT NULL FK → restaurants, CASCADE | The branch. |
| `name` | TEXT NOT NULL | Raw menu spelling, as scraped (post-consolidation). |
| `description` | TEXT NULL | 100% populated in current data. |
| `base_price_bdt` | NUMERIC(10,2) NOT NULL CHECK ≥ 0 | The "from" price = cheapest variation. |
| `image_url` | TEXT NULL | ~90% populated. Some foodpanda URLs carry a `?width=%s` template — normalized at read time (`normalize_product_image_url`). |
| `is_sold_out` | BOOLEAN DEFAULT FALSE | Source's sold-out flag (point-in-time from scrape). |
| `category_id` / `cuisine_id` / `food_type_id` / `food_sub_type_id` | SMALLINT FK SET NULL | The four taxonomy dimensions (§4.2). `food_type_id` is 100% populated; part of the brand-card key. |
| `canonical_dish_id` | INTEGER FK → canonical_dishes, SET NULL | NULL = not comparable across brands (single-brand dish) — still fully searchable. |
| `normalized_name` | TEXT NULL (indexed) | **The brand-card grouping key**, written by `load_batch` using `canonical_match_key()` — the *same* function the canonical bootstrap groups with, so both layers agree on what "the same dish name" means. |
| `is_active` | BOOLEAN DEFAULT TRUE | **Soft delete** (§3.5). Set FALSE when a re-scrape no longer sees the item. |
| `last_seen_at` | TIMESTAMPTZ | Touched on every load that sees the product. |
| `rating` / `review_count` | | **Reserved, not maintained** (§3.3). |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

#### `product_variations` — size/option price points
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `product_id` | INTEGER NOT NULL FK CASCADE | |
| `label` | TEXT NOT NULL DEFAULT 'Regular' | `UNIQUE (product_id, label)`. Defaults to `'Regular'` instead of NULL **on purpose**: Postgres treats NULLs as distinct under UNIQUE, so `UNIQUE(product_id, NULL)` wouldn't actually block duplicate default rows. |
| `price_bdt` | NUMERIC(10,2) NOT NULL CHECK ≥ 0 | |
| `sort_order` | SMALLINT DEFAULT 0 | Source order. |

Rebuilt (delete + insert) for the batch's products on every load — variations have no natural key of their own.

#### `product_flavor_tags` — M:N product ↔ flavor tag
Composite PK `(product_id, flavor_tag_id)`, both CASCADE. Rebuilt each load, same as variations.

### 4.5 The compare layer: `canonical_dishes`

The cross-brand comparison identity (§2.1). **Fully rebuilt on every load**: links nulled, table wiped, reinserted from `canonical_dishes.json` — which is why canonical serial ids must never be persisted anywhere.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | Used in `/dishes/compare/{id}` URLs — safe only because search hands the id to compare in the same session; never store it. |
| `name` | TEXT NOT NULL | Display name = most common raw spelling among member products. |
| `aliases` | TEXT[] NOT NULL DEFAULT '{}' | Other observed spellings; GIN-indexed, searched by `/dishes/search`. Also the future home for LLM/pgvector spelling unification — fillable without schema change. |
| `food_type_id` | SMALLINT FK SET NULL | Part of the grouping key `(food_type, normalized name)`. **Sub-type is deliberately NOT in the key** — the per-product classifier disagrees on sub_type across restaurants for the same dish (Beef Tehari: "Tehari" at one place, "Biryani" at another) and would fragment groups. |
| `food_sub_type_id` / `cuisine_id` / `category_id` | SMALLINT FK SET NULL | **Majority vote** across member products — gives one stable value so a dish doesn't flicker in and out of a filter when classifiers disagree per-restaurant. Products keep their own raw values. |
| `image_url` | TEXT NULL | Fallback only; search actually serves a random image from the member products' pool (§7.6). |
| `created_at` | TIMESTAMPTZ | |

Promotion rule: a name group becomes canonical only when it spans **≥ 2 distinct brands** (`MIN_BRANDS = 2`, counted via chains.json — not branches, §2.1). Set Menu items are excluded entirely. Single-brand dishes stay `canonical_dish_id = NULL` and remain searchable.

### 4.6 Users

#### `users`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | JWT `sub` claim. |
| `email` | TEXT UNIQUE NULL | CHECK: email OR phone must be present. |
| `phone` | TEXT UNIQUE NULL | Schema supports phone signup; the current API only implements email. |
| `password_hash` | TEXT NOT NULL | bcrypt. The ORM exposes a read-only `hashed_password` alias for older code. |
| `display_name` | TEXT NOT NULL | The ORM exposes a read-only `username` alias. Uniqueness is enforced **app-side only** (register checks) — no DB constraint; a race could theoretically duplicate it. |
| `is_active` | BOOLEAN DEFAULT TRUE | Not yet checked at login (see §12). |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

### 4.7 The two review stacks (parallel by design)

Two deliberately separate stacks that answer different questions:

- **`restaurant_reviews`** — "how was the *experience* at this **location**?" Attached to a **branch**; displayed pooled per brand, tagged with the branch.
- **`product_reviews`** — "how was this **dish** at this branch?" Attached to a **product** row (one branch's dish).

Each stack has identical satellite tables (`*_review_photos`, `*_review_votes`). Shared rules:

- **Account required.** One review per user per target (`UNIQUE (user_id, restaurant_id)` / `UNIQUE (user_id, product_id)`) — resubmitting **updates** your review rather than stacking a second one.
- **Post-moderation:** reviews insert with `status='approved'` and appear immediately; all public reads and rating math filter `status='approved'`. Switching to pre-moderation later = default to `'pending'` + build an approval queue. Nothing else changes.
- Ratings are **computed live** from approved reviews at read time (§3.3).

#### `restaurant_reviews` / `product_reviews`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `user_id` | INTEGER NOT NULL FK users CASCADE | |
| `restaurant_id` / `product_id` | INTEGER NOT NULL FK CASCADE | The target. **This cascade is why products soft-delete** (§3.5). |
| `rating` | SMALLINT NOT NULL CHECK 1–5 | |
| `body` | TEXT NULL | Exposed as `comment` in the API. |
| `status` | TEXT CHECK `pending\|approved\|rejected` DEFAULT 'pending' | App currently writes `'approved'` (post-moderation). |
| `is_verified_visit` / `is_verified_order` | BOOLEAN DEFAULT FALSE | Future verification badge; exposed as `is_verified`. |
| `helpful_count` / `not_helpful_count` | INTEGER DEFAULT 0 | Denormalized vote tallies — **not yet maintained**; the votes tables are the source of truth when voting ships. |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

#### `*_review_photos`
`id` PK · `review_id` FK CASCADE · `image_url` NOT NULL · `sort_order` · `created_at`. Schema-ready; no upload endpoint yet.

#### `*_review_votes`
Composite PK `(review_id, user_id)` — one vote per user per review · `is_helpful` BOOLEAN NOT NULL · `created_at`. Schema-ready; no endpoint yet.

### 4.8 Views (`v_restaurant_summary`, `v_product_detail`, `v_canonical_dish_comparison`)

Three convenience views defined at the bottom of `schema.sql` — handy for ad-hoc `psql` inspection (e.g. `v_canonical_dish_comparison` is the raw shape of the compare feature). **The API does not use them**; it builds richer shapes in Python (§7). Note that `v_restaurant_summary`/`v_product_detail` expose the *reserved* stored rating columns, so their `rating` fields read NULL/0 — trust the API's computed ratings, not the views'.

### 4.9 Indexes & extensions — what each one serves

| Index | Serves |
|---|---|
| `idx_products_name_trgm`, `idx_restaurants_name_trgm`, `idx_canonical_dishes_name_trgm` (GIN, `gin_trgm_ops`) | The `ILIKE '%q%'` searches in `/dishes/search` and `/restaurants?q=`. **pg_trgm is why substring search is fast**; a plain btree can't serve infix `LIKE`. |
| `idx_canonical_dishes_aliases` (GIN on TEXT[]) | Alias matching in search. |
| `idx_products_normalized_name` (btree) | Brand-card grouping / brand-dish detail lookups. |
| `idx_products_restaurant`, `idx_products_canonical_dish`, `idx_restaurants_chain` | The three hot join paths: branch menu, compare, brand's branches. |
| `idx_products_active`, `idx_products_sold_out` (partial) | The `WHERE is_active` filter on almost every product query. |
| `idx_products_category` / `_food_type` / `_price` / `_rating`; `idx_restaurants_city` / `_area` / `_rating` | Browse/filter paths (some speculative, cheap to keep). |
| Review indexes on `restaurant_id`/`product_id`/`user_id`/`status` | Rating aggregation and "my review" lookups. |

### 4.10 Changing the schema

**`schema.sql` builds FRESH databases. `migrations/` brings EXISTING ones up to date.** No Alembic, on purpose: Postgres-native features + a re-derivable catalogue made it overhead. But "reset and reload" stops being safe the moment there's one real user, so:

> **Every schema change goes in BOTH places**: edit `schema.sql`, and add a new numbered, idempotent (`IF NOT EXISTS`) file in `migrations/`. Apply with `psql "$DATABASE_PUBLIC_URL" -f migrations/NNN_name.sql`. Currently there's one: `001_products_normalized_name.sql`.

**Geo is an optional add-on, not a migration.** `schema_geo.sql` adds `CREATE EXTENSION postgis`, a generated `geog GEOGRAPHY(Point,4326)` column derived from lat/long, and a GiST index — enabling `ST_DWithin` radius queries and `ORDER BY geog <-> point` nearest-first. Railway's Postgres **has no PostGIS** (verified), so `schema.sql` stays portable and geo applies only on a PostGIS host when "near me" ships. Raw coords are already populated for all 451 branches, so it's purely additive.

⚠️ Adding a **product column that the pipeline writes** requires touching FIVE hardcoded lists in `load_batch.py`, and missing one fails *silently* — read [`HANDOFF.md` §9 trap 1](HANDOFF.md) before doing this.

---

## 5. The Python layer

### 5.1 `database.py` — engine & sessions (34 lines)

Resolution order for the connection string: `USE_SQLITE=1` → local SQLite file (import-check convenience only — the real schema won't apply there); else **`DATABASE_PUBLIC_URL`** (Railway public proxy); else `DATABASE_URL`.

⚠️ It calls `load_dotenv()` at import: `.env`'s `DATABASE_PUBLIC_URL` silently wins over an exported `DATABASE_URL`. Any script that must target a non-production DB has to **SET** `os.environ["DATABASE_PUBLIC_URL"]` (not pop it) *before importing* — `load_dotenv` won't override an already-set variable. Getting this wrong once wrote test rows into production. Tests use the `temp_db` fixture in `tests/conftest.py`, which handles it; don't roll your own.

`get_db()` is the FastAPI dependency yielding one session per request.

### 5.2 `models.py` — the thin ORM (314 lines)

One SQLAlchemy class per table, mirroring `schema.sql` exactly — mappings only, **no DDL authority** (§3.1). Only two things aren't pure mirroring:

- `User.username` / `User.hashed_password` — read-only `@property` aliases for the v1 names (`display_name` / `password_hash`), kept so older serialization code works. Writes use the real column names.
- The geo `geog` column is deliberately unmapped (would need geoalchemy2; it's generated anyway — geo queries will be raw SQL).

Relationship cascades mirror the DB: deleting a `Restaurant` ORM object cascades to products → variations/reviews. That's what makes `DELETE /branches/{id}` genuinely destructive (§8).

### 5.3 `schemas.py` — the API contract (36 Pydantic models)

This file **is** the frontend contract; `khawon-web/src/types/api/` mirrors it in TypeScript. Change a response model → update the TS mirror → `npm run typecheck`.

Field-by-field, grouped by domain. "←" = where the value comes from.

#### Auth
| Model | Fields | Notes |
|---|---|---|
| `UserCreate` | `email: EmailStr`, `username` (3–50), `password` (6–128) | Request body for register. `username` → stored as `display_name`. |
| `UserOut` | `id`, `email`, `username`, `created_at` | `username` ← the ORM alias for `display_name`. |
| `Token` | `access_token`, `token_type="bearer"` | JWT, 7-day default expiry. |

#### Taxonomy
| Model | Fields | Notes |
|---|---|---|
| `FoodTypeOut` | `id`, `name`, `description`, `image_url`, `parent_id` | Last three are **always `None`** — kept for v1 contract compat (the columns were dropped, §4.2). |
| `FoodTypePopularOut` | + `restaurant_count`, `review_count`, `average_rating` | Stats derived live: distinct restaurants with an active product of the type; approved product-review stats over those products. |
| `FoodSubTypeOut` | `id`, `name`, `food_type_id`, `dish_count`, `image_urls[]` | `dish_count` = active products; `image_urls` = deduped pool (≤20) of member product images for UI cycling. |
| `FoodSubTypeListResult` | `food_type`, `sub_types[]` | |
| `CuisineOut` | `id`, `name` | |
| `FlavorTagOut` | `id`, `name` | `name` ← `flavor_tags.label` (the slug is not exposed). |

#### Brands, branches & dishes
| Model | Fields | Notes |
|---|---|---|
| `BrandOut` | `id`, `slug`, `name` | The brand stamp on every card. **Link with `slug`**; `id` (chain_id) is for POST bodies only. |
| `BrandListOut` | `id`, `slug`, `name`, `branch_count`, `areas[]`, `image_url`, `food_types[]`, `cuisines[]` (names), `display_rating`, `display_rating_source`, `display_review_count` | One brand in browse. `areas` ← distinct branch areas; `image_url` ← first branch hero; `food_types`/`cuisines` ← union over branches; rating trio ← §7.5. |
| `BrandDetailOut` | `id`, `slug`, `name`, `branch_count`, `branches[]: RestaurantSummaryOut`, `display_rating`, `display_review_count`, `display_rating_source` | The brand page. `display_review_count` is **the count behind `display_rating`, same source** — pairing a rating with a count from a different source is the bug that once rendered "4.9 · 0 reviews". |
| `RestaurantSummaryOut` | `id`, `name`, `area`, `address`, `image_url`, `google_place_id`, `display_rating`, `display_rating_source` | A **branch** in embedded contexts. `id` here is a branch-row id → `/branches/*` only. |
| `BranchResolveOut` | `id`, `chain_id`, `chain_slug`, `name`, `area`, `address`, `phone`, `google_place_id`, `image_url` | Resolves branch → brand so old numeric branch links redirect to `/restaurants/{chain_slug}`; also backs the branch edit form. |
| `BrandDishOut` | `brand: BrandOut`, `food_type_id`, `slug`, `name`, `description`, `image_url`, `category_raw`, `food_type`, `cuisines[]`, `flavor_tags[]`, `canonical_dish_id`, `price_min_bdt`, `price_max_bdt`, `price_varies`, `branch_count`, `brand_branch_total`, `is_sold_out_everywhere`, `average_rating`, `review_count` | **The workhorse card** (§7.1). `slug` = slugified `normalized_name`; `(brand.id, food_type_id, slug)` is the card's natural key. Prices **always present**: `price_varies=False` ⇒ min == max, UI shows one number — one rule, no branching. `branch_count`/`brand_branch_total` → the "at 2 of 3 branches" badge (suppress when equal). Rating pooled across branches, approved only. |
| `BrandDishDetailOut` | + `branches[]: BrandBranchOut` | Card + per-branch breakdown. |
| `BrandBranchOut` | `restaurant_id`, `restaurant_name`, `area`, `product_id`, `price_bdt`, `is_sold_out`, `average_rating`, `review_count` | One branch serving the dish. `product_id` is what `POST /reviews` takes — dish reviews target ONE branch's row. |
| `DishOut` | `id`, `name`, `description`, `price_bdt`, `image_url`, `is_sold_out`, `is_active`, `category_raw`, `variations[]`, `food_type`, `canonical_dish_id`, `cuisines[]`, `flavor_tags[]`, `restaurant: RestaurantSummaryOut`, `average_rating`, `review_count` | A single **product** row (one branch's dish) — dish detail and branch-menu contexts. Rating ← approved product reviews for this row only. |
| `DishVariationOut` | `label`, `price_bdt` | |

#### Canonical / compare / search
| Model | Fields | Notes |
|---|---|---|
| `CanonicalDishOut` | `id`, `name`, `food_type`, `aliases[]`, `image_url` | |
| `CanonicalDishMatch` | + `restaurant_count`, `dish_count`, `average_rating`, `min_price_bdt`, `max_price_bdt` | Search's compare strip. `restaurant_count` counts **brands** (distinct chain_id); `image_url` ← random pick from member-product image pool. |
| `DishCompareResult` | `canonical_dish`, `dishes[]: BrandDishOut`, `total`, `offset`, `limit`, `average_rating`, `min_price_bdt`, `max_price_bdt` | Compare view: **one row per brand**. Aggregates span all rows, not just the page. |
| `DishSearchResult` | `query`, `canonical_matches[]` (≤10), `dishes[]: BrandDishOut`, `total`, `offset`, `limit` | The core search response. `total` counts **brand cards**, not product rows. |
| `FoodDetailResult` | `food_type: FoodTypePopularOut`, `restaurants[]: BrandListOut` | Food-type page: brands serving the type. |

#### Reviews
| Model | Fields | Notes |
|---|---|---|
| `ReviewCreate` | `dish_id`, `rating` (1–5), `comment` | `dish_id` = a **product** id (one branch's row). |
| `ReviewOut` | `id`, `dish_id`, `restaurant_id`, `dish_name`, `username`, `rating`, `comment`, `is_verified`, `created_at` | `restaurant_id` derived through the product; `comment` ← `body`; `is_verified` ← `is_verified_order`. |
| `RestaurantReviewCreate` | `branch_id`, `rating`, `comment` | Brand comes from the URL path; `branch_id` says which location was visited (validated to belong to the brand). |
| `RestaurantReviewOut` | `id`, `restaurant_id`, `branch_name`, `branch_area`, `username`, `rating`, `comment`, `is_verified`, `created_at` | Branch name/area let the UI tag pooled brand reviews by location. |
| `ReviewListResult` / `RestaurantReviewListResult` | `reviews[]`, `total`, `offset`, `limit` | |

#### Pagination wrappers & misc
| Model | Notes |
|---|---|
| `RestaurantCatalogueResult` | `restaurants[]: BrandListOut`, `total`, `offset`, `limit` — GET /restaurants. |
| `BranchListResult` | `branches[]: RestaurantSummaryOut`, `total`, `offset`, `limit` — GET /branches. |
| `PlaceSearchResult`, `PlacePhotoOut` | Google Places proxy shapes (contribute flow). |
| `FoodImageSearchResponse`, `FoodImageSearchResult` | AI food-image generation shapes; `search_help` carries setup guidance when HF isn't configured. |

---

## 6. How data gets IN — the pipeline

Five offline stages, all deterministic and re-runnable. Full run commands: [`HANDOFF.md` §10](HANDOFF.md). Scripts currently exist both in this repo (authoritative) and next to the data in `…\strip data\code\` (working copy) — **edit the repo copy, then copy over**; this duplication is a known hazard (see §12).

### 6.1 `classify_batch.py` — taxonomy assignment
Input: stripped scrape JSON. Output: `*_products.json` + `*_restaurants.json` per area. Assigns the four taxonomy dimensions via ordered rule lists (`FOOD_TYPE_RULES`, first match wins — the rule *order* implements "format wins over garnish", §4.2) plus flavor tags. 100% food_type coverage on real data.

### 6.2 `consolidate_variants.py` — size-row merge
**Problem:** foodpanda is inconsistent about sizes. Usually sizes are one product's `variations[]`; for ~420 dishes each size is a *separate product row* ("Steamed Chicken Momo 5 Pcs / 6pcs / 7 Pcs…"). Left alone, those rows would (a) collapse into a canonical dish whose "price range" is really one restaurant's portion ladder, and (b) sometimes carry *different classifications* for the same dish.

**Fix:** group by `(restaurant, canonical_match_key(name))` — the same key the canonical layer uses, so spelling-drifted size rows ("Chaap Polao Half" vs "Chap Pulao - Full") merge here rather than surviving as duplicates. 2+ rows for one dish → single product whose `variations[]` carries each size as a labelled price point; classification resolved by majority vote; description = longest; sold-out = only if *all* sizes are. Because grouping is strictly per-restaurant, the looser key can't fuse different brands' dishes. 16,918 → 16,385 products.

### 6.3 `bootstrap_chains.py` — brand identity
Groups branches into brands by **normalized name** (lowercase, strip ` - <branch>` suffixes, strip ~40 known area tokens, strip punctuation/outlet numbers) → `chains.json` (slug, display name, member codes).

- The source `chain_code` is **not** the grouping key — it's wrong for ~21% of real brands (splits Waffle Up across two codes; misses Habanero entirely). It's a signal only.
- `BRAND_OVERRIDES: {source_code → slug}` pins exceptions — same slug forces a merge, different slug forces a split. **Currently empty**: normalization got all 53 multi-location groups right, owner-reviewed via `--review` mode (prints candidate groups, writes nothing).
- Display name comes from **branch names** with location stripped *case-preservingly* (`strip_location`: `KOI Thé`, `Domino's Pizza` survive); most-common wins, ties break shorter. The scraped `chain_name` is never used (contaminated — see §4.3).

### 6.4 `bootstrap_canonical_dishes.py` — the compare layer
Groups products by `(food_type, canonical_match_key(name))`; promotes a group to a canonical dish only when it spans **≥ 2 brands** (via chains.json). Set Menu excluded. Then a conservative fuzzy pass merges near-identical keys *within a food type* (SequenceMatcher ≥ 0.92) — but **never** across different protein tokens (chicken ≠ beef) or distinct modifiers (shahi ≠ plain ≠ bbq), so "Chicken Biryani" and "Beef Biryani" can't fuse. Output carries majority-vote taxonomy + `member_source_product_ids` for linking.

**`canonical_match_key()` is the shared vocabulary of the whole system**: size-token strip → phrase + spelling map (`biriyani→biryani`, `pulao→polao`, plural folding) → stopword removal → sorted tokens. It's imported by both `consolidate_variants` and `load_batch` (which stores it as `products.normalized_name`), so the consolidation, brand-card, and canonical layers all agree on what "the same dish name" means. Extend the `SPELLING_MAP` there and every layer inherits it on the next load.

### 6.5 `load_batch.py` — the idempotent loader
`python load_batch.py consolidated.json canonical_dishes.json "restaurants_*.json" --chains chains.json`. Three committed phases:

1. **Lookups, brands, restaurants.** Get-or-create lookup rows from the data (taxonomy tables grow on demand). Upsert brands by slug; upsert restaurants by `source_restaurant_code` (area derived from each file's name); rebuild `restaurant_cuisines`; delete orphan chains.
2. **Products.** Upsert by `source_product_id` with **change detection**: a signature tuple of every written column is compared against the existing row; unchanged rows are skipped (makes reloads fast, ~50s, and writes minimal). Changed rows update via a single-round-trip `unnest` bulk SQL. Vanished products (in DB, not in this batch, belonging to this batch's restaurants) → `is_active = FALSE` — never deleted. Variations and flavor-tag links are delete-and-rebuilt for the batch's products.
3. **Canonical dishes.** Full replace: null all links, wipe the table, reinsert, relink members by `source_product_id`.

⚠️ The change-detection design is also its trap: the written-columns list is hardcoded in **five places**, and missing one silently makes a column un-backfillable or makes the load report success while writing nothing. Both happened. Regression tests pin it. Details: [`HANDOFF.md` §9 trap 1](HANDOFF.md).

Safe to re-run anytime; user data (users/reviews) and user-contributed branches are untouched (user branches aren't in any batch, so the deactivation sweep never reaches their products).

---

## 7. How data gets OUT — read-time assembly

The interesting backend logic is here: base rows go in, product-shaped responses come out, per request.

### 7.1 Brand dish cards — `brand_dishes.build_brand_dishes()`

Input: hydrated `Product` rows (any set). Groups by **`(chain_id, food_type_id, normalized_name)`** and emits one `BrandDishOut` per group:

- **Why `food_type_id` is in the key:** without it, a brand's "Chicken" *curry* fuses with its own "Chicken" *pizza* — same normalized name. There's a regression test pinning this.
- `name` ← most common raw spelling among members; `image_url` ← first member with one; `description`/`category`/`cuisine`/`flavor_tags` ← first member (members are near-identical by construction).
- `price_min/max` ← min/max of members' base prices; `price_varies` ← min ≠ max (true for only ~6.5% of multi-branch cards).
- `branch_count` ← distinct restaurants among members; `brand_branch_total` ← the brand's total active branches (one grouped query for the whole batch).
- `average_rating`/`review_count` ← **pooled** approved product reviews across members (sums fetched per product in one grouped query, pooled in Python).
- `is_sold_out_everywhere` ← all members sold out.

Two grouped queries total per call — no N+1.

### 7.2 Brand browse — `brand_browse.py`

`matching_chain_ids(q)`: chains with ≥1 active branch, filtered by brand/branch/area/address `ILIKE`, ordered by name. `build_brand_list(chain_ids)`: assembles `BrandListOut` cards in a fixed number of grouped queries (chains, branches, food types via products, cuisines, review stats).

### 7.3 Search — `dish_detail.search_dishes()` + `search_canonical_dishes()`

`GET /dishes/search?q=` returns two independent lists:

1. **`canonical_matches`** (capped 10): canonical dishes whose name, aliases, or food type match — the "compare across restaurants" strip, ordered by brand spread.
2. **`dishes`**: paginated brand cards. The clever part is *what gets hydrated*: a lightweight query fetches only `(id, name, chain_id, food_type_id, normalized_name)` for every match (own name, food-type name, or canonical name `ILIKE`); rows are grouped into cards **before** pagination (so `total` counts cards, not branch rows); groups are ranked exact < prefix < substring < matched-via-type; and only the requested page's groups get the full joinedload hydration + card build. A broad query like "chicken" never hydrates thousands of rows.

Coming up empty returns empty lists, not 404 — no results is a normal state.

### 7.4 Compare — `get_canonical_dish_comparison()`

All active products linked to the canonical id → `build_brand_dishes` → **one row per brand** (a chain is `branch_count=3` on one row, not 3 rows — comparing a dish to itself across branches is not a comparison). Sorted best-rated first, paginated; headline min/max price and average rating computed across **all** rows, not just the page.

### 7.5 Display ratings — `restaurant_reviews.resolve_display_rating()`

The cold-start rule, resolved **server-side** so every UI surface agrees:

```
khawon has ≥1 approved review  → (khawon avg,  khawon count,  "khawon")
else foodpanda rating exists   → (fp rating,   fp count,      "foodpanda")
else                           → (None, 0, None)
```

For a **brand**, the khawon side pools location reviews across branches, and the foodpanda side is a review-count-weighted average of branch ratings (planned upgrade: nearest branch, once geo lands).

**The iron rule: rating, count, and source travel together.** `display_review_count` is the count *behind* `display_rating`, from the same source. Pairing a foodpanda rating with the khawon count (0) shipped a page reading "4.9 · 0 reviews" once. Never mix.

The rating vocabulary across the API:
- `average_rating`/`review_count` = khawon's own, live from approved reviews.
- `old_rating`/`old_review_count` (DB) = scraped foodpanda values.
- `display_*` = the resolved trio above.

### 7.6 Image pools — `product_image_pools.py`

Sub-type tiles and canonical search cards don't store images; they serve a deduped pool (≤20) of member products' images in one grouped query — canonical matches pick randomly per request, sub-types return the pool for UI cycling. `dish_detail.normalize_product_image_url()` patches foodpanda URLs stored with an unresolved `?width=%s` template.

---

## 8. API endpoint reference

40 routes. 🔒 = requires `Authorization: Bearer <JWT>`.

**The URL rule** (§3.4): `/restaurants/*` takes the brand **slug**; `/branches/*` takes branch-row ids; `/dishes/{id}` takes product ids; chain_id and branch_id appear in POST bodies only. Numeric `/restaurants/{int}` 404s **by design**.

### Brand surface — `routers/restaurants.py`
| Endpoint | Returns | Notes |
|---|---|---|
| `GET /restaurants/?q=&offset=&limit=` | `RestaurantCatalogueResult` | Brand browse; q filters brand/branch/area/address. limit ≤ 100, default 24. |
| `GET /restaurants/{slug}` | `BrandDetailOut` | Brand page: branches as tags, pooled display rating. |
| `GET /restaurants/{slug}/menu` | `BrandDishOut[]` | Merged deduped brand menu (union + badge). Sorted category, then name. Unpaginated. |
| `GET /restaurants/{slug}/dishes/{food_type_id}/{dish_slug}` | `BrandDishDetailOut` | Brand dish + per-branch breakdown. Natural-key URL. |
| `GET /restaurants/{slug}/reviews?offset=&limit=` | `RestaurantReviewListResult` | Location reviews pooled across the brand, branch-tagged, newest first. |
| 🔒 `POST /restaurants/{slug}/reviews` | `RestaurantReviewOut` (201) | Body `{branch_id, rating, comment}`; branch validated to belong to the brand. Upserts (one per user per location). |
| 🔒 `DELETE /restaurants/{slug}/reviews/{review_id}` | 204 | Own reviews only (403 otherwise). |

### Branch surface — `routers/branches.py` (admin/contribute)
| Endpoint | Returns | Notes |
|---|---|---|
| `GET /branches/?q=&offset=&limit=` | `BranchListResult` | Location list; limit ≤ 200, default 50. |
| `GET /branches/{branch_id}` | `BranchResolveOut` | Branch → brand resolution (old-link redirects; edit form). |
| `GET /branches/{branch_id}/dishes` | `DishOut[]` | ONE location's raw menu (inspection). |
| 🔒 `POST /branches/` | `RestaurantSummaryOut` (201) | Multipart form. Creates a **brand of one** (`user-<slug>-<hex>` codes); pipeline regroups by name later. Image via upload or Google photo → Cloudinary. |
| 🔒 `PUT /branches/{branch_id}` | `RestaurantSummaryOut` | Edit details; image optional. |
| 🔒 `PUT /branches/{branch_id}/photo` | `RestaurantSummaryOut` | Photo only (400 if neither source given). |
| 🔒 `DELETE /branches/{branch_id}` | 204 | ⚠️ **Hard delete** — ORM cascade removes the branch's products and their reviews. Any signed-in user can call it currently (no role system). |

### Dish & review surface — `routers/dishes.py`, `routers/reviews.py`
| Endpoint | Returns | Notes |
|---|---|---|
| `GET /dishes/search?q=&offset=&limit=` | `DishSearchResult` | §7.3. `total` counts brand cards. |
| `GET /dishes/compare/{canonical_dish_id}` | `DishCompareResult` | §7.4. One row per brand. |
| `GET /dishes/{dish_id}` | `DishOut` | One product row. |
| `GET /dishes/{dish_id}/reviews?offset=&limit=` | `ReviewListResult` | Approved only, newest first. |
| 🔒 `POST /reviews/` | `ReviewOut` (201) | Body `{dish_id, rating, comment}` — targets one branch's product row. Upserts. |
| 🔒 `DELETE /reviews/{review_id}` | 204 | Own reviews only. |

### Taxonomy surface — `routers/food_types.py`
| Endpoint | Returns | Notes |
|---|---|---|
| `GET /food-types/` | `FoodTypeOut[]` | Alphabetical. |
| `GET /food-types/top?limit=` | `FoodTypePopularOut[]` | Ranked by review count then rating. |
| `GET /food-types/catalogue?q=` | `FoodTypePopularOut[]` | Full list with stats. |
| `GET /food-types/{id}` / `{id}/detail` / `{id}/sub-types` | `FoodTypeOut` / `FoodDetailResult` / `FoodSubTypeListResult` | Detail = stats + brands serving it. |
| 🔒 `POST /food-types/` · `PUT /food-types/{id}` | `FoodTypeOut` | Name only persists (v2 dropped image/description — accepted, ignored). |
| 🔒 `PUT /food-types/{id}/photo` | **501** | Deliberate: no image column in v2; explicit error beats a silent no-op. |
| 🔒 `DELETE /food-types/{id}` | 204 | FK is SET NULL — products survive, unclassified. |

### Support surfaces
| Endpoint | Notes |
|---|---|
| `POST /auth/register` · `POST /auth/login` · 🔒 `GET /auth/me` | §9. Login is OAuth2 form (`username` field accepts display name **or** email). |
| `GET /places/search?q=&area=` · `GET /places/details?place_id=` · `GET /places/photo?photo_name=` | Google Places proxy for the contribute flow — key stays server-side; photo endpoint proxies bytes. |
| `GET /food-images/generate?q=&limit=` (alias `/search`) · `GET /food-images/generated/{image_id}` | Hugging Face FLUX.1-schnell text-to-image for taxonomy/admin imagery; generations cached in memory 1h, served for preview, persisted to Cloudinary only when chosen. |
| `GET /` | Health check. |

---

## 9. Auth

Standard JWT bearer flow, deliberately minimal:

- **Register** with email + username + password (≥6 chars). Password → bcrypt → `password_hash`. Username stored as `display_name` (app-side uniqueness check only).
- **Login** (`OAuth2PasswordRequestForm`) by display name or email → JWT `{sub: user_id, exp}` signed HS256 with `JWT_SECRET`, default expiry 7 days (`JWT_EXPIRE_MINUTES=10080`).
- `get_current_user` decodes the token and loads the user; every 🔒 endpoint depends on it.

Not yet implemented (fine pre-launch, listed so nobody assumes otherwise): no roles/admin tier — every signed-in user can hit admin-ish endpoints including hard branch delete; `users.is_active` isn't checked at login; no refresh tokens, rate limiting, email verification, or password reset. ⚠️ `JWT_SECRET` falls back to a hardcoded dev default — **must** be set in production. CORS is `allow_origins=["*"]` — tighten at launch.

## 10. External services & environment variables

| Service | Used for | Env vars |
|---|---|---|
| Railway Postgres | The database | `DATABASE_PUBLIC_URL` (preferred) or `DATABASE_URL`; `USE_SQLITE=1` for import checks |
| Cloudinary | All image uploads (branch photos, food images) | `CLOUDINARY_URL` or the `CLOUDINARY_CLOUD_NAME/_API_KEY/_API_SECRET` trio |
| Google Places API (New) | Contribute-flow lookup + photos | `GOOGLE_PLACES_API_KEY` |
| Hugging Face Inference | AI food-image generation | `HF_TOKEN` (fine-grained, Inference-Providers scope); optional `HF_IMAGE_MODEL`, `HF_INFERENCE_PROVIDER` |
| — | Auth | `JWT_SECRET` (**required in prod**), `JWT_EXPIRE_MINUTES` |

All optional integrations degrade gracefully: missing config → 503 with a human-readable setup message (surfaced in the admin UI via `search_help`), not a crash.

## 11. Testing

`python -m pytest tests/` — **73 tests**. The `temp_db` fixture (`tests/conftest.py`) creates and drops a throwaway `khawon_test` database *on the same Postgres server*, applies `schema.sql`, and — critically — sets `DATABASE_PUBLIC_URL` correctly so nothing touches production (§5.1). Runs take ~3 min over the Railway proxy.

The suite is mostly **regression tests encoding past bugs**: the five-list `load_batch` trap, signature drift, brand-key food-type separation, id-overlap slug behavior, rating-source pairing. If one fails you've rediscovered a bug someone already paid for — fix the code, don't edit the test.

## 12. Known gaps & deferred work

Verified 2026-07-17:

| Item | State |
|---|---|
| **Pipeline script duplication** | Scripts live in repo *and* `strip data\code\`; nothing enforces sync. Fix: `--data-dir` flag, single repo copy. Highest-risk housekeeping item. |
| **Google Place IDs** | `google_place_id` NULL for all 451; `match_google_places.py` output never merged. Deferred to the map feature (coords already work). |
| **Near-me / geo** | Needs a PostGIS host + `schema_geo.sql` (§4.10). Then upgrade brand rating to nearest-branch. |
| **Spelling unification / semantic search** | `canonical_match_key` is exact-match after a hand-built spelling map; genuine variants it misses stay split. Plan: pgvector embeddings (also powers craving/semantic search). `aliases[]` is the landing zone. |
| **Review photos & votes** | Tables exist; no endpoints. `helpful_count` denorms not maintained. |
| **Moderation queue** | Only needed if switching off post-moderation. |
| **Roles/admin auth** | No role tier; see §9. Also: tighten CORS, set `JWT_SECRET`. |
| **`restaurant_sources`** | Defined, unpopulated (§4.3). |
| **Food-type images** | Dropped in v2; photo endpoint 501s. Restore = re-add columns via migration if the UI needs them. |
| **Stored rating denorms** | `products.rating` etc. reserved and unmaintained (§3.3) — revisit only if read-time aggregation gets slow. |
