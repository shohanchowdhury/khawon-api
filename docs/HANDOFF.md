# Khawon — Engineering Handoff

**Written:** 2026-07-16
**Purpose:** Single source of truth for how Khawon's backend works, why it's built this way, and where the traps are. Written to onboard someone with zero context.

**State at time of writing — everything below is verified, not remembered:**

| | |
|---|---|
| `khawon-api` | `074d227` on `main`, pushed, clean. **73 tests pass** (`python -m pytest tests/`) |
| `khawon-web` | `b419296` on `main`, pushed, clean. `npm run typecheck` clean |
| Live DB | Railway Postgres, loaded and verified |

> The old `strip data/code/KHAWON_HANDOFF.md` is **stale** (describes the pre-brand-model state). This file supersedes it.

---

## 1. The product

**Khawon** (khawon.com) — a food **discovery/catalogue** app for Dhaka, Bangladesh. **Not delivery.**

The differentiator vs foodpanda / Google Maps is one sentence:

> **Search a specific DISH → compare THAT dish across restaurants (price + rating) → with reviews.**

Three verbs: **search, compare, trust.** Every design decision below traces back to one of those. When something is ambiguous, ask "which verb does this serve?"

---

## 2. The mental model (read this first — nothing else makes sense without it)

Two layers that look similar and are constantly confused. They **stack**; they don't compete.

| Layer | Job | Scope | Example |
|---|---|---|---|
| **Chain grouping** | Dedupe *within* one brand | intra-brand | Domino's Margherita at 3 branches → **1 card** |
| **Canonical dish** | Compare *across* brands | inter-brand | Chicken Biryani at **9 different brands** |

**Why this matters:** they were originally conflated. `canonical_dishes` promoted a dish on "2+ distinct **restaurants**" — which counted *branches*. So a chain-exclusive drink sold at 3 branches of one chain (`Peach Apple Fizz`) looked "comparable across restaurants" when nobody else on earth makes it. Fixing this dropped canonical dishes from 2,527 → **1,431** (–40%). That wasn't damage; those 1,008 dishes were the *chain layer's* job misfiled into the *canonical layer*. They're still fully searchable — they just stopped pretending to be comparisons.

### The three-word vocabulary

Get these right and the codebase reads cleanly:

- **Brand** = `restaurant_chains` row. **In the API, "restaurant" MEANS brand.**
- **Branch** (a.k.a. location) = `restaurants` row. A physical outlet.
- **Every restaurant is a brand.** A standalone restaurant is *a brand of one* (`branch_count == 1`). This is the load-bearing invariant: it means every rule is a plain `GROUP BY chain_id` with **zero** `if chain else` special-casing. 451 branches → 378 brands (53 multi-location, 325 solo).

---

## 3. Architecture

```
  scrape (foodpanda)
        │
        ▼
  ┌─────────────────── PIPELINE (offline, deterministic, re-runnable) ──────────────────┐
  │  strip_restaurants.py       raw scrape        → new data/restaurants_<area>_partN.json│
  │    (lives at `strip data/`, NOT `strip data/code/` like the others)                   │
  │  classify_batch.py          restaurants_*     → *_products.json + *_restaurants.json │
  │  consolidate_variants.py    *_products.json   → consolidated.json                    │
  │  bootstrap_chains.py        *_restaurants.json→ chains.json          (BRAND identity)│
  │  bootstrap_canonical_dishes consolidated+chains→ canonical_dishes.json (COMPARE layer)│
  └──────────────────────────────────────┬──────────────────────────────────────────────┘
                                         │  load_batch.py  (idempotent upsert)
                                         ▼
                              PostgreSQL (schema.sql = source of truth)
                                         │
                                         ▼
                              FastAPI (main.py + routers/)
                                         │
                                         ▼
                              React + Vite (khawon-web)
```

**Guiding principle: SQL-first.** `schema.sql` is the source of truth, **not** the ORM. It uses Postgres-native features the ORM can't express (PostGIS geography, generated columns, GiST/trigram indexes). `models.py` is a *thin read/write mapping* over tables `schema.sql` already created. `main.py` must **never** call `create_all()` or run ad-hoc migrations — that was removed on purpose.

**Second principle: curated data lives in the pipeline, never as hand-edited DB rows.** The DB gets reloaded from pipeline output; anything typed directly into the database is wiped on the next load.

---

## 4. Repo layout

