# Brand Identity + Canonical-by-Brand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every restaurant a trustworthy brand identity derived in the pipeline, then make canonical dishes count brands instead of branches — so "compare across restaurants" stops comparing a dish to itself at three branches of one chain.

**Architecture:** A new pipeline stage (`bootstrap_chains.py`) groups restaurants into brands by normalized name, with a per-restaurant override map for exceptions. It emits `chains.json`, which `load_batch.py` uses to populate `restaurant_chains` and set `restaurants.chain_id` for **every** restaurant (a standalone restaurant is a brand of one). `bootstrap_canonical_dishes.py` then promotes a dish only when 2+ distinct **brands** serve it, and the API counts distinct brands for `restaurant_count`.

**Tech Stack:** Python 3.12, pytest 7.4.4, SQLAlchemy 2.0, psycopg2, PostgreSQL 18 (Railway, no PostGIS), FastAPI.

**Spec:** `docs/superpowers/specs/2026-07-15-chain-brand-model-design.md` (commit 780fa03)

**Scope:** This plan covers spec phases **1–2 only**. Phases 3–4 (`products.normalized_name`, brand-grouped search cards, brand-level review pooling) get a separate plan. Phase 5 (geo) is out of scope.

## Global Constraints

- **Repo:** `C:\Users\shoha\Documents\GitHub\Khawon\khawon-api`, branch `main`.
- **The repo copy of pipeline scripts is authoritative.** `bootstrap_canonical_dishes.py` and `consolidate_variants.py` exist BOTH in this repo AND in `C:\Users\shoha\OneDrive\Desktop\strip data\code\`. They are byte-identical apart from line endings (repo = CRLF). **Always edit the repo copy.** After changing a pipeline script, copy it to `strip data\code\` so the two do not drift.
- **Data directory (not in the repo):** `C:\Users\shoha\OneDrive\Desktop\strip data\code\v2_output\` holds `consolidated.json`, `canonical_dishes.json`, and `restaurants_*_restaurants.json`. Never hardcode this path in a module — pass it via CLI args.
- **Curated data lives in the pipeline, never as hand-edited DB rows.** The Railway DB is reset and reloaded from pipeline output; DB edits are wiped on reload.
- **Windows:** prefix ad-hoc Python with `PYTHONIOENCODING=utf-8` (Bengali/emoji in the data).
- **Do not regress the existing fuzzy canonical matching.** `bootstrap_canonical_dishes.py` already does spelling maps, protein signatures, modifier compatibility, and `SequenceMatcher` fuzzy merge at `FUZZY_MERGE_THRESHOLD = 0.92`. Leave all of it intact; this plan changes only the promotion rule.
- **Naming:** the spec calls the override map `CHAIN_OVERRIDES`; this plan names it **`BRAND_OVERRIDES`** — same thing, "brand" is the accurate term (it also covers standalone restaurants). Use `BRAND_OVERRIDES`.
- **Dataset canaries:** 451 restaurants and 16,402 active products are **measured facts** — assert them. The brand counts (**383 total** = 331 solo + 52 multi-location) are **predictions from a prototype normalizer**, not from the `normalize_brand_name` in Task 2. Confirm them on the first real run of Task 4; if they differ, work out *why* (a real merge/split difference) before pinning the true values into the test and back into this section. Do not blindly edit the test to match whatever it prints.
- **Test DB gotcha (spec §9):** `database.py` calls `load_dotenv()`, which re-reads `DATABASE_PUBLIC_URL` from `.env` and it **wins over** `DATABASE_URL`. To point tests at a temp DB you must **SET** `os.environ["DATABASE_PUBLIC_URL"]` (load_dotenv will not override an already-set var) — popping it is not enough or tests silently hit the real Railway DB. Always `engine.dispose()` and terminate backends before `DROP DATABASE`.

---

### Task 1: Test infrastructure

There is no `tests/` directory and pytest is missing from `requirements.txt` (though pytest 7.4.4 is installed locally). Everything downstream needs this.

**Files:**
- Modify: `requirements.txt`
- Create: `conftest.py` (repo root — makes root-level modules importable from tests)
- Create: `tests/conftest.py`
- Create: `tests/test_infra.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `temp_db` session fixture yielding a Postgres URL with `schema.sql` applied, with the app's `database`/`models` modules pointed at it. Later DB tasks depend on this exact fixture name.

- [ ] **Step 1: Add pytest to requirements**

Append to `requirements.txt`:

```
pytest==7.4.4
```

- [ ] **Step 2: Create the root conftest so tests can import root modules**

Create `conftest.py` at the repo root with exactly this content (a root-level conftest causes pytest to prepend the repo root to `sys.path`, which is how `import load_batch` works from `tests/`):

```python
"""Root conftest: presence of this file makes pytest add the repo root to
sys.path, so tests can `import load_batch`, `import bootstrap_chains`, etc."""
```

- [ ] **Step 3: Write the temp-DB fixture**

Create `tests/conftest.py`:

```python
"""Test fixtures. The temp_db fixture builds a throwaway Postgres database
from schema.sql and repoints the app at it.

WARNING: database.py calls load_dotenv(), which re-reads DATABASE_PUBLIC_URL
from .env and that value WINS over DATABASE_URL. So we must SET both env vars
(load_dotenv does not override an already-set var). Popping is NOT enough --
doing so silently runs the tests against the real Railway database.
"""
import os
import sys

import psycopg2
import pytest
from dotenv import load_dotenv

TEST_DB_NAME = "khawon_test"


def _admin_url() -> str:
    load_dotenv()
    return os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def temp_db():
    admin_url = _admin_url()
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")

    base, _, _ = admin_url.rpartition("/")
    url = f"{base}/{TEST_DB_NAME}"

    with open("schema.sql", encoding="utf-8") as fh:
        schema_sql = fh.read()
    tmp = psycopg2.connect(url)
    tmp.autocommit = True
    tmp.cursor().execute(schema_sql)
    tmp.close()

    # Must SET both -- see module docstring.
    os.environ["DATABASE_URL"] = url
    os.environ["DATABASE_PUBLIC_URL"] = url
    os.environ["USE_SQLITE"] = ""
    for mod in ("database", "models", "main", "dish_detail", "restaurant_reviews"):
        sys.modules.pop(mod, None)

    yield url

    import database
    database.engine.dispose()  # release pooled sessions or DROP DATABASE fails
    cur.execute(
        "select pg_terminate_backend(pid) from pg_stat_activity "
        "where datname=%s and pid<>pg_backend_pid()",
        (TEST_DB_NAME,),
    )
    cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    cur.close()
    admin.close()


@pytest.fixture
def db_session(temp_db):
    """Fresh session; truncates data tables between tests."""
    from database import SessionLocal
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
```

- [ ] **Step 4: Write the failing infra test**

Create `tests/test_infra.py`:

```python
def test_temp_db_has_schema(temp_db, db_session):
    from sqlalchemy import text
    n = db_session.execute(text(
        "select count(*) from information_schema.tables "
        "where table_schema='public' and table_type='BASE TABLE'"
    )).scalar()
    assert n == 20


def test_temp_db_is_not_the_real_database(temp_db):
    assert temp_db.endswith("/khawon_test")
```

- [ ] **Step 5: Run the tests**

Run: `cd khawon-api && python -m pytest tests/test_infra.py -v`
Expected: 2 passed. (`test_temp_db_has_schema` asserts 20 — the table count `schema.sql` creates.)

If `test_temp_db_is_not_the_real_database` fails, the env-var trap above has bitten you.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt conftest.py tests/
git commit -m "test: add pytest infrastructure and temp-db fixture"
```

---

### Task 2: Brand name normalization

**Files:**
- Create: `bootstrap_chains.py`
- Test: `tests/test_bootstrap_chains.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize_brand_name(name: str) -> str` and `brand_slug(normalized: str) -> str`. Tasks 3–4 use both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bootstrap_chains.py`:

```python
from bootstrap_chains import brand_slug, normalize_brand_name


def test_strips_area_suffix_after_dash():
    assert normalize_brand_name("Waffle Up - Dhanmondi") == "waffle up"
    assert normalize_brand_name("Bella Italia - Uttara") == "bella italia"


def test_strips_area_token_without_dash():
    assert normalize_brand_name("Domino's Pizza Gulshan") == "domino s pizza"
    assert normalize_brand_name("KOI The Uttara") == "koi the"


def test_keeps_ampersand_and_collapses_punctuation():
    assert normalize_brand_name("Greens & Seeds - Chef's Table Dhanmondi") == "greens & seeds"


def test_distinct_brands_do_not_collapse():
    assert normalize_brand_name("Pizza Hut-Dhanmondi") != normalize_brand_name("Pizza Burg - Mohammadpur")


def test_slug_is_url_safe():
    assert brand_slug("domino s pizza") == "domino-s-pizza"
    assert brand_slug("greens & seeds") == "greens-and-seeds"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bootstrap_chains.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap_chains'`

- [ ] **Step 3: Implement**

Create `bootstrap_chains.py`:

```python
"""bootstrap_chains.py

Brand identity for Khawon. Groups restaurants into BRANDS so a chain's
branches collapse into one identity.

Why not trust the source chain_code? It is wrong for ~21% of real brands: it
SPLITS them (Waffle Up across cz5re/ch5ue, New Hanif Biryani across
ck1ob/cr7gd, Thai Bistro across cy7dd/cq2yv) and MISSES them entirely
(Rice & More has chain_code=None on one branch). So chain_code is a signal,
never the grouping key.

Grouping is by normalized name, with BRAND_OVERRIDES pinning exceptions.
Every restaurant gets a brand -- a standalone restaurant is a brand of one --
so downstream code can always GROUP BY brand with no special-casing.

Usage:
    python bootstrap_chains.py --restaurants "v2_output/restaurants_*_restaurants.json" \
                               --out v2_output/chains.json
    python bootstrap_chains.py --restaurants "..." --review    # print groups, write nothing
"""

from __future__ import annotations

import re

# Area/branch tokens that appear in restaurant names but are not part of the
# brand. Longest first so "gulshan avenue" is removed before "gulshan".
AREA_TOKENS = [
    "jashimuddin avenue", "azampur railgate", "gulshan avenue", "shimanto square",
    "noorjahan road", "elephant road", "tajmohol road", "central road",
    "middle badda", "sat masjid", "satmosjid", "shatmosjid", "keari plaza",
    "nakhalpara", "centrepoint", "shahjadpur", "panthapath", "mohammadpur",
    "green road", "kalabagan", "baridhara", "dhanmondi", "mohakhali",
    "sukrabad", "hatirpool", "shyamoli", "flagship", "jigatola", "zigatola",
    "baridhara", "gulshan", "uttara", "banani", "airport", "badda",
]


def normalize_brand_name(name: str) -> str:
    """Brand key: lowercase, drop a ' - <branch>' suffix, drop area tokens,
    drop punctuation. 'Waffle Up - Dhanmondi' and 'Waffle Up' both -> 'waffle up'."""
    s = (name or "").lower().strip()
    # Drop everything after the first ' - ' (branch/outlet suffix), e.g.
    # "Greens & Seeds - Chef's Table Dhanmondi" -> "greens & seeds".
    s = re.split(r"\s+[-–—]\s+", s)[0]
    # Also handle "Ledor- Dhanmondi" / "Mithai- Jigatola" (no space before dash).
    s = re.split(r"[-–—]\s+", s)[0]
    s = re.sub(r"[^a-z0-9 &]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for token in AREA_TOKENS:
        s = re.sub(rf"\b{re.escape(token)}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Trailing outlet numbers: "gulshan 2" already lost 'gulshan'; drop the digit.
    s = re.sub(r"\s+\d+$", "", s).strip()
    return s


def brand_slug(normalized: str) -> str:
    """URL-safe stable key derived from the normalized brand name."""
    s = normalized.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bootstrap_chains.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap_chains.py tests/test_bootstrap_chains.py
git commit -m "feat(pipeline): brand name normalization"
```

---

### Task 3: Brand grouping with overrides

**Files:**
- Modify: `bootstrap_chains.py`
- Test: `tests/test_bootstrap_chains.py`

**Interfaces:**
- Consumes: `normalize_brand_name`, `brand_slug` (Task 2).
- Produces: `BRAND_OVERRIDES: dict[str, str]` (source_restaurant_code -> brand slug) and
  `build_brands(restaurants: list[dict]) -> list[dict]` returning
  `[{"slug": str, "name": str, "member_codes": list[str]}]` sorted by slug.
  Task 4 writes this to `chains.json`; Task 5 reads that file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bootstrap_chains.py`:

```python
from bootstrap_chains import build_brands


def _r(code, name, chain_code=None, chain_name=None):
    return {"source_restaurant_code": code, "name": name,
            "chain_code": chain_code, "chain_name": chain_name}


def test_chain_branches_group_into_one_brand():
    brands = build_brands([
        _r("b41r", "Waffle Up - Dhanmondi", "cz5re", "Waffle Up"),
        _r("fjsq", "Waffle Up", "cz5re", "Waffle Up"),
        _r("sajp", "Waffle Up - Gulshan", "ch5ue", "Waffle Up - Kitchen"),
        _r("o4qm", "Waffle Up - Uttara", "cz5re", "Waffle Up"),
    ])
    assert len(brands) == 1
    assert sorted(brands[0]["member_codes"]) == ["b41r", "fjsq", "o4qm", "sajp"]


def test_groups_despite_split_chain_code():
    """Thai Bistro is cy7dd vs cq2yv in the source. Name wins."""
    brands = build_brands([
        _r("rfaa", "Thai Bistro - Banani", "cy7dd"),
        _r("s3lj", "Thai Bistro - Gulshan 2", "cq2yv"),
    ])
    assert len(brands) == 1


def test_groups_despite_missing_chain_code():
    """Rice & More has chain_code None on one branch."""
    brands = build_brands([
        _r("t37j", "Rice & More", None),
        _r("waua", "Rice & More - Uttara", "ci0an"),
    ])
    assert len(brands) == 1


def test_standalone_restaurant_is_a_brand_of_one():
    brands = build_brands([_r("avx4", "Niribily Hotel & Restaurant", None)])
    assert len(brands) == 1
    assert brands[0]["member_codes"] == ["avx4"]


def test_distinct_restaurants_do_not_false_merge():
    brands = build_brands([
        _r("s6so", "Pizza Hut-Dhanmondi", None),
        _r("u4cw", "Pizza Burg - Mohammadpur", None),
    ])
    assert len(brands) == 2


def test_display_name_prefers_chain_name():
    brands = build_brands([
        _r("gs3j", "Domino's Pizza - Dhanmondi", "cu0zf", "Domino's Pizza"),
        _r("wteu", "Domino's Pizza Gulshan", "cu0zf", "Domino's Pizza"),
    ])
    assert brands[0]["name"] == "Domino's Pizza"


def test_display_name_falls_back_to_shortest_raw_name():
    brands = build_brands([
        _r("a", "Habanero", None),
        _r("b", "Habanero - Dhanmondi", None),
    ])
    assert brands[0]["name"] == "Habanero"


