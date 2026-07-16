# Brand Cards + Review Pooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse a chain's duplicate dishes into one brand card — Domino's Margherita once, not three times — with a price range, an "at 2 of 3 branches" badge, and reviews pooled across branches.

**Architecture:** Products keep their per-branch rows (per-branch reviews, availability and future map pins all need them). A new `products.normalized_name` column lets the API group them at read time by `(chain_id, food_type_id, normalized_name)` into a brand card. No `chain_dishes` table: the card is a grouping, not an entity.

**Tech Stack:** Python 3.12, pytest 7.4.4, SQLAlchemy 2.0, psycopg2, PostgreSQL 18 (Railway, no PostGIS), FastAPI, Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-07-15-chain-brand-model-design.md` (phases 3-4)
**Predecessor:** `docs/superpowers/plans/2026-07-15-brand-identity-and-canonical-brands.md` (phases 1-2, DONE)

## Global Constraints

- **Repo:** `C:\Users\shoha\Documents\GitHub\Khawon\khawon-api`, branch `main` (10 unpushed commits from phases 1-2).
- **Phases 1-2 are live and must not regress:** every restaurant has a `chain_id` (standalone = brand of one), `restaurant_chains.chain_code` holds brand slugs, canonical dishes promote on 2+ brands. Live DB: 451 restaurants, 378 brands (53 multi-location), 1,431 canonical dishes, 16,402 active products, 0 null `chain_id`.
- **Brand = `chain_id`, always.** Never `COALESCE(chain_id, id)`; phase 1 guarantees it is populated. A standalone restaurant is a brand of one and must flow through every rule unchanged — no `if chain else` branches.
- **Repo copies of pipeline scripts are authoritative.** `bootstrap_chains.py`, `bootstrap_canonical_dishes.py`, `consolidate_variants.py` also exist in `C:\Users\shoha\OneDrive\Desktop\strip data\code\` (identical apart from CRLF). Edit the repo copy, then copy to the data folder.
- **Data directory:** `C:\Users\shoha\OneDrive\Desktop\strip data\code\v2_output\` (`consolidated.json`, `canonical_dishes.json`, `chains.json`, `restaurants_*_restaurants.json`). Pass paths via CLI args, never hardcode in a module.
- **Test DB gotcha:** `database.py` calls `load_dotenv()`, which re-reads `DATABASE_PUBLIC_URL` from `.env` and it **wins over** `DATABASE_URL`. The `temp_db` fixture in `tests/conftest.py` already handles this by SETTING both — use that fixture, never roll your own. `engine.dispose()` before `DROP DATABASE`.
- **Windows:** prefix ad-hoc Python with `PYTHONIOENCODING=utf-8`.
- **Do not regress fuzzy canonical matching** (`SequenceMatcher` @ 0.92, spelling maps, protein signatures) in `bootstrap_canonical_dishes.py`.
- **Frontend is the owner's job.** This plan changes the API contract; do not touch `khawon-web`.

---

### Task 1: Add products.normalized_name (schema + live migration)

`schema.sql` is the source of truth for **fresh** databases, but the live Railway DB already holds data and there is no Alembic. Reset+reload works only while `users`/`product_reviews` are empty — it stops being an option the moment there is one real user. So a numbered migration file is introduced here, matching the `schema_geo.sql` precedent of a standalone `.sql` applied deliberately.

**Files:**
- Modify: `schema.sql` (products table + index list)
- Create: `migrations/001_products_normalized_name.sql`
- Create: `migrations/README.md`
- Test: `tests/test_schema_normalized_name.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `products.normalized_name TEXT` + `idx_products_normalized_name`, present in both a fresh `schema.sql` database and the live one.

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_normalized_name.py`:

```python
from sqlalchemy import text


def test_products_has_normalized_name_column(temp_db, db_session):
    """The temp DB is built from schema.sql, so this proves fresh databases get
    the column."""
    row = db_session.execute(text(
        "select data_type from information_schema.columns "
        "where table_name='products' and column_name='normalized_name'"
    )).first()
    assert row is not None, "products.normalized_name missing from schema.sql"
    assert row[0] == "text"


def test_normalized_name_is_indexed(temp_db, db_session):
    idx = {r[0] for r in db_session.execute(text(
        "select indexname from pg_indexes where tablename='products'"
    ))}
    assert "idx_products_normalized_name" in idx
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd khawon-api && python -m pytest tests/test_schema_normalized_name.py -v`
Expected: FAIL — `AssertionError: products.normalized_name missing from schema.sql`

- [ ] **Step 3: Add the column to schema.sql**

In `schema.sql`, inside `CREATE TABLE products`, add after the `canonical_dish_id` line:

```sql
    -- Match key for read-time brand grouping: the API collapses a chain's
    -- branches into one card via (chain_id, food_type_id, normalized_name).
    -- Written by load_batch using canonical_match_key(), the SAME function the
    -- canonical bootstrap groups with, so both layers agree on what "the same
    -- dish name" means (and brand dedupe inherits its spelling map).
    normalized_name         TEXT,
```

And with the other product indexes:

```sql
CREATE INDEX idx_products_normalized_name ON products(normalized_name);
```

- [ ] **Step 4: Write the live migration**

Create `migrations/001_products_normalized_name.sql`:

```sql
-- Adds products.normalized_name (read-time brand grouping key).
-- schema.sql already contains this column for fresh databases; this file
-- brings an EXISTING database up to date. Idempotent - safe to re-run.
-- Apply:  psql "$DATABASE_PUBLIC_URL" -f migrations/001_products_normalized_name.sql
-- Then re-run load_batch.py to populate the column.

BEGIN;

ALTER TABLE products ADD COLUMN IF NOT EXISTS normalized_name TEXT;
CREATE INDEX IF NOT EXISTS idx_products_normalized_name ON products(normalized_name);

