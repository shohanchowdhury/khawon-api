# Chain Restaurants — Brand Model Design

**Date:** 2026-07-15
**Status:** Approved design, ready for implementation planning
**Scope:** khawon-api (backend + pipeline). Frontend is designed separately by the owner.

---

## 1. Problem

A chain's branches each carry their own copy of the menu, so the same dish appears once per branch. Searching "Margherita" returns Domino's three times — same pizza, same price, three cards. This is the duplicate clutter that motivated the work.

The owner's requirements:

1. A chain should present **one menu**.
2. **Individual (per-branch) reviews** must survive.
3. **Per-branch map locations / directions** must be possible later.

### What the data actually says

Measured against the loaded Railway DB (451 restaurants, 16,402 active products):

| Fact | Value |
|---|---|
| Restaurants with a `chain_id` | 251 of 451 |
| Chains in `restaurant_chains` | 189 (only 44 have >1 location) |
| Real multi-location brands (by name normalization) | 52, covering 120 restaurants |
| **Standalone restaurants** | **331 (73%)** |
| Dish-name groups at 2+ branches of one chain | 1,692 |
| — with **identical price** across branches | 1,634 (**97%**) |
| — with differing price | 58 (3%) |
| Dish names at only ONE branch of their chain | 6,268 |
| **Redundant product rows within chains** | **2,543** (24% of chained rows, ~15% of catalogue) |

Two findings reshaped the design:

**Finding 1 — chains do NOT have identical menus.** Domino's runs **71 / 75 / 60** products across its three branches. 6,268 dish names exist at only one branch of their chain. The stated goal ("chains have the same menu") is false in the data; the design must handle partial availability rather than assume uniformity.

**Finding 2 — `chain_id` is untrustworthy (~21% wrong).** It comes from foodpanda's `chain_code`. It **splits** brands (`Waffle Up` across ids 85 and 228; `New Hanif Biryani` across 91 and 118; `Thai Bistro` across 218 and 222; `Cafe Mario's` across 227 and 232) and **misses** them entirely (`Hungry Pizza Lovers`, `Happy Potato Bangladesh` untagged; `Indian Kitchen`, `Rice & More` half-tagged). 11 of ~52 real brands are wrong. Grouping by `chain_id` today would silently mis-merge and mis-split.

---

## 2. Core insight: two layers, previously conflated

| Layer | Job | Example |
|---|---|---|
| **Chain (brand) grouping** | Dedupe *within* one brand | Domino's Margherita ×3 branches → 1 card |
| **Canonical dish** | Compare *across* brands | Chicken Biryani at 7 different brands |

`canonical_dishes` currently promotes a dish on "2+ distinct **restaurants**", which counts branches. So a chain-exclusive drink sold at 3 branches of one brand (`Peach Apple Fizz`, `Iced Orange Americano`) qualifies as "comparable" — but nobody else makes it. There is nothing to compare.

**1,008 of 2,527 linked canonical dishes (40%) are single-brand.** They were the chain layer's job, misfiled into the canonical layer. Moving them is a correction, not a loss — and because search already surfaces non-canonical dishes, they remain fully findable.

---

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Brand card in search now; nearest-branch (geo) later** | Same data model; geo layers on top when near-me ships. |
| D2 | **No physical merge.** Per-branch product rows stay; collapse at read time | Per-branch reviews, availability, and future map pins all require the rows. The "merge them" instinct is incompatible with the owner's own requirements. |
| D3 | **Union + availability badge** ("at 2 of 3 branches") | Intersection would delete ~1/3 of Domino's menu; union-without-badge sends users to a branch that lacks the dish, breaking the "trust" leg of search/compare/trust. |
| D4 | **Price: single value when uniform; `from ৳X` / range when it varies** | 97% are uniform. The 3% get a range on the card and an exact price on branch drill-down. |
| D5 | **Dish reviews: pooled headline + per-branch breakdown** | Splitting an already-empty review pool 3 ways worsens cold-start. Breakdown preserves "individual reviews". |
| D6 | **Brand overall rating = review-count-weighted average across branches** | "Nearest branch's rating" needs geo, which is deferred. Swappable to nearest-branch when geo lands. Existing khawon→foodpanda fallback applies, weighted across branches. |
| D7 | **Chain identity: normalize to propose, human confirms, overrides file** | `chain_id` is 21% wrong. Auto-grouping alone risks invisible false merges, which fuse both menus *and* reviews. At 451 restaurants a one-time human pass (~52 groups) is feasible — that window closes as more areas are scraped. |
| D8 | **Chain mapping lives in the pipeline, not DB rows** | The DB is reset and reloaded from `v2_output`. Hand-edited DB rows would be wiped on the next reload. Mirrors `FOREIGN_DISH_OVERRIDES`. |
| D9 | **Compare shows one row per brand.** Canonical promotion becomes "2+ distinct **brands**" | Comparing a dish to itself across branches is not comparison. Accepts the 40% canonical drop (2,527 → ~1,519). |
| D10 | **Every restaurant gets a `chain_id`** (standalone = brand of one) | Makes "brand" always `chain_id`: a plain indexable `GROUP BY`, no `COALESCE('c'||chain_id,'r'||id)` string keys. Eliminates all `if chain else` special-casing. `restaurant_chains` already holds 189 chains of which only 44 are multi-location. |
| D11 | **No `chain_dishes` table.** Add `products.normalized_name`; group at read time | The brand card is a *grouping*, not an entity. `canonical_dishes` stays the only precomputed dish entity because cross-brand identity needs curation; intra-brand dedupe does not. |
| D12 | **Brand-dish key = `(chain_id, food_type_id, normalized_name)`** | `food_type_id` is required, not decoration: without it a brand's "Chicken" curry fuses with its "Chicken" pizza. The canonical bootstrap already learned this exact lesson. |
| D13 | **Natural key in URLs, not a serial id** | Serial ids churn on every reload; the natural key survives, so deep links stay valid. |
| D14 | **Restaurant browse becomes brand-level** | One Domino's with "3 locations"; the branch list feeds the future directions view. |

### Non-goals

- Geo / near-me / nearest-branch (deferred; needs PostGIS, absent on Railway — see `schema_geo.sql`).
- Fuzzy/semantic brand or dish matching (pgvector) — exact normalization only, consistent with the canonical bootstrap.
- Renaming `restaurant_chains` → `brands` (pure churn).
- Physically deleting duplicate product rows.

---

## 4. Architecture

```
bootstrap_chains.py  ──► chains.json ──► load_batch.py ──► restaurants.chain_id
      (+ CHAIN_OVERRIDES)                                   restaurant_chains
                                                                   │
                                          brand = chain_id (always populated)
                                                                   │
                    ┌──────────────────────────────────────────────┴───────────┐
                    ▼                                                          ▼
        Chain grouping (read-time)                             Canonical dishes (precomputed)
   GROUP BY chain_id, food_type_id,                        promoted on 2+ distinct BRANDS
            normalized_name                                 restaurant_count = brand count
   → brand card: price range, availability,
     pooled rating