```
Khawon/
├── khawon-api/                 # FastAPI + SQLAlchemy + Postgres
│   ├── schema.sql              # ★ SOURCE OF TRUTH for the DB (fresh databases)
│   ├── schema_geo.sql          # optional PostGIS add-on (near-me). NOT a migration.
│   ├── migrations/             # numbered idempotent .sql for EXISTING databases
│   ├── models.py               # thin ORM mapping — does NOT drive DDL
│   ├── database.py             # engine/session; reads DATABASE_PUBLIC_URL or DATABASE_URL
│   ├── schemas.py              # Pydantic API contract (what the frontend consumes)
│   ├── main.py                 # app + router registration
│   │
│   ├── load_batch.py           # ★ pipeline → Postgres loader (idempotent). SEE TRAPS §9
│   ├── bootstrap_chains.py     # brand identity (name → brand, + BRAND_OVERRIDES)
│   ├── bootstrap_canonical_dishes.py  # cross-brand compare layer (fuzzy matching)
│   ├── consolidate_variants.py # merges same-restaurant size-variant rows
│   │
│   ├── brand_dishes.py         # ★ read-time brand card grouping (the dedupe)
│   ├── brand_browse.py         # brand list assembly (browse/catalogue)
│   ├── dish_detail.py          # dish/product queries + search + compare
│   ├── restaurant_reviews.py   # restaurant-level reviews + display-rating resolution
│   ├── food_detail.py, food_sub_types.py, product_image_pools.py
│   ├── auth.py, places.py, storage.py, food_images.py
│   │
│   ├── routers/
│   │   ├── restaurants.py      # BRAND surface ({slug} = brand)
│   │   ├── branches.py         # BRANCH admin surface ({branch_id} = location row)
│   │   ├── dishes.py           # search + compare + dish detail
│   │   ├── reviews.py          # dish reviews
│   │   └── auth.py, food_types.py, food_images.py, places.py
│   │
│   ├── tests/                  # pytest; conftest.py has the temp_db fixture (SEE §9)
│   └── docs/
│       ├── HANDOFF.md          # this file
│       └── superpowers/        # ("superpowers" = the tool that generated these; ignore the name)
│           ├── specs/          # design docs (the WHY)
│           └── plans/          # implementation plans (the HOW, historical)
│
└── khawon-web/                 # React 19 + Vite frontend
    └── src/
        ├── api/client.ts       # every backend call
        ├── types/api/          # TS mirrors of schemas.py
        └── pages/, components/, utils/
```