COMMIT;
```

Create `migrations/README.md`:

```markdown
# Migrations

`schema.sql` is the source of truth and builds a **fresh** database. These
numbered files bring an **existing** database up to the same shape.

There is no Alembic here on purpose: the schema uses Postgres-native features
the ORM cannot express, and the catalogue is re-derivable from the pipeline.
But "just reset and reload" stops being safe once real users and reviews
exist, so schema changes get a migration file from now on.

Apply in numeric order, once per database:

    psql "$DATABASE_PUBLIC_URL" -f migrations/001_products_normalized_name.sql

Every file must be idempotent (`IF NOT EXISTS`) so a re-run is a no-op.
When adding a column, add it to BOTH schema.sql and a new migration file.
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_schema_normalized_name.py -v`
Expected: 2 passed. (The `temp_db` fixture rebuilds from `schema.sql`, so this proves the fresh-DB path.)

- [ ] **Step 6: Apply the migration to the live database**

```bash
cd "C:/Users/shoha/Documents/GitHub/Khawon/khawon-api"
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'.')
from sqlalchemy import text
from database import engine
with engine.begin() as c:
    c.execute(text(open('migrations/001_products_normalized_name.sql', encoding='utf-8').read()))
with engine.connect() as c:
    print('column:', c.execute(text(\"select column_name from information_schema.columns where table_name='products' and column_name='normalized_name'\")).scalar())
    print('populated rows:', c.execute(text('select count(*) from products where normalized_name is not null')).scalar())
"
```

Expected: `column: normalized_name`, `populated rows: 0` (Task 2 fills it).

- [ ] **Step 7: Commit**

```bash
git add schema.sql migrations/ tests/test_schema_normalized_name.py
git commit -m "feat(schema): add products.normalized_name for brand grouping"
```

---

### Task 2: load_batch populates normalized_name

**Files:**
- Modify: `load_batch.py` (imports, `prod_values`)
- Test: `tests/test_load_batch_normalized_name.py`

**Interfaces:**
- Consumes: `canonical_match_key(name) -> str` from `bootstrap_canonical_dishes`.
- Produces: every active product row has `normalized_name` set. Tasks 3+ group on it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_batch_normalized_name.py`:

```python
def test_prod_values_sets_normalized_name():
    """The loader must use the same key the canonical bootstrap groups with, so
    brand dedupe and canonical grouping agree on 'the same dish name'."""
    from bootstrap_canonical_dishes import canonical_match_key
    assert canonical_match_key("Chicken Biriyani") == canonical_match_key("Chicken Biryani")


def test_branches_of_a_brand_share_a_normalized_name():
    from bootstrap_canonical_dishes import canonical_match_key
    # same dish, two branches, trivial spelling drift
    assert canonical_match_key("Margherita") == canonical_match_key("margherita")
    # size prefix stripped
    assert canonical_match_key("1:1 - Margherita") == canonical_match_key("Margherita")


def test_different_dishes_do_not_share_a_key():
    from bootstrap_canonical_dishes import canonical_match_key
    assert canonical_match_key("Chicken Biryani") != canonical_match_key("Beef Biryani")
```

- [ ] **Step 2: Run to verify it passes**

Run: `python -m pytest tests/test_load_batch_normalized_name.py -v`
Expected: 3 passed — these characterize `canonical_match_key`, which already exists. They exist to pin the behaviour the loader depends on before wiring it up.

- [ ] **Step 3: Wire it into load_batch**

In `load_batch.py`, add the import near the top:

```python
from bootstrap_canonical_dishes import canonical_match_key
```

In `prod_values(p, rid)`, add to the returned dict (next to `"name"`):

```python
                # Read-time brand grouping key; same function the canonical
                # bootstrap groups with, so both layers agree.
                "normalized_name": canonical_match_key(p.get("name", "")),
```

- [ ] **Step 4: Reload and verify population**

```bash
cd "C:/Users/shoha/Documents/GitHub/Khawon/khawon-api"
export PYTHONIOENCODING=utf-8
D="C:/Users/shoha/OneDrive/Desktop/strip data/code/v2_output"
python load_batch.py "$D/consolidated.json" "$D/canonical_dishes.json" "$D/restaurants_*_restaurants.json" --chains "$D/chains.json"
```

Note: `load_batch` only writes rows whose values CHANGED. Adding a field makes every row differ, so expect ~16,402 updated (not "unchanged") on this run. Then:

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'.')
from sqlalchemy import text
from database import engine
with engine.connect() as c:
    print('null normalized_name (want 0):', c.execute(text('select count(*) from products where is_active and normalized_name is null')).scalar())
    print('Domino Margherita rows sharing a key:', [tuple(r) for r in c.execute(text(
      \"select p.normalized_name, count(*) from products p join restaurants r on r.id=p.restaurant_id \"
      \"join restaurant_chains rc on rc.id=r.chain_id where rc.chain_code='domino-s-pizza' \"
      \"and p.name ilike 'margherita' group by p.normalized_name\"))])
"
```

Expected: `null normalized_name: 0`, and Margherita showing one key across 3 rows.

- [ ] **Step 5: Commit**

```bash
git add load_batch.py tests/test_load_batch_normalized_name.py
git commit -m "feat(pipeline): populate products.normalized_name on load"
```

---

### Task 3: BrandDishOut schema + grouping helper

**Files:**
- Modify: `schemas.py` (new `BrandDishOut`, `BrandBranchOut`)
- Create: `brand_dishes.py`
- Test: `tests/test_brand_dishes.py`

**Interfaces:**
- Consumes: `models.Product`, `models.Restaurant`, `resolve_display_rating` from `restaurant_reviews`.
- Produces:
  - `schemas.BrandDishOut` (fields below).
  - `brand_key(product) -> tuple[int, int | None, str]` = `(chain_id, food_type_id, normalized_name)`.
  - `build_brand_dishes(db, products: list[models.Product]) -> list[schemas.BrandDishOut]` — groups the given products and pools their reviews. Tasks 4-7 all call this.

- [ ] **Step 1: Add the schemas**

In `schemas.py`, after `DishOut`:

```python
class BrandOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class BrandBranchOut(BaseModel):
    """One branch serving a brand dish."""
    restaurant_id: int
    restaurant_name: str
    area: Optional[str] = None
    product_id: int          # the branch's own dish row; review it via POST /reviews
    price_bdt: float
    is_sold_out: bool = False
    average_rating: Optional[float] = None
    review_count: int = 0


class BrandDishOut(BaseModel):
    """A dish as one brand serves it, collapsing that brand's branches into a
    single card. A standalone restaurant is a brand of one, so its card is
    identical in shape (branch_count == brand_branch_total == 1)."""
    brand: BrandOut
    food_type_id: Optional[int] = None
    slug: str                       # slugified normalized_name; with brand.id +
                                    # food_type_id this is the card's natural key
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    category_raw: Optional[str] = None
    food_type: Optional[FoodTypeOut] = None
    cuisines: list[CuisineOut] = []
    flavor_tags: list[FlavorTagOut] = []
    canonical_dish_id: Optional[int] = None
    # Always present. When price_varies is False, min == max and the UI shows
    # one number -- one rule, no branching.
    price_min_bdt: float
    price_max_bdt: float
    price_varies: bool = False
    branch_count: int               # branches of this brand serving the dish
    brand_branch_total: int         # branches this brand has overall
    is_sold_out_everywhere: bool = False
    # Pooled across the brand's branches (spec D5).
    average_rating: Optional[float] = None
    review_count: int = 0
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_brand_dishes.py`:

```python
import itertools

import pytest

# Explicit counter: source_product_id is UNIQUE, and hash() on a str is salted
# per process (PYTHONHASHSEED), so hash-derived ids collide at random.
_pid = itertools.count(9000)


def _seed(db, brand_slug, brand_name, branches, dish_name, prices, food_type_id=None):
    """branches: list of (code, restaurant_name). prices: list aligned to branches;
    a None price means that branch does NOT sell the dish."""
    import models
    chain = models.RestaurantChain(chain_code=brand_slug, name=brand_name)
    db.add(chain)
    db.flush()
    prods = []
    for (code, rname), price in zip(branches, prices):
        r = models.Restaurant(source_restaurant_code=code, name=rname, chain_id=chain.id)
        db.add(r)
        db.flush()
        if price is None:
            continue
        p = models.Product(source_product_id=next(_pid),
                           restaurant_id=r.id, name=dish_name, base_price_bdt=price,
                           normalized_name=dish_name.lower(), food_type_id=food_type_id)
        db.add(p)
        prods.append(p)
    db.commit()
    return chain, prods


def test_three_branches_collapse_to_one_card(temp_db, db_session):
    from brand_dishes import build_brand_dishes
    _, prods = _seed(db_session, "domino-s-pizza", "Domino's Pizza",
                     [("gs3j", "Domino's Dhanmondi"), ("wteu", "Domino's Gulshan"),
                      ("s1b9", "Domino's Uttara")],
                     "Margherita", [199, 199, 199])
    cards = build_brand_dishes(db_session, prods)
    assert len(cards) == 1
    assert cards[0].branch_count == 3
    assert cards[0].brand.name == "Domino's Pizza"
    assert cards[0].price_varies is False
    assert cards[0].price_min_bdt == 199 and cards[0].price_max_bdt == 199


def test_price_range_when_branches_disagree(temp_db, db_session):
    from brand_dishes import build_brand_dishes
    _, prods = _seed(db_session, "domino-s-pizza", "Domino's Pizza",
                     [("gs3j", "A"), ("wteu", "B"), ("s1b9", "C")],
                     "Margherita", [199, 199, 348])
    (card,) = build_brand_dishes(db_session, prods)
    assert card.price_varies is True
    assert card.price_min_bdt == 199
    assert card.price_max_bdt == 348


def test_availability_when_dish_is_at_some_branches(temp_db, db_session):
    """Brand has 3 branches; only 2 sell the dish -> 'at 2 of 3 branches'."""
    from brand_dishes import build_brand_dishes
    _, prods = _seed(db_session, "domino-s-pizza", "Domino's Pizza",
                     [("gs3j", "A"), ("wteu", "B"), ("s1b9", "C")],
                     "Margherita", [199, 199, None])
    (card,) = build_brand_dishes(db_session, prods)
    assert card.branch_count == 2
    assert card.brand_branch_total == 3


def test_standalone_restaurant_is_a_brand_of_one(temp_db, db_session):
    from brand_dishes import build_brand_dishes
    _, prods = _seed(db_session, "niribily", "Niribily", [("avx4", "Niribily")],
                     "Bhorta", [80])
    (card,) = build_brand_dishes(db_session, prods)
    assert card.branch_count == 1
    assert card.brand_branch_total == 1
    assert card.price_varies is False


def test_different_brands_stay_separate(temp_db, db_session):
    from brand_dishes import build_brand_dishes
    _, a = _seed(db_session, "domino-s-pizza", "Domino's", [("gs3j", "D1")], "Margherita", [199])
    _, b = _seed(db_session, "bella-italia", "Bella Italia", [("s8mp", "B1")], "Margherita", [250])
    cards = build_brand_dishes(db_session, a + b)
    assert len(cards) == 2


def test_same_name_different_food_type_stays_separate(temp_db, db_session):
    """Spec D12: without food_type_id in the key, ONE brand's 'Chicken' curry
    fuses with its own 'Chicken' pizza. Seed both on the same restaurant."""
    import models
    from brand_dishes import build_brand_dishes

    curry = models.FoodType(name="Curry")
    pizza = models.FoodType(name="Pizza")
    chain = models.RestaurantChain(chain_code="brand-x", name="X")
    db_session.add_all([curry, pizza, chain])
    db_session.flush()
    r = models.Restaurant(source_restaurant_code="r1", name="X1", chain_id=chain.id)
    db_session.add(r)
    db_session.flush()

    prods = [
        models.Product(source_product_id=next(_pid), restaurant_id=r.id, name="Chicken",
                       base_price_bdt=100, normalized_name="chicken", food_type_id=curry.id),
        models.Product(source_product_id=next(_pid), restaurant_id=r.id, name="Chicken",
                       base_price_bdt=200, normalized_name="chicken", food_type_id=pizza.id),
    ]
    db_session.add_all(prods)
    db_session.commit()

    cards = build_brand_dishes(db_session, prods)
    assert len(cards) == 2, "Chicken curry and Chicken pizza must not fuse"


def test_pooled_rating_across_branches(temp_db, db_session):
    """Spec D5: pool reviews across branches so a thin review pool is not split
    three ways."""
    import models
    from brand_dishes import build_brand_dishes
    _, prods = _seed(db_session, "domino-s-pizza", "Domino's",
                     [("gs3j", "A"), ("wteu", "B")], "Margherita", [199, 199])
    u1 = models.User(email="a@b.com", display_name="a", password_hash="x")
    u2 = models.User(email="c@d.com", display_name="c", password_hash="x")
    db_session.add_all([u1, u2])
    db_session.flush()
    db_session.add(models.ProductReview(user_id=u1.id, product_id=prods[0].id,
                                        rating=5, status="approved"))
    db_session.add(models.ProductReview(user_id=u2.id, product_id=prods[1].id,
                                        rating=3, status="approved"))
    db_session.commit()
    (card,) = build_brand_dishes(db_session, prods)
    assert card.review_count == 2
    assert card.average_rating == 4.0


def test_pending_reviews_are_excluded_from_pooling(temp_db, db_session):
    import models
    from brand_dishes import build_brand_dishes
    _, prods = _seed(db_session, "b", "B", [("r1", "R1")], "Margherita", [199])
    u = models.User(email="a@b.com", display_name="a", password_hash="x")
    db_session.add(u)
    db_session.flush()
    db_session.add(models.ProductReview(user_id=u.id, product_id=prods[0].id,
                                        rating=1, status="pending"))
    db_session.commit()
    (card,) = build_brand_dishes(db_session, prods)
    assert card.review_count == 0
    assert card.average_rating is None
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_brand_dishes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brand_dishes'`

- [ ] **Step 4: Implement**

Create `brand_dishes.py`:

```python
"""Brand dish cards: collapse a brand's branches into one card.

The card is a GROUPING, not an entity -- there is no chain_dishes table. Name,
price range, availability and pooled rating are all derived from the per-branch
product rows, which must stay for per-branch reviews, availability and future
map pins.

Key = (chain_id, food_type_id, normalized_name). food_type_id is required, not
decoration: without it a brand's "Chicken" curry fuses with its "Chicken"
pizza (the canonical bootstrap learned this the hard way).
"""

import collections
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas


def brand_key(p: models.Product) -> tuple:
    return (p.restaurant.chain_id, p.food_type_id, p.normalized_name)


def dish_slug(normalized_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (normalized_name or "").lower()).strip("-")


def _branch_totals(db: Session, chain_ids: list[int]) -> dict[int, int]:
    """How many branches each brand has overall (the '3' in 'at 2 of 3')."""
    if not chain_ids:
        return {}
    return dict(
        db.query(models.Restaurant.chain_id, func.count(models.Restaurant.id))
        .filter(models.Restaurant.chain_id.in_(chain_ids),
                models.Restaurant.is_active.is_(True))
        .group_by(models.Restaurant.chain_id)
        .all()
    )


def _review_stats(db: Session, product_ids: list[int]) -> dict[int, tuple]:
    """(sum_rating, count) per product, APPROVED only -- summed so the caller
    can pool across a brand's branches without re-querying."""
    if not product_ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.ProductReview.product_id,
            func.sum(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .filter(models.ProductReview.product_id.in_(product_ids),
                models.ProductReview.status == "approved")
        .group_by(models.ProductReview.product_id)
        .all()
    }


def build_brand_dishes(db: Session, products: list[models.Product]) -> list[schemas.BrandDishOut]:
    if not products:
        return []

    groups: dict[tuple, list[models.Product]] = collections.defaultdict(list)
    for p in products:
        groups[brand_key(p)].append(p)

    totals = _branch_totals(db, [p.restaurant.chain_id for p in products])
    stats = _review_stats(db, [p.id for p in products])

    cards: list[schemas.BrandDishOut] = []
    for (chain_id, food_type_id, normalized_name), members in groups.items():
        prices = [float(m.base_price_bdt) for m in members]
        rating_sum = sum(stats.get(m.id, (0, 0))[0] or 0 for m in members)
        rating_n = sum(stats.get(m.id, (0, 0))[1] or 0 for m in members)
        # display name = most common raw spelling among the branches
        display = collections.Counter(m.name.strip() for m in members).most_common(1)[0][0]
        first = members[0]
        cards.append(schemas.BrandDishOut(
            brand=schemas.BrandOut(id=chain_id, name=first.restaurant.chain.name),
            food_type_id=food_type_id,
            slug=dish_slug(normalized_name),
            name=display,
            description=first.description,
            image_url=next((m.image_url for m in members if m.image_url), None),
            category_raw=first.category.name if first.category else None,
            food_type=schemas.FoodTypeOut(id=first.food_type.id, name=first.food_type.name)
                      if first.food_type else None,
            cuisines=[schemas.CuisineOut.model_validate(first.cuisine)] if first.cuisine else [],
            flavor_tags=[schemas.FlavorTagOut(id=l.flavor_tag.id, name=l.flavor_tag.label)
                         for l in first.flavor_tag_links],
            canonical_dish_id=first.canonical_dish_id,
            price_min_bdt=min(prices),
            price_max_bdt=max(prices),
            price_varies=min(prices) != max(prices),
            branch_count=len({m.restaurant_id for m in members}),
            brand_branch_total=totals.get(chain_id, len({m.restaurant_id for m in members})),
            is_sold_out_everywhere=all(m.is_sold_out for m in members),
            average_rating=round(rating_sum / rating_n, 1) if rating_n else None,
            review_count=rating_n,
        ))
    return cards
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_brand_dishes.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add schemas.py brand_dishes.py tests/test_brand_dishes.py
git commit -m "feat(api): brand dish cards with price range, availability, pooled reviews"
```

---

### Task 4: Search returns brand cards

This is the task that kills the duplicates: `Margherita Pizza` currently returns 18 rows (Bella Italia ×3, Delifrance ×3, Alfredough ×3, …) for 12 brands.

**Files:**
- Modify: `dish_detail.py` (`search_dishes`)
- Modify: `schemas.py` (`DishSearchResult.dishes` type)
- Modify: `routers/dishes.py` (docstring only)
- Test: `tests/test_search_brand_cards.py`

**Interfaces:**
- Consumes: `build_brand_dishes` (Task 3).
- Produces: `search_dishes(db, q, *, offset, limit) -> tuple[list[schemas.BrandDishOut], int]` — **the return element type changes** from `DishOut` to `BrandDishOut`; `total` now counts brand cards, not product rows.

- [ ] **Step 1: Write the failing test**

Create `tests/test_search_brand_cards.py`:

```python
import itertools

# Explicit counter -- hash() on a str is salted per process, so hash-derived
# source_product_id values collide at random. See tests/test_brand_dishes.py.
_pid = itertools.count(50000)


def _brand_with_branches(db, slug, name, branches, dish, price=199):
    import models
    chain = models.RestaurantChain(chain_code=slug, name=name)
    db.add(chain)
    db.flush()
    out = []
    for i, code in enumerate(branches):
        r = models.Restaurant(source_restaurant_code=code, name=f"{name} {i}", chain_id=chain.id)
        db.add(r)
        db.flush()
        p = models.Product(source_product_id=next(_pid), restaurant_id=r.id,
                           name=dish, base_price_bdt=price, normalized_name=dish.lower())
        db.add(p)
        out.append(p)
    db.commit()
    return out


def test_search_collapses_a_chain_to_one_card(temp_db, db_session):
    from dish_detail import search_dishes
    _brand_with_branches(db_session, "domino-s-pizza", "Dominos", ["a1", "a2", "a3"], "Margherita")
    cards, total = search_dishes(db_session, "margherita")
    assert total == 1, "three branches must be one card"
    assert cards[0].branch_count == 3


def test_search_keeps_distinct_brands(temp_db, db_session):
    from dish_detail import search_dishes
    _brand_with_branches(db_session, "domino-s-pizza", "Dominos", ["a1", "a2"], "Margherita")
    _brand_with_branches(db_session, "bella-italia", "Bella", ["b1"], "Margherita", price=250)
    cards, total = search_dishes(db_session, "margherita")
    assert total == 2
    assert {c.brand.name for c in cards} == {"Dominos", "Bella"}


def test_search_paginates_cards_not_rows(temp_db, db_session):
    from dish_detail import search_dishes
    for i in range(3):
        _brand_with_branches(db_session, f"brand-{i}", f"Brand{i}", [f"r{i}a", f"r{i}b"], "Margherita")
    page, total = search_dishes(db_session, "margherita", offset=0, limit=2)
    assert total == 3 and len(page) == 2
    page2, _ = search_dishes(db_session, "margherita", offset=2, limit=2)
    assert len(page2) == 1


def test_search_still_surfaces_non_canonical_dishes(temp_db, db_session):
    """A single-restaurant dish has canonical_dish_id NULL and must remain findable."""
    from dish_detail import search_dishes
    _brand_with_branches(db_session, "solo", "Solo", ["s1"], "Prawn Tempura")
    cards, total = search_dishes(db_session, "tempura")
    assert total == 1
    assert cards[0].canonical_dish_id is None
    assert cards[0].branch_count == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_search_brand_cards.py -v`
Expected: FAIL — `assert 3 == 1` (search returns one row per branch today).

- [ ] **Step 3: Implement**

In `schemas.py`, change `DishSearchResult`:

```python
class DishSearchResult(BaseModel):
    """The core 'search a food' response: canonical dishes to compare
    (with stats), plus paginated brand dish cards. A chain appears ONCE:
    its branches are collapsed into a single card."""
    query: str
    canonical_matches: list[CanonicalDishMatch] = []
    total: int = 0        # number of brand cards, not product rows
    offset: int = 0
    limit: int = 20
    dishes: list[BrandDishOut] = []
```

In `dish_detail.py`, replace `search_dishes` with:

```python
def search_dishes(
    db: Session,
    q: str,
    *,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[schemas.BrandDishOut], int]:
    """Brand dish cards matching the query, paginated, most-relevant first.

    A chain appears ONCE: its branches collapse into one card. Non-canonical
    dishes are included (a dish at one restaurant has canonical_dish_id NULL
    but must still be findable).

    Ranks on a lightweight (id, name, key) fetch and only hydrates the page's
    groups, so a broad query ("chicken") never joins thousands of rows.
    """
    from brand_dishes import build_brand_dishes

    pattern = f"%{q}%"
    q_lower = q.lower().strip()

    rows = (
        db.query(
            models.Product.id,
            models.Product.name,
            models.Restaurant.chain_id,
            models.Product.food_type_id,
            models.Product.normalized_name,
        )
        .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
        .outerjoin(models.FoodType, models.Product.food_type_id == models.FoodType.id)
        .outerjoin(models.CanonicalDish, models.Product.canonical_dish_id == models.CanonicalDish.id)
        .filter(
            models.Product.is_active.is_(True),
            or_(
                models.Product.name.ilike(pattern),
                models.FoodType.name.ilike(pattern),
                models.CanonicalDish.name.ilike(pattern),
            ),
        )
        .all()
    )

    # group the candidate rows into brand cards before paginating, so the page
    # size counts cards
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r.chain_id, r.food_type_id, r.normalized_name), []).append(r)

    def rank(key):
        best = min(_search_rank(r.name, q_lower) for r in groups[key])
        label = min((r.name or "").lower() for r in groups[key])
        return (best, label)

    ordered_keys = sorted(groups, key=rank)
    total = len(ordered_keys)
    page_keys = ordered_keys[offset : offset + limit]
    if not page_keys:
        return [], total

    page_ids = [r.id for k in page_keys for r in groups[k]]
    prods = _product_query(db).filter(models.Product.id.in_(page_ids)).all()
    cards = build_brand_dishes(db, prods)

    # Restore relevance order. The card exposes `slug`, not normalized_name, so
    # key the lookup on the slug the card will actually carry.
    order = {(k[0], k[1], dish_slug(k[2])): i for i, k in enumerate(page_keys)}
    cards.sort(key=lambda c: order.get((c.brand.id, c.food_type_id, c.slug), len(order)))
    return cards, total
```

Add the import at the top of `dish_detail.py`:

```python
from brand_dishes import build_brand_dishes, dish_slug
```

(`brand_dishes` imports only `models`/`schemas`, so there is no import cycle — drop the
function-local imports shown above and use this top-level one throughout.)

Ensure `_product_query` eager-loads the brand so `build_brand_dishes` does not N+1:

```python
def _product_query(db: Session):
    return db.query(models.Product).options(
        joinedload(models.Product.food_type),
        joinedload(models.Product.category),
        joinedload(models.Product.cuisine),
        joinedload(models.Product.restaurant).joinedload(models.Restaurant.chain),
        joinedload(models.Product.variations),
        joinedload(models.Product.flavor_tag_links).joinedload(models.ProductFlavorTag.flavor_tag),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_search_brand_cards.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -v`
Expected: all pass. If `tests/test_infra.py` or phase 1-2 tests fail, you regressed something — fix rather than adjust the test.

- [ ] **Step 6: Commit**

```bash
git add dish_detail.py schemas.py routers/dishes.py tests/test_search_brand_cards.py
git commit -m "feat(api): search returns brand cards, collapsing chain branches"
```

---

### Task 5: Compare returns one row per brand

`GET /dishes/compare/{id}` currently lists one row per branch: Bella Italia ×3, Delifrance ×3. `restaurant_count` already says 12 brands while the list shows 18 rows — they must agree.

**Files:**
- Modify: `dish_detail.py` (`get_canonical_dish_comparison`)
- Modify: `schemas.py` (`DishCompareResult.dishes` type)
- Test: `tests/test_compare_brand_rows.py`

**Interfaces:**
- Consumes: `build_brand_dishes` (Task 3).
- Produces: `DishCompareResult.dishes: list[BrandDishOut]`; `total` counts brands.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_brand_rows.py`:

```python
def test_compare_lists_one_row_per_brand(temp_db, db_session):
    """Two Domino's branches + one Bella Italia serving the same canonical dish
    = 2 rows, not 3. Comparing a dish to itself across branches is not a
    comparison."""
    import models
    from dish_detail import get_canonical_dish_comparison

    cd = models.CanonicalDish(name="Margherita")
    db_session.add(cd)
    db_session.flush()

    dom = models.RestaurantChain(chain_code="domino-s-pizza", name="Dominos")
    bel = models.RestaurantChain(chain_code="bella-italia", name="Bella")
    db_session.add_all([dom, bel])
    db_session.flush()

    for i, (code, chain, price) in enumerate([
        ("gs3j", dom, 199), ("wteu", dom, 199), ("s8mp", bel, 250),
    ]):
        r = models.Restaurant(source_restaurant_code=code, name=code, chain_id=chain.id)
        db_session.add(r)
        db_session.flush()
        db_session.add(models.Product(source_product_id=7000 + i, restaurant_id=r.id,
                                      name="Margherita", base_price_bdt=price,
                                      normalized_name="margherita",
                                      canonical_dish_id=cd.id))
    db_session.commit()

    result = get_canonical_dish_comparison(db_session, cd.id)
    assert result.total == 2, "Dominos must appear once, not per branch"
    assert {d.brand.name for d in result.dishes} == {"Dominos", "Bella"}
    dominos = next(d for d in result.dishes if d.brand.name == "Dominos")
    assert dominos.branch_count == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_compare_brand_rows.py -v`
Expected: FAIL — `assert 3 == 2`.

- [ ] **Step 3: Implement**

In `schemas.py`:

```python
class DishCompareResult(BaseModel):
    """One canonical dish compared across every BRAND serving it. A chain is one
    row with branch_count > 1, not one row per branch."""
    canonical_dish: CanonicalDishOut
    dishes: list[BrandDishOut]
    total: int = 0        # number of brands
    offset: int = 0
    limit: int = 20
    average_rating: Optional[float] = None
    min_price_bdt: Optional[float] = None
    max_price_bdt: Optional[float] = None
```

In `dish_detail.py`, inside `get_canonical_dish_comparison`, replace the enrich/sort/slice block that builds `sorted_dishes` with:

```python
    from brand_dishes import build_brand_dishes

    products = (
        _product_query(db)
        .filter(
            models.Product.canonical_dish_id == canonical_dish_id,
            models.Product.is_active.is_(True),
        )
        .all()
    )
    cards = build_brand_dishes(db, products)
    cards.sort(key=lambda c: (c.average_rating is None, -(c.average_rating or 0)))
    total = len(cards)
    page = cards[offset : offset + limit]

    rated = [c for c in cards if c.average_rating is not None]
    avg_rating = round(sum(c.average_rating for c in rated) / len(rated), 1) if rated else None
    prices = [c.price_min_bdt for c in cards] + [c.price_max_bdt for c in cards]
    min_price = min(prices) if prices else None
    max_price = max(prices) if prices else None
```

Keep the existing `schemas.DishCompareResult(...)` construction, passing `dishes=page`.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_compare_brand_rows.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add dish_detail.py schemas.py tests/test_compare_brand_rows.py
git commit -m "feat(api): compare lists one row per brand, not per branch"
```

---

### Task 6: Brand endpoints

**Files:**
- Create: `routers/brands.py`
- Modify: `main.py` (include the router)
- Modify: `schemas.py` (`BrandDetailOut`)
- Test: `tests/test_brands_router.py`

**Interfaces:**
- Consumes: `build_brand_dishes`, `dish_slug` (Task 3), `resolve_display_rating` (`restaurant_reviews`).
- Produces: `GET /brands/{chain_id}` and `GET /brands/{chain_id}/dishes/{food_type_id}/{slug}`.
  `food_type_id` is in the path because the card key includes it (spec D12) — a `{chain_id, slug}`-only URL collides a brand's "Chicken" curry with its "Chicken" pizza. The natural key is used deliberately instead of a serial id: ids churn on reload, the key does not, so deep links survive.

- [ ] **Step 1: Add the schema**

In `schemas.py`:

```python
class BrandDetailOut(BaseModel):
    """A brand and its branches. The branch list is what the future
    map/directions view renders."""
    id: int
    name: str
    branch_count: int
    branches: list[RestaurantSummaryOut] = []
    display_rating: Optional[float] = None
    display_rating_source: Optional[str] = None
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_brands_router.py`:

```python
def _seed_brand(db):
    import models
    chain = models.RestaurantChain(chain_code="domino-s-pizza", name="Domino's Pizza")
    db.add(chain)
    db.flush()
    ft = models.FoodType(name="Pizza")
    db.add(ft)
    db.flush()
    prods = []
    for i, (code, price) in enumerate([("gs3j", 199), ("wteu", 199), ("s1b9", 348)]):
        r = models.Restaurant(source_restaurant_code=code, name=f"Dominos {code}",
                              area="Dhanmondi", chain_id=chain.id, old_rating=4.5,
                              old_review_count=100)
        db.add(r)
        db.flush()
        p = models.Product(source_product_id=6000 + i, restaurant_id=r.id,
                           name="Margherita", base_price_bdt=price,
                           normalized_name="margherita", food_type_id=ft.id)
        db.add(p)
        prods.append(p)
    db.commit()
    return chain, ft


def test_brand_page_lists_branches(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, _ = _seed_brand(db_session)
    body = TestClient(app).get(f"/brands/{chain.id}").json()
    assert body["name"] == "Domino's Pizza"
    assert body["branch_count"] == 3
    assert len(body["branches"]) == 3
    assert body["display_rating_source"] == "foodpanda"  # no khawon reviews yet


def test_brand_page_404s_for_unknown_brand(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    assert TestClient(app).get("/brands/999999").status_code == 404


def test_brand_dish_detail_shows_per_branch_breakdown(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, ft = _seed_brand(db_session)
    body = TestClient(app).get(f"/brands/{chain.id}/dishes/{ft.id}/margherita").json()
    assert body["name"] == "Margherita"
    assert body["branch_count"] == 3
    assert body["price_varies"] is True
    assert len(body["branches"]) == 3
    # each branch exposes its own product_id so it can be reviewed
    assert all(b["product_id"] for b in body["branches"])


def test_brand_dish_detail_404s_for_unknown_slug(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, ft = _seed_brand(db_session)
    assert TestClient(app).get(f"/brands/{chain.id}/dishes/{ft.id}/nope").status_code == 404
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_brands_router.py -v`
Expected: FAIL — 404 on `/brands/{id}` (router does not exist).

- [ ] **Step 4: Implement**

Create `routers/brands.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from brand_dishes import build_brand_dishes, dish_slug
from database import get_db
from dish_detail import _product_query
import models
from restaurant_reviews import resolve_display_rating, restaurant_review_stats
import schemas

router = APIRouter(prefix="/brands", tags=["Brands"])


@router.get("/{chain_id}", response_model=schemas.BrandDetailOut)
def get_brand(chain_id: int, db: Session = Depends(get_db)):
    """A brand and its branches. The branch list feeds the map/directions view."""
    chain = db.query(models.RestaurantChain).filter(models.RestaurantChain.id == chain_id).first()
    if not chain:
        raise HTTPException(status_code=404, detail="Brand not found")

    branches = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.chain_id == chain_id, models.Restaurant.is_active.is_(True))
        .order_by(models.Restaurant.name)
        .all()
    )
    stats = restaurant_review_stats(db, [b.id for b in branches])
    # brand rating = review-count-weighted average across branches, else the
    # foodpanda fallback (spec D6). Nearest-branch replaces this when geo lands.
    total_n = sum(stats.get(b.id, (None, 0))[1] or 0 for b in branches)
    khawon_avg = (
        round(sum(float(stats[b.id][0]) * stats[b.id][1] for b in branches if b.id in stats) / total_n, 1)
        if total_n else None
    )
    fp = [(float(b.old_rating), b.old_review_count or 0) for b in branches if b.old_rating is not None]
    fp_n = sum(n for _, n in fp)
    fp_avg = round(sum(r * n for r, n in fp) / fp_n, 1) if fp_n else (fp[0][0] if fp else None)
    rating, _count, source = resolve_display_rating(khawon_avg, total_n, fp_avg, fp_n)

    return schemas.BrandDetailOut(
        id=chain.id,
        name=chain.name,
        branch_count=len(branches),
        branches=[
            schemas.RestaurantSummaryOut(
                id=b.id, name=b.name, area=b.area, address=b.address,
                image_url=b.hero_image_url, google_place_id=b.google_place_id,
            )
            for b in branches
        ],
        display_rating=rating,
        display_rating_source=source,
    )


@router.get("/{chain_id}/dishes/{food_type_id}/{slug}", response_model=schemas.BrandDishDetailOut)
def get_brand_dish(chain_id: int, food_type_id: int, slug: str, db: Session = Depends(get_db)):
    """One brand's dish, with the per-branch breakdown (prices + each branch's
    own product_id, which is what POST /reviews takes)."""
    products = [
        p for p in (
            _product_query(db)
            .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
            .filter(
                models.Restaurant.chain_id == chain_id,
                models.Product.food_type_id == food_type_id,
                models.Product.is_active.is_(True),
            )
            .all()
        )
        if dish_slug(p.normalized_name) == slug
    ]
    if not products:
        raise HTTPException(status_code=404, detail="Brand dish not found")

    card = build_brand_dishes(db, products)[0]
    from dish_detail import _product_review_stats
    stats = _product_review_stats(db, [p.id for p in products])
    branches = []
    for p in sorted(products, key=lambda x: x.restaurant.name):
        avg_raw, n = stats.get(p.id, (None, 0))
        branches.append(schemas.BrandBranchOut(
            restaurant_id=p.restaurant.id,
            restaurant_name=p.restaurant.name,
            area=p.restaurant.area,
            product_id=p.id,
            price_bdt=float(p.base_price_bdt),
            is_sold_out=p.is_sold_out,
            average_rating=round(float(avg_raw), 1) if avg_raw else None,
            review_count=n or 0,
        ))
    return schemas.BrandDishDetailOut(**card.model_dump(), branches=branches)
```

Add to `schemas.py`:

```python
class BrandDishDetailOut(BrandDishOut):
    """Brand dish + per-branch breakdown (spec D5: pooled headline, branch detail)."""
    branches: list[BrandBranchOut] = []
```

In `main.py`, add the import and registration:

```python
from routers import auth, brands, dishes, food_images, food_types, places, restaurants, reviews
...
app.include_router(brands.router)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_brands_router.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add routers/brands.py main.py schemas.py tests/test_brands_router.py
git commit -m "feat(api): brand page and brand dish detail with per-branch breakdown"
```

---

### Task 7: Reload, verify, and check the duplicates are actually gone

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 2: Confirm the app imports and the contract builds**

```bash
USE_SQLITE=1 python -c "import main; main.app.openapi(); print('routes:', len(main.app.routes))"
```

Expected: no error; route count above 41 (brand endpoints added).

- [ ] **Step 3: Verify against the live database**

This is the acceptance test for the whole chain effort — `Margherita Pizza` reported 12 brands but listed 18 rows.

```bash
cd "C:/Users/shoha/Documents/GitHub/Khawon/khawon-api"
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'.')
from main import app
from fastapi.testclient import TestClient
c = TestClient(app)
s = c.get('/dishes/search', params={'q':'margherita','limit':5}).json()
print('search margherita -> cards:', s['total'])
for d in s['dishes'][:5]:
    price = f\"{d['price_min_bdt']:.0f}\" if not d['price_varies'] else f\"{d['price_min_bdt']:.0f}-{d['price_max_bdt']:.0f}\"
    print(f\"   {d['brand']['name']!r}: {d['name']} | {price}tk | at {d['branch_count']} of {d['brand_branch_total']} branches\")
m = s['canonical_matches'][0]
comp = c.get(f\"/dishes/compare/{m['id']}\").json()
print()
print(f\"compare {m['name']!r}: restaurant_count={m['restaurant_count']} brands | rows={comp['total']}\")
assert m['restaurant_count'] == comp['total'], 'headline and row count MUST agree'
print('   brands:', [d['brand']['name'] for d in comp['dishes']])
"
```

Expected: `restaurant_count == comp['total']` (the assert must hold — that mismatch was the whole bug), each Bella Italia / Delifrance / Alfredough appearing **once** with `branch_count: 3`.

- [ ] **Step 4: Commit any fixes and report**

Report to the owner: card counts before/after, and confirm the frontend contract changes (`dishes[]` is now `BrandDishOut`, `DishCompareResult.dishes` is now `BrandDishOut`, new `/brands/*` endpoints).

---

## Verification of done

- Searching a chain dish returns **one card per brand**, with `branch_count` / `brand_branch_total`.
- `price_varies` is true only where branches genuinely disagree (~3% of shared chain dishes); otherwise min == max.
- Compare's `restaurant_count` equals the number of rows returned.
- A standalone restaurant renders `branch_count == brand_branch_total == 1`.
- A brand's "Chicken" curry and "Chicken" pizza remain separate cards.
- Reviews pool across branches; pending/rejected excluded.
- `POST /reviews {dish_id}` still targets one branch's product row (unchanged).
- `python -m pytest tests/` passes.

## Follow-on

- Phase 5 (geo/near-me): apply `schema_geo.sql` on a PostGIS host; swap brand rating from weighted-average to nearest-branch.
- Owner: frontend brand card, availability badge, "from ৳X", per-branch breakdown.
- `GET /restaurants` brand-level browse (spec D14) — deliberately deferred; the dish surfaces are what drove this work.