def test_override_pins_a_restaurant_to_a_brand(monkeypatch):
    import bootstrap_chains
    monkeypatch.setitem(bootstrap_chains.BRAND_OVERRIDES, "zzz1", "totally-different")
    brands = build_brands([
        _r("zzz1", "Habanero", None),
        _r("zzz2", "Habanero - Dhanmondi", None),
    ])
    assert len(brands) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bootstrap_chains.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_brands'`

- [ ] **Step 3: Implement**

Append to `bootstrap_chains.py`:

```python
import collections

# source_restaurant_code -> brand slug. One mechanism covers both directions:
# assign the same slug to force a MERGE, a different slug to force a SPLIT.
# Populated from the owner's one-time review of the candidate groups
# (`python bootstrap_chains.py --review`). Empty means normalization alone was
# correct for every group.
BRAND_OVERRIDES: dict[str, str] = {}


def _display_name(members: list[dict]) -> str:
    """Brand display name. chain_name is unreliable for GROUPING but good for
    DISPLAY ('Domino's Pizza' beats 'Domino's Pizza Gulshan'); fall back to the
    shortest raw name, which is usually the branch-less one."""
    chain_names = [m.get("chain_name") for m in members if m.get("chain_name")]
    if chain_names:
        return collections.Counter(chain_names).most_common(1)[0][0]
    return min((m["name"].strip() for m in members), key=len)


def build_brands(restaurants: list[dict]) -> list[dict]:
    """Group restaurants into brands. Every restaurant lands in exactly one
    brand; a standalone restaurant is a brand of one."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in restaurants:
        code = r["source_restaurant_code"]
        slug = BRAND_OVERRIDES.get(code) or brand_slug(normalize_brand_name(r["name"]))
        if not slug:
            slug = brand_slug(code)  # degenerate name -> unique brand of one
        groups[slug].append(r)

    brands = [
        {
            "slug": slug,
            "name": _display_name(members),
            "member_codes": sorted(m["source_restaurant_code"] for m in members),
        }
        for slug, members in groups.items()
    ]
    brands.sort(key=lambda b: b["slug"])
    return brands
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bootstrap_chains.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap_chains.py tests/test_bootstrap_chains.py
git commit -m "feat(pipeline): brand grouping with per-restaurant overrides"
```

---

### Task 4: bootstrap_chains CLI + chains.json + review report

**Files:**
- Modify: `bootstrap_chains.py`
- Test: `tests/test_bootstrap_chains_cli.py`

**Interfaces:**
- Consumes: `build_brands` (Task 3).
- Produces: `load_restaurants(glob_pattern: str) -> list[dict]`, `main()`, and the on-disk
  `v2_output/chains.json` — a JSON list of `{"slug","name","member_codes"}`. Task 5 reads this file.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bootstrap_chains_cli.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

DATA = Path(r"C:\Users\shoha\OneDrive\Desktop\strip data\code\v2_output")
GLOB = str(DATA / "restaurants_*_restaurants.json")