**Pipeline scripts exist in TWO places:** the repo (authoritative, versioned) and `C:\Users\shoha\OneDrive\Desktop\strip data\code\` (working copy, where the data lives). They're identical apart from line endings. **Edit the repo copy, then copy to the data folder.** Data outputs (`v2_output/*.json`) live only in the data folder — they're big and not in git.

> ⚠️ **This duplication is a hazard** and should probably be collapsed: two copies of the same script can drift silently, and nothing enforces the sync. The only reason it exists is that the scripts need to sit next to the (ungitted) data. Worth fixing by having the scripts take a `--data-dir` and living only in the repo. Also note `strip_restaurants.py` and `match_google_places.py` live **outside** `code/` and are **not** mirrored into the repo at all — they're earlier-stage/one-off tools.

---

## 5. The database

**20 tables.** Postgres 18 on Railway. Extensions: `pg_trgm` (fuzzy/substring name search — dish search is the core feature). **PostGIS is NOT available on Railway** — see §8.

### Live data (verified 2026-07-16)

| Table | Rows | Note |
|---|---|---|
| `restaurants` | 451 | branches/locations |
| `restaurant_chains` | 378 | **brands** (53 multi-location, 325 solo) |
| `products` | 16,402 | 16,385 active + 17 soft-deactivated |
| `product_variations` | 20,643 | size/option price points |
| `canonical_dishes` | 1,431 | cross-brand compare identities |
| `food_types` / `food_sub_types` | 28 / 111 | browsing taxonomy |
| `cuisines` / `food_categories` / `flavor_tags` | 11 / 6 / 9 | lookups |
| `users` / `restaurant_reviews` / `product_reviews` | 0 / 0 / 0 | **no real users yet — pre-launch** |

Derived: **13,653 brand-dish cards** from 16,385 active products — i.e. ~17% of the catalogue was chain duplication, now collapsed at read time.

### Core tables

**`restaurant_chains`** — the brand. `chain_code` is a **unique URL-safe slug** (`bella-italia`) — this is the API's URL key, not the id. Named "chains" for historical reasons; it means *brand*, and every restaurant has one.

**`restaurants`** — a branch. `source_restaurant_code` is the natural key from the scrape. `chain_id` is **always populated** (never NULL). `old_rating`/`old_review_count` = the **foodpanda** scraped rating; `rating`/`review_count` = khawon's own (see §7 rating rules). `latitude`/`longitude` present for all 451.

**`products`** — a menu item at ONE branch. `source_product_id` is the natural key. Single-FK lookups for `category_id`/`cuisine_id`/`food_type_id`/`food_sub_type_id`, plus nullable `canonical_dish_id`. **`normalized_name`** is the read-time brand-grouping key (see §6). `is_active` + `last_seen_at` implement **soft delete**: a re-scrape that no longer sees an item sets `is_active=false`, **never DELETEs** — `product_reviews` cascades on delete, so a hard delete silently destroys user reviews on a dish that's just temporarily off the menu.

**`canonical_dishes`** — the cross-brand compare identity. `aliases[]` holds other observed spellings. Carries **majority-vote** `food_type`/`sub_type`/`cuisine`/`category` from its member products, so a dish doesn't flicker in/out of a filter when the per-product classifier disagrees across restaurants.

**Two parallel review stacks** — `restaurant_reviews` (overall experience, attached to a **branch**) and `product_reviews` (a dish at a branch). Each has `_photos` and `_votes` siblings. Both: account required, one-per-user (`UNIQUE(user_id, X)`), `status IN (pending|approved|rejected)`.

### Taxonomy (4 independent dimensions)

1. **Category** — meal role: Breakfast / Main Dish / Appetizer / Sides / Dessert / Drinks
2. **Food Type** — 28 deliberately COARSE umbrellas (Rice, Curry, Pizza, Beverages, Set Menu…) for browsing
3. **Food Sub Type** — the *natural* differentiator per type: protein for Curry, format for Rice (Biryani/Fried Rice/Tehari), prep for Dumpling (Fried/Steamed), drink kind for Beverages. Deliberately BROAD.
4. **Cuisine** — Bangladeshi / Indian / Chinese / Italian / Korean…; "Asian" is a catch-all only.

**Key classification principle — "format wins over garnish":** `"Tandoori Chicken Pizza"` → Pizza, not Grill. `"Chicken Seekh Kabab Roll"` → Wraps & Rolls, not Kebab. Implemented via **rule ORDER** in `FOOD_TYPE_RULES` (first-match-wins) in `classify_batch.py`. Several bugs came from getting this order wrong.

**Set Menu** is its own Food Type; `sub_type` = its primary component.

---

## 6. How brand cards work (the dedupe)

The thing that makes "one Domino's, not three" work.

- **The card is a GROUPING, not an entity.** There is **no `chain_dishes` table** and there shouldn't be. Name, price range, availability, and pooled rating are all *derived* from the per-branch `products` rows — which must exist anyway for per-branch reviews, availability, and future map pins.
- **No physical merge.** Duplicate rows are never deleted. Collapse happens **at read time**.
- **Key = `(chain_id, food_type_id, normalized_name)`.**

`food_type_id` in that key is **load-bearing, not decoration**: without it, a brand's "Chicken" *curry* fuses with its own "Chicken" *pizza*. The canonical bootstrap learned this the hard way; there's a regression test pinning it (`test_same_name_different_food_type_stays_separate`).

`normalized_name` is written by `load_batch` using **`canonical_match_key()`** — the *same* function the canonical bootstrap groups with. That's deliberate: both layers then agree on what "the same dish name" means, and brand dedupe inherits the spelling map (`biriyani`→`biryani`), stopword removal, and token sort for free.

**What a card carries** (`BrandDishOut`):
- `price_min_bdt` / `price_max_bdt` / `price_varies` — **always present**. When `price_varies` is false, min == max and the UI shows one number. One rule, no branching. (Only ~6.5% of multi-branch brand dishes genuinely differ in price.)
- `branch_count` / `brand_branch_total` → the "at 2 of 3 branches" badge. Suppress the badge when they're equal.
- `average_rating` / `review_count` — **pooled across branches**, approved-only.
- `brand: {id, slug, name}` — **link with `slug`**.

**Union, not intersection.** The brand menu shows every dish *any* branch sells, badged with availability. Intersection would silently delete ~1/3 of Domino's menu; union-without-a-badge would send someone to Uttara for a pizza only Gulshan sells — breaking the "trust" verb.

---

## 7. The API contract

**"Restaurant" means brand.** `{slug}` in these paths is the brand slug (`chain_code`).

### Brand surface — `routers/restaurants.py`

| Endpoint | Notes |
|---|---|
| `GET /restaurants/?q=&offset=&limit=` | **Paginated** browse → `{restaurants, total, offset, limit}`. 378 brands. |
| `GET /restaurants/{slug}` | Brand page: branches as tags, pooled rating |
| `GET /restaurants/{slug}/menu` | **Merged deduped** brand menu → `BrandDishOut[]` |
| `GET /restaurants/{slug}/dishes/{food_type_id}/{dish_slug}` | Brand dish + per-branch breakdown |
| `GET/POST /restaurants/{slug}/reviews` | Location reviews. POST body carries `branch_id` |
| `DELETE /restaurants/{slug}/reviews/{review_id}` | Own reviews only |

### Branch surface — `routers/branches.py`

| Endpoint | Notes |
|---|---|
| `GET /branches/?q=&offset=&limit=` | Paginated location list (admin) |
| `GET /branches/{branch_id}` | **Resolve** → `{chain_id, chain_slug, …}`. Old branch link → redirect to `/restaurants/{chain_slug}` |
| `POST/PUT/DELETE /branches/…` | Contribute/admin: create location, edit, photo |
| `GET /branches/{branch_id}/dishes` | ONE location's raw menu (admin/inspection) |

### Dish surface — `routers/dishes.py`, `routers/reviews.py`

| Endpoint | Notes |
|---|---|
| `GET /dishes/search?q=&offset=&limit=` | `canonical_matches[]` (compare strip, capped 10) + `dishes[]` (**paginated brand cards**). `total` counts cards. |
| `GET /dishes/compare/{canonical_dish_id}` | **One row per BRAND**, not per branch |
| `GET /dishes/{product_id}` | One branch's dish |
| `GET /dishes/{product_id}/reviews` | Paginated |
| `POST /reviews/ {dish_id, rating, comment}` | Dish review — targets ONE branch's product row |

### ⚠️ The URL rule (this bit people)

| Use | For |
|---|---|
| **`slug`** (`brand.slug`, or `id` from `/restaurants`… no — `slug`) | every `/restaurants/*` URL |
| **`id`** (chain_id) | POST bodies only. **Never in a URL.** |
| **branch-row id** (`BrandBranchOut.restaurant_id`, `/branches` list) | `/branches/*` only |

**Why slugs:** `restaurants.id` and `restaurant_chains.id` are separate serial sequences that **overlap**. When URLs were numeric, `/restaurants/296` returned *Bella Italia* (chain 296) while branch row 296 was *Pizzolo Caffe* — a **silent wrong-restaurant bug with no 404 to catch it**. A slug can't be mistaken for either id space, so the failure is impossible by construction rather than avoided by discipline. Slugs also survive the pipeline reloads that churn serial ids, so deep links stay valid. Numeric `/restaurants/{int}` now 404s — that's intentional.

### Rating rules (get this right or the UI lies)

- `average_rating` / `review_count` = **khawon's own**, computed live from **approved** reviews.
- `foodpanda_rating` / `old_rating` = the scraped rating.
- **`display_rating` + `display_review_count` + `display_rating_source`** = server-resolved: khawon's if it has reviews, else foodpanda as a cold-start fallback. **`display_review_count` is the count BEHIND `display_rating`, from the same source.**

> **Never pair `display_rating` with a count from a different source.** That exact bug shipped: the brand page rendered "4.9 · **0 reviews** (Foodpanda)" because it paired a foodpanda rating with the khawon review total (0). Rating, count, and source must always come from the same place.

- Stored `products.rating` / `restaurants.rating` columns are **reserved for future denormalization and NOT maintained**. Don't read them. Ratings compute on read (correct by construction; no stale aggregates). Revisit only if review volume demands it.
- **Post-moderation**: reviews insert `status='approved'` and are visible immediately; reads filter `approved`. To switch to strict pre-moderation: default to `pending` and build an approval queue.

---

## 8. Schema changes & the geo situation

**`schema.sql` builds FRESH databases. `migrations/` brings EXISTING ones up to date.** There is no Alembic — on purpose (Postgres-native features + a re-derivable catalogue). But "just reset and reload" **stops being safe the moment there's one real user**, so:

> **Adding a column? Add it to BOTH `schema.sql` AND a new numbered `migrations/NNN_*.sql`.** Every migration must be idempotent (`IF NOT EXISTS`). Apply: `psql "$DATABASE_PUBLIC_URL" -f migrations/001_....sql`

**Geo / "near me" is deferred.** `schema_geo.sql` holds `CREATE EXTENSION postgis` + a generated `geog GEOGRAPHY` column + a GiST index. **Railway's Postgres has no PostGIS** (verified: `postgis available: False`), so `schema.sql` was made portable — geo is an **optional add-on**, not a migration. Raw `latitude`/`longitude` are populated for all 451 branches, so the distance math is ready the day you move to a PostGIS host. `schema_geo.sql` is additive; nothing else changes.

---

## 9. ⚠️ Traps (each of these cost real debugging time)

### 1. `load_batch.py` has FIVE hardcoded column lists

Adding a column to `prod_values` requires touching **all five** or it fails **silently**:

1. `prod_values` (the dict)
2. `_prod_signature` (change detection)
3. `_prod_signature_from_row` (change detection)
4. the `existing_rows` SELECT
5. `_bulk_update_products_unnest` (raw SQL: SET clause + CAST array + `d(...)` alias + params — all positional)

**Miss #2–4** → the row compares "unchanged", the write is skipped, and the column **can never be backfilled** no matter how many reloads. **Miss #5** → the load cheerfully reports `16402 updated` while writing nothing. Both happened, in sequence, on one column. Guards: `test_bulk_update_actually_persists_normalized_name` (round-trips through a real DB — catches a stale list wherever it lives) and `test_signature_row_and_dict_forms_agree` (catches the inverse: drift makes every row compare "changed" forever, rewriting 16k rows each load).

### 2. `load_dotenv` hijacks your test database

`database.py` calls `load_dotenv()`, which re-reads `DATABASE_PUBLIC_URL` from `.env` — and **that wins over `DATABASE_URL`**. To point anything at a temp DB you must **SET** `os.environ["DATABASE_PUBLIC_URL"]` (load_dotenv won't override an already-set var). **Popping it is not enough** — do that and you silently run against the **real Railway database**. (This happened: it polluted production with test rows.) Use the existing `temp_db` fixture in `tests/conftest.py`; don't roll your own. Also `engine.dispose()` before `DROP DATABASE`, or it fails as in-use.

### 3. Don't put backticks in `git commit -m "..."`

Bash command-substitutes them: a message saying `` `id` `` became `uid=197609(shoha) gid=…` in a real commit. **Always use `git commit -F - <<'EOF'`** (quoted heredoc).

### 4. Serial ids churn on reload

The catalogue is re-derived; serial ids are not stable across loads. Never persist or deep-link a serial id. Natural keys (`chain_code`, `source_product_id`, `source_restaurant_code`, dish slugs) are stable — use those.

### 5. `chain_id` from the source is ~21% wrong

foodpanda's `chain_code` **splits** brands (Waffle Up across two codes; also Thai Bistro, New Hanif Biryani, Cafe Mario's, Tehari Ghar, Bhorta Bari) and **misses** them entirely (Habanero, Happy Potato, Hungry Pizza Lovers untagged). `bootstrap_chains.py` groups by **normalized name** instead and treats `chain_code` as a *signal only*. `BRAND_OVERRIDES` (code → slug) pins exceptions — same slug forces a merge, different slug forces a split. It's currently **empty**: normalization got all 53 groups right, owner-reviewed.

### 6. Brand display names come from BRANCH names, not `chain_name`

foodpanda's `chain_name` is contaminated — it named `"Rice Lab - Mirpur"` for a brand whose only branches are Uttara and Gulshan (Mirpur isn't one of them). `strip_location()` strips the location from branch names **case-preservingly**, so `KOI Thé`, `Domino's Pizza`, `Greens & Seeds` survive intact where the lowercased match key gives unusable text (`koi th`).

### 7. Don't push `.limit()` into a query with `joinedload`ed collections

`_product_query` joinedloads `variations` and `flavor_tags`. SQL `LIMIT` + a joined collection truncates **wrong rows**. Current code paginates in Python or via a subquery of ids on purpose. Leave it.

### 8. A partial `restaurants_glob` used to corrupt out-of-batch variations

`load_batch` deletes `product_variations` / `product_flavor_tags` scoped to the current batch (`restaurant_id = ANY(batch_rest_ids)`), but the rebuild loop resolved product ids through `prod_id_by_spid`, seeded from **every** product row in the DB. So a product whose restaurant wasn't in the glob got its variations re-inserted having never been deleted → `UniqueViolation on (product_id, label)`. Hidden for months because every real load passed a glob covering all 451 restaurants; it surfaced on 2026-07-17 when `consolidated.json` was reloaded with a Dhanmondi-only glob. **Fixed** — the rebuild loop now skips products whose restaurant isn't in the batch (widening the delete would wipe restaurants the batch never loaded). Pinned by `tests/test_load_batch_partial_glob.py`.

> **Related, still open:** an *empty* glob (matches zero files) is a silent no-op — `load_batch` logs `0 restaurants`, skips every product as `skipped_no_restaurant`, and reports `0 new, 0 changed, 0 unchanged` without erroring. On Git Bash the cause is usually a POSIX path (`/c/Users/...`): bash converts plain arguments but not the unexpanded `*` glob string, so Python's `glob.glob` matches nothing. Pass a **native Windows path** (`C:/Users/...`) and eyeball the `N restaurants` line. A fail-fast guard for the zero-match case is not yet in the tree.

---

## 10. Running it

```bash
# ---- backend ----
cd khawon-api
pip install -r requirements.txt
# .env needs DATABASE_PUBLIC_URL (Railway) or DATABASE_URL
python -m uvicorn main:app --port 8000            # → http://localhost:8000/docs

python -m pytest tests/                            # 73 tests. Needs DB access:
                                                   # the temp_db fixture creates+drops
                                                   # a throwaway `khawon_test` DB on the
                                                   # same server, so runs take ~3 min.

# ---- frontend ----
cd khawon-web
npm install
npm run dev          # → http://localhost:5173 ; expects API at localhost:8000 (.env)
npm run typecheck
```

### Re-running the pipeline (data folder: `strip data/code/`)

```bash
python classify_batch.py <input.json> <out.json>
python consolidate_variants.py                       # → v2_output/consolidated.json
python bootstrap_chains.py --restaurants "v2_output/restaurants_*_restaurants.json" \
                          --out v2_output/chains.json
python bootstrap_chains.py --restaurants "..." --review    # human review list, writes nothing
python bootstrap_canonical_dishes.py v2_output/canonical_dishes.json \
       --input v2_output/consolidated.json --chains v2_output/chains.json

# load (idempotent upsert — NOT a reset). Area is derived per-file.
cd khawon-api
python load_batch.py "$D/consolidated.json" "$D/canonical_dishes.json" \
       "$D/restaurants_*_restaurants.json" --chains "$D/chains.json"
```

Windows: prefix ad-hoc Python with `PYTHONIOENCODING=utf-8` (Bengali/emoji in the data).

---

## 11. Deferred / next

| Item | Notes |
|---|---|
| **Frontend build-out** | Owner-driven. Contract is stable; brand cards + badges are live. |
| **Google Place IDs** | `google_place_id` is **0 for all 451**. `match_google_places.py` output was never merged into the v2 restaurant JSONs. Deferred to the map feature; coords already work. |
| **Near-me / geo** | Apply `schema_geo.sql` on a PostGIS host. Then swap brand rating from weighted-average to nearest-branch. |
| **Fuzzy/semantic matching** | pgvector embeddings to unify spelling variants the exact key misses AND power craving/semantic search — same capability, dual use. |
| **Flavor tags** | Populated (9 tags, ~14.9k product links, verified 2026-07-17) but coarse — only 9 slugs. Richer tagging is future classifier work. |
| **Moderation queue** | Only if switching off post-moderation. |
| **Food-type images** | `food_types` is a bare `(id, name)` lookup — the v1 image/description columns were dropped. Photo upload endpoint returns **501**. Restore by re-adding columns if the UI needs it. |

---

## 12. Where to read next

- `docs/superpowers/specs/2026-07-15-chain-brand-model-design.md` — **the WHY.** Every brand-model decision (D1–D14) with its rationale and the data behind it. Read this before touching the brand/canonical layers.
- `docs/superpowers/plans/` — historical implementation plans. Useful for archaeology, not current state.
- `schema.sql` — heavily commented; the comments explain *why*, not just *what*.
- `tests/` — the regression tests encode the traps in §9. If one fails, you've rediscovered a bug someone already paid for; fix the code, don't edit the test to match.