```

Both layers key off one trustworthy brand identity. Brand identity is therefore built first.

### 4.1 Brand identity (pipeline)

New `bootstrap_chains.py`, alongside the existing bootstrap scripts:

- **Input:** `v2_output/restaurants_*_restaurants.json` (name, `source_restaurant_code`, `chain_code`, `chain_name`).
- **Normalize:** lowercase; strip ` - <Area>` suffix; strip area tokens (Dhanmondi, Gulshan, Uttara, Banani, …); strip punctuation; collapse whitespace.
- **Group** by normalized name → candidate brands. `chain_code` is a **signal**, never truth.
- **`CHAIN_OVERRIDES`:** force-merge and force-split rules for exceptions (`Waffle Up` 85+228, `New Hanif Biryani` 91+118, `Thai Bistro` 218+222, `Cafe Mario's` 227+232, plus any false merge found in review).
- **Output:** `chains.json` — brand slug, display name, member `source_restaurant_code`s. Also prints the ~52 candidate groups for a one-time human eyeball.
- Deterministic and re-runnable, like every other pipeline stage.

`load_batch.py` consumes `chains.json`: upserts `restaurant_chains` (`chain_code` = brand slug), sets `restaurants.chain_id` for **every** restaurant (standalone → brand of one).

**Growth path:** when a new area is scraped and a standalone gains a second location, the next `bootstrap_chains` run regroups it automatically. No migration, no backfill — brands are derived, not hand-maintained.

### 4.2 Schema change (exactly one column)

```sql
ALTER TABLE products ADD COLUMN normalized_name TEXT;
CREATE INDEX idx_products_normalized_name ON products(normalized_name);
```

- Populated by `load_batch.py` using the **same** normalization function the canonical bootstrap uses (one source of truth for normalization).
- Unlocks both layers: chain grouping becomes `GROUP BY r.chain_id, p.food_type_id, p.normalized_name`; the canonical bootstrap stops recomputing names offline.
- Existing `idx_restaurants_chain` covers the join side.

No other schema change. `restaurant_chains` and `restaurants.chain_id` already exist.

### 4.3 Canonical rule change (pipeline, no schema)

`bootstrap_canonical_dishes.py`: promotion changes from "2+ distinct restaurants" to "2+ distinct **brands**"; `restaurant_count` counts brands.

---

## 5. API contract

`DishOut` (one product = one branch) becomes a **brand-grouped card**. A standalone restaurant is a brand with one branch, so solo cards render exactly as today — one shape, no special-casing.

```
BrandDishOut:
  brand: {id, name}                      # chain_id + display name
  name, image_url, description
  food_type, category_raw, cuisines[], flavor_tags[]
  canonical_dish_id                      # nullable, as today
  price_min_bdt, price_max_bdt, price_varies   # always present; when
                                         # price_varies=false, min==max and the
                                         # UI shows one number. No separate
                                         # price_bdt field — one rule, no branch.
  branch_count, brand_branch_total       # -> "at 2 of 3 branches"
  average_rating, review_count           # pooled across branches (D5)
  display_rating, display_rating_source  # existing khawon->foodpanda fallback
```

**Endpoints:**