def test_cli_writes_chains_json_for_the_real_dataset(tmp_path):
    out = tmp_path / "chains.json"
    proc = subprocess.run(
        [sys.executable, "bootstrap_chains.py", "--restaurants", GLOB, "--out", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    brands = json.loads(out.read_text(encoding="utf-8"))

    # Canaries against the real dataset (see Global Constraints).
    assert len(brands) == 383, f"expected 383 brands, got {len(brands)}"
    assert sum(len(b["member_codes"]) for b in brands) == 451
    assert len([b for b in brands if len(b["member_codes"]) > 1]) == 52

    by_slug = {b["slug"]: b for b in brands}
    # The four known chain_code failures must be fixed by normalization.
    assert len(by_slug["waffle-up"]["member_codes"]) == 4
    assert len(by_slug["thai-bistro"]["member_codes"]) == 2
    assert len(by_slug["new-hanif-biryani"]["member_codes"]) == 2
    assert len(by_slug["rice-and-more"]["member_codes"]) == 2


def test_every_restaurant_appears_in_exactly_one_brand(tmp_path):
    out = tmp_path / "chains.json"
    subprocess.run(
        [sys.executable, "bootstrap_chains.py", "--restaurants", GLOB, "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    brands = json.loads(out.read_text(encoding="utf-8"))
    codes = [c for b in brands for c in b["member_codes"]]
    assert len(codes) == len(set(codes)), "a restaurant landed in two brands"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bootstrap_chains_cli.py -v`
Expected: FAIL — non-zero return code (`bootstrap_chains.py` has no CLI yet).

- [ ] **Step 3: Implement**

Append to `bootstrap_chains.py`:

```python
import argparse
import glob as globlib
import json


def load_restaurants(glob_pattern: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(globlib.glob(glob_pattern)):
        with open(path, encoding="utf-8") as fh:
            rows.extend(json.load(fh))
    if not rows:
        raise SystemExit(f"No restaurants matched: {glob_pattern}")
    return rows


def _print_review_report(brands: list[dict], restaurants: list[dict]) -> None:
    """Print the multi-location candidate groups for the owner's one-time review.
    Anything wrong here gets pinned in BRAND_OVERRIDES."""
    by_code = {r["source_restaurant_code"]: r for r in restaurants}
    multi = [b for b in brands if len(b["member_codes"]) > 1]
    print(f"\n--- {len(multi)} candidate multi-location brands (review these) ---")
    for b in sorted(multi, key=lambda x: (-len(x["member_codes"]), x["slug"])):
        codes = [by_code[c].get("chain_code") for c in b["member_codes"]]
        flag = "  <-- source chain_code SPLIT/MISSING" if len(set(codes)) > 1 or None in codes else ""
        print(f'\n{b["slug"]!r} -> "{b["name"]}" ({len(b["member_codes"])} locations){flag}')
        for code in b["member_codes"]:
            r = by_code[code]
            print(f'    {code}  {r["name"]}   chain_code={r.get("chain_code")}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restaurants", required=True,
                    help='glob for classify *_restaurants.json, e.g. "v2_output/restaurants_*_restaurants.json"')
    ap.add_argument("--out", default="v2_output/chains.json")
    ap.add_argument("--review", action="store_true",
                    help="print candidate groups for human review; write nothing")
    args = ap.parse_args()

    restaurants = load_restaurants(args.restaurants)
    brands = build_brands(restaurants)

    multi = [b for b in brands if len(b["member_codes"]) > 1]
    print(f"Restaurants:            {len(restaurants)}")
    print(f"Brands:                 {len(brands)}")
    print(f"  multi-location:       {len(multi)}")
    print(f"  standalone (of one):  {len(brands) - len(multi)}")
    print(f"Overrides applied:      {len(BRAND_OVERRIDES)}")

    if args.review:
        _print_review_report(brands, restaurants)
        return

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(brands, fh, indent=2, ensure_ascii=False)
    print(f"\nWritten to: {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bootstrap_chains_cli.py -v`
Expected: 2 passed.

If a canary fails (383/451/52), do NOT edit the test to match. Investigate: normalization either fused two real brands or split one.

- [ ] **Step 5: Copy to the data folder and commit**

```bash
cp bootstrap_chains.py "C:/Users/shoha/OneDrive/Desktop/strip data/code/bootstrap_chains.py"
git add bootstrap_chains.py tests/test_bootstrap_chains_cli.py
git commit -m "feat(pipeline): bootstrap_chains CLI emitting chains.json"
```

---

### Task 5: load_batch populates chain_id for every restaurant

Today `load_batch.py` builds chains from the source `chain_code` (lines ~287-296) and sets `chain_id` only for restaurants that have one. Both must change.

**Files:**
- Modify: `load_batch.py` (chain block ~lines 287-296, `rest_values` ~line 128, `main()` args)
- Test: `tests/test_load_batch_chains.py`

**Interfaces:**
- Consumes: `chains.json` from Task 4.
- Produces: `upsert_chains(db, brands: list[dict]) -> dict[str, int]` mapping
  `source_restaurant_code -> chain_id`, covering every member code.
  After a load, **every** `restaurants.chain_id` is non-NULL and `restaurant_chains.chain_code` holds the brand slug.

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_batch_chains.py`:

```python
from sqlalchemy import text


def test_every_restaurant_gets_a_chain_id(temp_db, db_session, tmp_path):
    """Standalone restaurants must be brands of one -- chain_id is never NULL."""
    import models
    from load_batch import upsert_chains

    chains = [
        {"slug": "waffle-up", "name": "Waffle Up", "member_codes": ["b41r", "fjsq"]},
        {"slug": "niribily", "name": "Niribily", "member_codes": ["avx4"]},
    ]
    for code, name in [("b41r", "Waffle Up - Dhanmondi"), ("fjsq", "Waffle Up"),
                       ("avx4", "Niribily Hotel & Restaurant")]:
        db_session.add(models.Restaurant(source_restaurant_code=code, name=name))
    db_session.commit()

    code_to_chain_id = upsert_chains(db_session, chains)
    for code in ("b41r", "fjsq", "avx4"):
        db_session.execute(
            text("update restaurants set chain_id=:cid where source_restaurant_code=:c"),
            {"cid": code_to_chain_id[code], "c": code},
        )
    db_session.commit()

    assert db_session.execute(
        text("select count(*) from restaurants where chain_id is null")).scalar() == 0
    # the two Waffle Up branches share one brand
    assert code_to_chain_id["b41r"] == code_to_chain_id["fjsq"]
    assert code_to_chain_id["avx4"] != code_to_chain_id["b41r"]
    # brand slug is stored as chain_code
    slugs = {r[0] for r in db_session.execute(text("select chain_code from restaurant_chains"))}
    assert slugs == {"waffle-up", "niribily"}


def test_upsert_chains_is_idempotent(temp_db, db_session):
    from load_batch import upsert_chains
    chains = [{"slug": "waffle-up", "name": "Waffle Up", "member_codes": ["b41r"]}]
    first = upsert_chains(db_session, chains)
    db_session.commit()
    second = upsert_chains(db_session, chains)
    db_session.commit()
    assert first == second
    from sqlalchemy import text as t
    assert db_session.execute(t("select count(*) from restaurant_chains")).scalar() == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_load_batch_chains.py -v`
Expected: FAIL — `ImportError: cannot import name 'upsert_chains' from 'load_batch'`

- [ ] **Step 3: Implement**

In `load_batch.py`, add this function above `main()`:

```python
def upsert_chains(db, brands: list[dict]) -> dict[str, int]:
    """Upsert restaurant_chains keyed by brand slug (stored in chain_code).
    Returns {source_restaurant_code: chain_id} covering EVERY member code."""
    existing = {c.chain_code: c.id for c in db.query(models.RestaurantChain).all()}
    missing = [{"chain_code": b["slug"], "name": b["name"]}
               for b in brands if b["slug"] not in existing]
    for row in _bulk_insert_returning(db, models.RestaurantChain, missing,
                                      models.RestaurantChain.chain_code,
                                      models.RestaurantChain.id):
        existing[row.chain_code] = row.id
    return {code: existing[b["slug"]] for b in brands for code in b["member_codes"]}
```

Replace the old chain block in `main()` (the `chain_rows` / `chain_id` / `missing_chain` lines that read `r["chain_code"]`) with:

```python
        # ---- Brands (chains) -------------------------------------------
        # Source chain_code is unreliable (~21% wrong); chains.json from
        # bootstrap_chains.py is the truth. Every restaurant gets a chain_id.
        brands = json.load(open(args.chains, encoding="utf-8"))
        code_to_chain_id = upsert_chains(db, brands)
```

In `rest_values(r)`, replace the `chain_id` line with:

```python
                "chain_id": code_to_chain_id.get(r.get("source_restaurant_code")),
```

Add the CLI argument in `main()` next to the other `ap.add_argument` calls:

```python
    ap.add_argument("--chains", default="v2_output/chains.json",
                    help="bootstrap_chains.py output; every restaurant gets a brand")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_load_batch_chains.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add load_batch.py tests/test_load_batch_chains.py
git commit -m "feat(pipeline): load brands from chains.json, chain_id for every restaurant"
```

---

### Task 6: Canonical promotion counts brands, not branches

**Files:**
- Modify: `bootstrap_canonical_dishes.py` (`build_canonical_dishes` ~lines 217-262, `main()` ~lines 265-294)
- Test: `tests/test_canonical_brands.py`

**Interfaces:**
- Consumes: `chains.json` (Task 4).
- Produces: `build_canonical_dishes(products, code_to_brand)` — note the **new second parameter**
  `code_to_brand: dict[str, str]` mapping `source_restaurant_code -> brand slug`. `restaurant_count`
  in the output now means **brand count**.

**Do not touch** the fuzzy merge, spelling map, protein signature, or modifier logic.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_canonical_brands.py`:

```python
from bootstrap_canonical_dishes import build_canonical_dishes


def _p(pid, name, code, food_type="Pizza", price=100.0):
    return {"product_id": pid, "name": name, "source_restaurant_code": code,
            "restaurant": code, "food_type": food_type, "sub_type": None,
            "cuisine": "Italian", "category": "Main Dish", "price_bdt": price}


def test_same_dish_across_branches_of_one_brand_is_not_canonical():
    """Three Domino's branches selling Margherita is ONE brand -- nothing to
    compare, so it must not be promoted."""
    products = [_p(1, "Margherita", "gs3j"), _p(2, "Margherita", "wteu"),
                _p(3, "Margherita", "s1b9")]
    code_to_brand = {"gs3j": "domino-s-pizza", "wteu": "domino-s-pizza", "s1b9": "domino-s-pizza"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert dishes == []


def test_same_dish_across_two_brands_is_canonical():
    products = [_p(1, "Margherita", "gs3j"), _p(2, "Margherita", "s8mp")]
    code_to_brand = {"gs3j": "domino-s-pizza", "s8mp": "bella-italia"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1
    assert dishes[0]["restaurant_count"] == 2


def test_restaurant_count_counts_brands_not_branches():
    """Two Domino's branches + one Bella Italia == 2 brands, not 3."""
    products = [_p(1, "Margherita", "gs3j"), _p(2, "Margherita", "wteu"),
                _p(3, "Margherita", "s8mp")]
    code_to_brand = {"gs3j": "domino-s-pizza", "wteu": "domino-s-pizza", "s8mp": "bella-italia"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1
    assert dishes[0]["restaurant_count"] == 2
    assert dishes[0]["product_count"] == 3


def test_two_standalone_restaurants_still_qualify():
    products = [_p(1, "Margherita", "aaaa"), _p(2, "Margherita", "bbbb")]
    code_to_brand = {"aaaa": "brand-a", "bbbb": "brand-b"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1


def test_fuzzy_merge_still_works():
    """Regression guard: spelling variants must still merge across brands."""
    products = [_p(1, "Chicken Biryani", "aaaa", food_type="Rice"),
                _p(2, "Chicken Biriyani", "bbbb", food_type="Rice")]
    code_to_brand = {"aaaa": "brand-a", "bbbb": "brand-b"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_canonical_brands.py -v`
Expected: FAIL — `TypeError: build_canonical_dishes() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Implement**

In `bootstrap_canonical_dishes.py`, change the constant and signature:

```python
MIN_BRANDS = 2  # replaces MIN_RESTAURANTS: comparison is across BRANDS, not branches
```

Change `build_canonical_dishes` to accept the mapping and promote on brands:

```python
def build_canonical_dishes(products: list[dict], code_to_brand: dict[str, str]) -> tuple[list[dict], int]:
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for p in products:
        if p.get("food_type") in EXCLUDED_FOOD_TYPES or p.get("food_type") is None:
            continue
        match_key = canonical_match_key(p.get("name", ""))
        if not match_key:
            continue
        groups[(p["food_type"], match_key)].append(p)

    def brand_of(item: dict) -> str:
        code = item.get("source_restaurant_code")
        # unmapped code -> treat the restaurant as its own brand
        return code_to_brand.get(code, f"__unmapped__{code}")

    promoted: list[tuple[tuple[str, str], list[dict]]] = []
    for key, items in groups.items():
        if len({brand_of(x) for x in items}) >= MIN_BRANDS:
            promoted.append((key, items))
```

Keep the rest of the function unchanged (fuzzy merge, majority vote), but replace the
`restaurants = sorted(...)` line and the `restaurant_count` value in the output dict:

```python
        brands = sorted({brand_of(x) for x in items})
        ...
            "restaurant_count": len(brands),  # brands, not branches
```

In `main()`, load the mapping and pass it through:

```python
    ap.add_argument("--chains", default="v2_output/chains.json")
    ...
    with open(args.chains, encoding="utf-8") as fh:
        brands_json = json.load(fh)
    code_to_brand = {code: b["slug"] for b in brands_json for code in b["member_codes"]}

    canonical_dishes, merge_count = build_canonical_dishes(products, code_to_brand)
```

Update the print label:

```python
    print(f"Canonical dishes created:  {len(canonical_dishes)}  (2+ distinct BRANDS)")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_canonical_brands.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cp bootstrap_canonical_dishes.py "C:/Users/shoha/OneDrive/Desktop/strip data/code/bootstrap_canonical_dishes.py"
git add bootstrap_canonical_dishes.py tests/test_canonical_brands.py
git commit -m "feat(pipeline): promote canonical dishes on 2+ brands, not branches"
```

---

### Task 7: API counts distinct brands for restaurant_count

`_canonical_match_stats_batch` counts `distinct Product.restaurant_id`, which counts branches. It must count brands.

**Files:**
- Modify: `dish_detail.py` (`_canonical_match_stats_batch`, the `product_stats` query)
- Test: `tests/test_canonical_api_brands.py`

**Interfaces:**
- Consumes: `restaurants.chain_id` populated for every restaurant (Task 5).
- Produces: no signature change. `CanonicalDishMatch.restaurant_count` now means brand count.

- [ ] **Step 1: Write the failing test**

Create `tests/test_canonical_api_brands.py`:

```python
from sqlalchemy import text


def test_restaurant_count_counts_brands_not_branches(temp_db, db_session):
    """Two Domino's branches + one Bella Italia serving the same canonical
    dish must report restaurant_count == 2."""
    import models
    from dish_detail import _canonical_match_stats_batch

    dom = models.RestaurantChain(chain_code="domino-s-pizza", name="Domino's Pizza")
    bel = models.RestaurantChain(chain_code="bella-italia", name="Bella Italia")
    db_session.add_all([dom, bel])
    db_session.flush()

    r1 = models.Restaurant(source_restaurant_code="gs3j", name="Domino's Dhanmondi", chain_id=dom.id)
    r2 = models.Restaurant(source_restaurant_code="wteu", name="Domino's Gulshan", chain_id=dom.id)
    r3 = models.Restaurant(source_restaurant_code="s8mp", name="Bella Italia", chain_id=bel.id)
    db_session.add_all([r1, r2, r3])
    db_session.flush()

    cd = models.CanonicalDish(name="Margherita")
    db_session.add(cd)
    db_session.flush()

    for i, r in enumerate([r1, r2, r3], start=1):
        db_session.add(models.Product(source_product_id=1000 + i, restaurant_id=r.id,
                                      name="Margherita", base_price_bdt=199,
                                      canonical_dish_id=cd.id))
    db_session.commit()

    (match,) = _canonical_match_stats_batch(db_session, [cd])
    assert match.restaurant_count == 2, "counted branches instead of brands"
    assert match.dish_count == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_canonical_api_brands.py -v`
Expected: FAIL — `assert 3 == 2` (it currently counts branches).

- [ ] **Step 3: Implement**

In `dish_detail.py`, inside `_canonical_match_stats_batch`, change the `product_stats` query to join
`Restaurant` and count distinct `chain_id`:

```python
    product_stats = {
        row[0]: (row[1], row[2], row[3], row[4])
        for row in db.query(
            models.Product.canonical_dish_id,
            # brands, not branches: three Domino's branches are one restaurant
            # to a diner comparing prices.
            func.count(func.distinct(models.Restaurant.chain_id)),
            func.count(models.Product.id),
            func.min(models.Product.base_price_bdt),
            func.max(models.Product.base_price_bdt),
        )
        .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
        .filter(
            models.Product.canonical_dish_id.in_(ids),
            models.Product.is_active.is_(True),
        )
        .group_by(models.Product.canonical_dish_id)
        .all()
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_canonical_api_brands.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the whole suite for regressions**

Run: `python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dish_detail.py tests/test_canonical_api_brands.py
git commit -m "feat(api): canonical restaurant_count counts brands, not branches"
```

---

### Task 8: Owner review gate, full pipeline run, reload, verify

**This task is gated on the owner.** Task 4's `--review` output is the artifact they review; anything wrong gets pinned into `BRAND_OVERRIDES` before the real load.

**Files:**
- Modify: `bootstrap_chains.py` (`BRAND_OVERRIDES`, only if review finds errors)

**Interfaces:**
- Consumes: everything above.
- Produces: a reloaded Railway DB where every restaurant has a brand and canonical dishes count brands.

- [ ] **Step 1: Generate the review list for the owner**

```bash
cd "C:/Users/shoha/OneDrive/Desktop/strip data/code"
PYTHONIOENCODING=utf-8 python "C:/Users/shoha/Documents/GitHub/Khawon/khawon-api/bootstrap_chains.py" \
  --restaurants "v2_output/restaurants_*_restaurants.json" --review > brand_review.txt
```

Expected: `Brands: 383`, `multi-location: 52`. Hand `brand_review.txt` to the owner.

- [ ] **Step 2: Apply any corrections the owner reports**

For each wrong group, add a pin to `BRAND_OVERRIDES` in `bootstrap_chains.py`. Force a SPLIT by giving
a restaurant its own slug; force a MERGE by giving two restaurants the same slug:

```python
BRAND_OVERRIDES: dict[str, str] = {
    # "code": "brand-slug",
}
```

Then re-run Step 1 and confirm the group is right. If a canary in
`tests/test_bootstrap_chains_cli.py` (383/451/52) now legitimately changes, update the constant in the
test **and** in this plan's Global Constraints, and say so in the commit message.

- [ ] **Step 3: Regenerate chains.json and canonical_dishes.json**

```bash
cd "C:/Users/shoha/OneDrive/Desktop/strip data/code"
PYTHONIOENCODING=utf-8 python bootstrap_chains.py --restaurants "v2_output/restaurants_*_restaurants.json" --out v2_output/chains.json
PYTHONIOENCODING=utf-8 python bootstrap_canonical_dishes.py v2_output/canonical_dishes.json --input v2_output/consolidated.json --chains v2_output/chains.json
```

Expected: canonical count drops from ~2,547 to roughly **1,500-1,600** (the spec predicts ~1,519). A
number near 2,547 means the brand mapping did not reach the promotion rule.

- [ ] **Step 4: Reload the Railway database**

The DB is reset and reloaded from pipeline output; this is expected and non-destructive to anything
irreplaceable (users/reviews are empty). **Confirm with the owner before running.**

```bash
cd "C:/Users/shoha/Documents/GitHub/Khawon/khawon-api"
D="C:/Users/shoha/OneDrive/Desktop/strip data/code/v2_output"
PYTHONIOENCODING=utf-8 python load_batch.py "$D/consolidated.json" "$D/canonical_dishes.json" "$D/restaurants_*_restaurants.json" --chains "$D/chains.json" --area Dhanmondi
```

Note: `load_batch.py` tags every restaurant in the run with a single `--area`. Run it once per area
(`--area Gulshan`, `--area Uttara` with the matching globs) exactly as before.

- [ ] **Step 5: Verify against the live database**

```bash
cd "C:/Users/shoha/Documents/GitHub/Khawon/khawon-api"
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'.')
from sqlalchemy import text
from database import engine
with engine.connect() as c:
    print('restaurants:', c.execute(text('select count(*) from restaurants')).scalar())
    print('chain_id NULL (must be 0):', c.execute(text('select count(*) from restaurants where chain_id is null')).scalar())
    print('brands:', c.execute(text('select count(*) from restaurant_chains')).scalar())
    print('multi-location brands:', c.execute(text('select count(*) from (select chain_id from restaurants group by chain_id having count(*)>1) x')).scalar())
    print('canonical dishes:', c.execute(text('select count(*) from canonical_dishes')).scalar())
    print('Waffle Up branches under one brand:', c.execute(text(
      \"select count(*) from restaurants r join restaurant_chains rc on rc.id=r.chain_id where rc.chain_code='waffle-up'\")).scalar())
"
```

Expected: `restaurants: 451`, `chain_id NULL: 0`, `brands: 383`, `multi-location brands: 52`,
`canonical dishes: ~1500-1600`, `Waffle Up branches: 4`.

- [ ] **Step 6: Commit**

```bash
git add bootstrap_chains.py tests/
git commit -m "chore(pipeline): apply owner brand review, reload with brand identity"
```

---

## Verification of done

- Every restaurant has a `chain_id`; `restaurant_chains.chain_code` holds brand slugs.
- Waffle Up (4), Thai Bistro (2), New Hanif Biryani (2), Rice & More (2) each form one brand despite
  broken source `chain_code`s.
- Canonical dishes count brands; a dish sold only at branches of one chain is no longer canonical
  (and remains searchable as a flat dish, since search already surfaces non-canonical dishes).
- `python -m pytest tests/ -v` passes.

## Follow-on (separate plan)

Spec phases 3-4: `products.normalized_name` column, brand-grouped search cards (price range +
availability badge), brand-level review pooling. Phase 5 (geo/near-me) remains deferred.