| Endpoint | Change |
|---|---|
| `GET /dishes/search` | `dishes[]` returns brand cards (Domino's Margherita once). Canonical strip unchanged. |
| `GET /dishes/compare/{canonical_id}` | One row **per brand**, not per branch. |
| `GET /brands/{chain_id}` | **New.** Brand page: branch list (addresses now, map pins later), pooled rating + per-branch breakdown. |
| `GET /brands/{chain_id}/dishes/{food_type_id}/{slug}` | **New.** Brand dish detail: per-branch prices, availability, per-branch review breakdown. `food_type_id` is in the path because the brand-dish key includes it (D12) — a `{chain_id, slug}`-only URL would collide a brand's "Chicken" curry with its "Chicken" pizza. `slug` = slugified `normalized_name`. |
| `GET /dishes/{product_id}` | **Unchanged.** A specific branch's dish. |
| `POST /reviews {dish_id}` | **Unchanged.** You review the dish you ate, at the branch you ate it. Pooling is read-side only. |
| `GET /restaurants` | Brand-level (one Domino's, "3 locations"). |

---

## 6. Edge cases

| Case | Behavior |
|---|---|
| Standalone restaurant (73% of catalogue) | `branch_count=1`, `brand_branch_total=1`; badge suppressed; `price_varies=false`; pooled rating = its own. Renders as today. |
| Dish at some branches | `branch_count < brand_branch_total` → availability badge. |
| Price disagrees (3%) | `price_varies=true`, "from ৳X"; exact price on branch drill-down. |
| Sold out at one branch | Still shown; sold-out is per-branch detail. |
| Same normalized name, different food types within a brand | Kept apart by `food_type_id` in the key (D12). |
| Single-brand dish (was canonical) | Loses `canonical_dish_id`; still searchable as a flat dish; deduped by the chain layer. |
| Solo restaurant gains a 2nd branch later | Regrouped automatically on next pipeline run. |

---

## 7. Phasing

Each phase is independently shippable.

1. **Brand identity** — `bootstrap_chains.py` + `CHAIN_OVERRIDES` + `load_batch` populates `chain_id` for all. Foundation; no user-visible change.
2. **Canonical → brands** — promotion rule + `restaurant_count` count brands. Fixes compare inflation (2,527 → ~1,519).
3. **Brand-grouped search + brand card** — `products.normalized_name`, read-time grouping, availability, price range. Kills the duplicates.
4. **Brand-level review pooling** — pooled headline + per-branch breakdown (D5/D6).
5. *Deferred:* geo / nearest branch (needs PostGIS; `schema_geo.sql`).

Ordering rationale: identity first, because both layers key off it and grouping on 21%-wrong identity would ship visible mis-merges.

---

## 8. Impact / migration

- **Canonical dishes: 2,527 → ~1,519 linked** (1,008 single-brand dishes unlink). They remain searchable as flat dishes.
- **`restaurant_count` shrinks** for 1,416 canonical dishes (now counts brands). Any UI copy showing "at N restaurants" now means brands.
- **`restaurant_chains` grows to ~383 rows** (331 brands of one + ~52 multi-location brands), most being brands of one. Acceptable; the table already holds 189 chains of which only 44 are multi-location.
- **`chain_code` semantics change** from foodpanda's code to our brand slug. Derived data, rebuilt on reload.
- No data loss: no product rows are deleted; `canonical_dish_id` unlinking is recomputed by the bootstrap, not destructive.

---

## 9. Testing

**Pipeline (unit):**
- Normalization: ` - Area` suffix and area tokens stripped; punctuation collapsed.
- Overrides applied: `Waffle Up` (85+228) → one brand; `Thai Bistro` (218+222) → one; `New Hanif Biryani` (91+118) → one; `Hungry Pizza Lovers` groups despite null `chain_id`.
- **False-merge guard:** known-distinct restaurants do NOT fuse.
- Every restaurant ends with a `chain_id`; determinism (same input → same `chains.json`).

**Canonical (unit):**
- 2+ brands rule; a 3-branch single-brand dish drops out and remains searchable as a flat dish.
- A dish shared by two standalone restaurants still qualifies.

**API (smoke, isolated temp DB):**
- Seed a 3-branch chain sharing a dish → search returns **one** card, `branch_count=3`.
- Differing branch prices → `price_varies=true` with correct range.
- Dish at 2 of 3 branches → correct availability.
- Pooled rating equals the average across branches; per-branch breakdown correct.
- Compare returns one row per brand.
- **Regression:** solo restaurant renders `branch_count=1`, badge suppressed.

> Temp-DB harness note: `database.py` calls `load_dotenv()`, which re-reads `DATABASE_PUBLIC_URL` from `.env` and it wins over `DATABASE_URL`. To point a test at a temp DB you must **set** `os.environ['DATABASE_PUBLIC_URL']` to the temp URL (not pop it), or the test hits the real Railway DB. Dispose the engine before `DROP DATABASE`.

---

## 10. Open items

- The one-time human review of the ~52 candidate brand groups (owner) — feeds `CHAIN_OVERRIDES`.
- Frontend: brand card, availability badge, "from ৳X", per-branch breakdown (owner).
