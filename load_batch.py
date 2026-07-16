"""Load the v2 pipeline output into the schema.sql database.

USAGE:
    python load_batch.py consolidated.json canonical_dishes.json RESTAURANTS_GLOB [--area Dhanmondi]

    consolidated.json   - consolidate_variants.py output (products; each carries
                          restaurant name + source_restaurant_code, classification
                          fields food_type/sub_type/category/cuisine/flavor_tags,
                          and a variations[] array).
    canonical_dishes.json - bootstrap_canonical_dishes.py output.
    RESTAURANTS_GLOB    - glob for the classify *_restaurants.json files that hold
                          restaurant detail (address/coords/rating/chain/...),
                          e.g. "v2_output/restaurants_*_restaurants.json".

Idempotent: restaurants upsert by source_restaurant_code, products by
source_product_id. Re-run marks vanished products is_active=false (never
deletes - product_reviews would cascade away). Bulk Core inserts for
high-latency (Railway proxy) performance.

SQL-first: assumes schema.sql has already created the tables. This only
inserts/updates rows; it does not create schema.
"""
import argparse
import glob
import json
import collections
import re
from datetime import datetime, timezone

from sqlalchemy import bindparam, delete, insert, select, text, update

from bootstrap_canonical_dishes import canonical_match_key
from database import SessionLocal
import models

AREA_FROM_PATH = re.compile(r"restaurants_([a-z]+)_", re.IGNORECASE)
AREA_LABELS = {"dhanmondi": "Dhanmondi", "gulshan": "Gulshan", "uttara": "Uttara"}


def _area_from_path(path: str, fallback: str) -> str:
    match = AREA_FROM_PATH.search(path.replace("\\", "/"))
    if not match:
        return fallback
    return AREA_LABELS.get(match.group(1).lower(), match.group(1).capitalize())


def _ts_log(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[load_batch {ts}] {message}", flush=True)


def _commit_phase(db, label: str) -> None:
    _ts_log(f"committing: {label}...")
    db.commit()
    _ts_log(f"committed: {label}")


def _norm_price(value) -> float:
    if value is None:
        return 0.0
    return float(value)


# NOTE: every column written by prod_values must appear in BOTH signature
# builders below, and be SELECTed into existing_rows. A column missing here can
# never be backfilled: the row compares equal, the write is skipped, and the
# column stays NULL forever no matter how many times you reload.
def _prod_signature(vals: dict) -> tuple:
    return (
        vals["restaurant_id"],
        vals["name"],
        vals.get("description"),
        _norm_price(vals.get("base_price_bdt")),
        vals.get("image_url"),
        bool(vals.get("is_sold_out", False)),
        vals.get("category_id"),
        vals.get("cuisine_id"),
        vals.get("food_type_id"),
        vals.get("food_sub_type_id"),
        vals.get("normalized_name"),
    )


def _prod_signature_from_row(row) -> tuple:
    return (
        row.restaurant_id,
        row.name,
        row.description,
        _norm_price(row.base_price_bdt),
        row.image_url,
        bool(row.is_sold_out),
        row.category_id,
        row.cuisine_id,
        row.food_type_id,
        row.food_sub_type_id,
        row.normalized_name,
    )


def _touch_batch_products(db, restaurant_ids: list[int], seen_at: datetime) -> None:
    if not restaurant_ids:
        return
    _ts_log(f"touching last_seen_at for products in {len(restaurant_ids)} restaurants...")
    db.execute(
        text(
            """
            UPDATE products
            SET last_seen_at = :seen_at, is_active = TRUE
            WHERE restaurant_id = ANY(CAST(:restaurant_ids AS int[]))
            """
        ),
        {"seen_at": seen_at, "restaurant_ids": restaurant_ids},
    )


def _bulk_update_products_unnest(
    db, rows: list[dict], *, label: str, chunk_size: int = 1000
) -> None:
    """Single-round-trip product updates via unnest (fast over Railway proxy).

    WARNING: this SQL hardcodes its column list and the unnest arrays are
    positional. Adding a column to prod_values means touching FIVE places --
    prod_values, _prod_signature, _prod_signature_from_row, the existing_rows
    SELECT, and here (SET clause + CAST array + d(...) alias + params dict, all
    in the same order). Miss this one and the load cheerfully reports "N
    updated" while never writing the column. See
    test_bulk_update_actually_persists_normalized_name.
    """
    if not rows:
        return
    stmt = text(
        """
        UPDATE products AS p SET
            restaurant_id = d.restaurant_id,
            name = d.name,
            description = d.description,
            base_price_bdt = d.base_price_bdt,
            image_url = d.image_url,
            is_sold_out = d.is_sold_out,
            category_id = d.category_id,
            cuisine_id = d.cuisine_id,
            food_type_id = d.food_type_id,
            food_sub_type_id = d.food_sub_type_id,
            normalized_name = d.normalized_name,
            is_active = TRUE,
            last_seen_at = :seen_at
        FROM unnest(
            CAST(:ids AS int[]),
            CAST(:restaurant_ids AS int[]),
            CAST(:names AS text[]),
            CAST(:descriptions AS text[]),
            CAST(:prices AS numeric[]),
            CAST(:image_urls AS text[]),
            CAST(:is_sold_out AS boolean[]),
            CAST(:category_ids AS int[]),
            CAST(:cuisine_ids AS int[]),
            CAST(:food_type_ids AS int[]),
            CAST(:food_sub_type_ids AS int[]),
            CAST(:normalized_names AS text[])
        ) AS d(
            id, restaurant_id, name, description, base_price_bdt, image_url,
            is_sold_out, category_id, cuisine_id, food_type_id, food_sub_type_id,
            normalized_name
        )
        WHERE p.id = d.id
        """
    )
    total = len(rows)
    _ts_log(f"{label}: 0/{total}")
    for start in range(0, total, chunk_size):
        chunk = rows[start : start + chunk_size]
        seen_at = chunk[0]["last_seen_at"]
        db.execute(
            stmt,
            {
                "seen_at": seen_at,
                "ids": [row["_id"] for row in chunk],
                "restaurant_ids": [row["restaurant_id"] for row in chunk],
                "names": [row["name"] for row in chunk],
                "descriptions": [row.get("description") for row in chunk],
                "prices": [_norm_price(row.get("base_price_bdt")) for row in chunk],
                "image_urls": [row.get("image_url") for row in chunk],
                "is_sold_out": [bool(row.get("is_sold_out", False)) for row in chunk],
                "category_ids": [row.get("category_id") for row in chunk],
                "cuisine_ids": [row.get("cuisine_id") for row in chunk],
                "food_type_ids": [row.get("food_type_id") for row in chunk],
                "food_sub_type_ids": [row.get("food_sub_type_id") for row in chunk],
                "normalized_names": [row.get("normalized_name") for row in chunk],
            },
        )
        _ts_log(f"{label}: {min(start + chunk_size, total)}/{total}")


def _bulk_update_by_id(db, table, rows: list[dict], *, label: str, chunk_size: int = 500) -> None:
    """Chunked executemany updates so Railway proxy loads show steady progress."""
    if not rows:
        return
    cols = [k for k in rows[0] if k != "_id"]
    stmt = (
        update(table)
        .where(table.c.id == bindparam("_id"))
        .values({c: bindparam(c) for c in cols})
    )
    total = len(rows)
    for start in range(0, total, chunk_size):
        chunk = rows[start : start + chunk_size]
        db.execute(stmt, chunk)
        _ts_log(f"{label}: {min(start + chunk_size, total)}/{total}")


def _bulk_set_canonical_links(db, link_updates: list[dict]) -> None:
    if not link_updates:
        return
    stmt = text(
        """
        UPDATE products AS p
        SET canonical_dish_id = data.cdid
        FROM unnest(CAST(:pids AS int[]), CAST(:cdids AS int[])) AS data(pid, cdid)
        WHERE p.id = data.pid
        """
    )
    chunk_size = 3000
    for start in range(0, len(link_updates), chunk_size):
        chunk = link_updates[start : start + chunk_size]
        db.execute(
            stmt,
            {"pids": [row["_id"] for row in chunk], "cdids": [row["cdid"] for row in chunk]},
        )
        _ts_log(f"canonical links: {min(start + chunk_size, len(link_updates))}/{len(link_updates)}")


def _bulk_insert_returning(db, model, rows, *return_cols):
    if not rows:
        return []
    return list(db.execute(insert(model).returning(*return_cols), rows))


def _get_or_create_lookup(db, model, names, name_col="name"):
    """Return {name: id} for a simple unique-name lookup table, inserting the
    missing ones."""
    existing = {getattr(r, name_col): r.id for r in db.query(model).all()}
    missing = [n for n in names if n and n not in existing]
    for row in _bulk_insert_returning(db, model, [{name_col: n} for n in missing],
                                      getattr(model, name_col), model.id):
        existing[getattr(row, name_col)] = row.id
    return existing


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


def delete_orphan_chains(db) -> int:
    """Drop restaurant_chains rows no restaurant points at.

    Brands are re-derived every load, so a previous load's rows (e.g. the
    foodpanda-era chain_code keys) linger unreferenced once every restaurant
    is repointed at a brand slug. Returns the number deleted."""
    referenced = select(models.Restaurant.chain_id).where(
        models.Restaurant.chain_id.isnot(None)
    )
    result = db.execute(
        delete(models.RestaurantChain).where(
            models.RestaurantChain.id.notin_(referenced)
        )
    )
    return result.rowcount


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("products_path")          # consolidated.json
    ap.add_argument("canonical_path")         # canonical_dishes.json
    ap.add_argument("restaurants_glob")       # glob for *_restaurants.json
    ap.add_argument("--area", default="Dhanmondi")
    ap.add_argument("--chains", default="v2_output/chains.json",
                    help="bootstrap_chains.py output; every restaurant gets a brand")
    args = ap.parse_args()

    with open(args.products_path, encoding="utf-8") as f:
        products = json.load(f)
    with open(args.canonical_path, encoding="utf-8") as f:
        canonical = json.load(f)
    restaurants = []
    for path in sorted(glob.glob(args.restaurants_glob)):
        area = _area_from_path(path, args.area)
        with open(path, encoding="utf-8") as f:
            for row in json.load(f):
                restaurants.append({**row, "_area": area})

    _ts_log(
        f"loaded {len(products)} products, {len(canonical)} canonical dishes, "
        f"{len(restaurants)} restaurants"
    )

    db = SessionLocal()
    stats = collections.Counter()
    try:
        _ts_log("phase 1/3: lookups, chains, restaurants")
        # ---- Lookup tables ----------------------------------------------
        cuisine_names = {p["cuisine"] for p in products if p.get("cuisine")}
        cat_names = {p["category"] for p in products if p.get("category")}
        ft_names = {p["food_type"] for p in products if p.get("food_type")}
        flavor_slugs = {t for p in products for t in (p.get("flavor_tags") or [])}

        cu_id = _get_or_create_lookup(db, models.Cuisine, cuisine_names)
        cat_id = _get_or_create_lookup(db, models.FoodCategory, cat_names)
        ft_id = _get_or_create_lookup(db, models.FoodType, ft_names)

        # food_sub_types are scoped under a food_type: key (food_type_id, name)
        need_sub = {(p["food_type"], p["sub_type"]) for p in products
                    if p.get("food_type") and p.get("sub_type")}
        sub_id = {(r.food_type_id, r.name): r.id for r in db.query(models.FoodSubType).all()}
        missing_sub = [{"food_type_id": ft_id[ft], "name": st}
                       for (ft, st) in need_sub if (ft_id[ft], st) not in sub_id]
        for row in _bulk_insert_returning(db, models.FoodSubType, missing_sub,
                                          models.FoodSubType.food_type_id,
                                          models.FoodSubType.name, models.FoodSubType.id):
            sub_id[(row.food_type_id, row.name)] = row.id

        # flavor_tags (slug + label; label = slug prettified)
        fl_existing = {r.slug: r.id for r in db.query(models.FlavorTag).all()}
        missing_fl = [{"slug": s, "label": s.replace("_", " ").title()}
                      for s in flavor_slugs if s not in fl_existing]
        for row in _bulk_insert_returning(db, models.FlavorTag, missing_fl,
                                          models.FlavorTag.slug, models.FlavorTag.id):
            fl_existing[row.slug] = row.id

        # ---- Brands (chains) --------------------------------------------
        # The source chain_code is unreliable (~21% of real brands are split or
        # untagged), so chains.json from bootstrap_chains.py is the truth here.
        # It covers EVERY restaurant - a standalone one is a brand of one - so
        # downstream code can always GROUP BY chain_id with no special-casing.
        with open(args.chains, encoding="utf-8") as f:
            brands = json.load(f)
        code_to_chain_id = upsert_chains(db, brands)

        # ---- Restaurants: upsert by source_restaurant_code --------------
        def rest_values(r):
            coords = r.get("coordinates") or {}
            images = r.get("images") or {}
            return {
                "source_restaurant_code": r.get("source_restaurant_code"),
                "name": r["name"],
                "address": r.get("address"),
                "latitude": coords.get("latitude"),
                "longitude": coords.get("longitude"),
                "old_rating": r.get("rating"),
                "old_review_count": r.get("review_number"),
                "budget_tier": r.get("budget"),
                "phone": r.get("phone"),
                "city": r.get("city") or "Dhaka",
                "area": r.get("_area") or args.area,
                "chain_id": code_to_chain_id.get(r.get("source_restaurant_code")),
                "hero_image_url": images.get("hero"),
                "logo_image_url": images.get("logo"),
                "google_place_id": r.get("google_place_id"),
                "match_status": r.get("match_status") or "unmatched",
            }

        codes = [r.get("source_restaurant_code") for r in restaurants]
        code_to_id = {c: i for c, i in db.query(
            models.Restaurant.source_restaurant_code, models.Restaurant.id
        ).filter(models.Restaurant.source_restaurant_code.in_(codes))}

        new_rest, upd_rest = [], []
        for r in restaurants:
            vals = rest_values(r)
            if vals["source_restaurant_code"] in code_to_id:
                upd_rest.append({**vals, "_id": code_to_id[vals["source_restaurant_code"]]})
                stats["rest_updated"] += 1
            else:
                new_rest.append(vals)
                stats["rest_created"] += 1
        for row in _bulk_insert_returning(db, models.Restaurant, new_rest,
                                          models.Restaurant.source_restaurant_code,
                                          models.Restaurant.id):
            code_to_id[row.source_restaurant_code] = row.id
        if upd_rest:
            _bulk_update_by_id(
                db,
                models.Restaurant.__table__,
                upd_rest,
                label="restaurants updated",
            )

        # restaurant_cuisines (rebuild for this batch's restaurants)
        batch_rest_ids = [code_to_id[c] for c in codes if c in code_to_id]
        if batch_rest_ids:
            db.execute(delete(models.RestaurantCuisine).where(
                models.RestaurantCuisine.restaurant_id.in_(batch_rest_ids)))
        rc_rows = []
        for r in restaurants:
            rid = code_to_id.get(r.get("source_restaurant_code"))
            if rid is None:
                continue
            for cname in (r.get("cuisines") or []):
                cid = cu_id.get(cname)
                if cid:
                    rc_rows.append({"restaurant_id": rid, "cuisine_id": cid})
        # dedupe (a restaurant may list a cuisine twice)
        rc_rows = [dict(t) for t in {tuple(sorted(d.items())) for d in rc_rows}]
        if rc_rows:   # rows already cleared above, so plain insert is safe
            db.execute(insert(models.RestaurantCuisine), rc_rows)

        # Every restaurant now points at a brand slug, so any chain row left
        # over from a previous load (e.g. the foodpanda-era chain_code keys) is
        # unreferenced. Must run AFTER the restaurant upsert above.
        stats["orphan_chains_deleted"] = delete_orphan_chains(db)

        _commit_phase(db, "lookups, chains, restaurants")

        _ts_log("phase 2/3: products, variations, flavor tags")
        # ---- Products: upsert by source_product_id ----------------------
        def prod_values(p, rid):
            return {
                "source_product_id": p["product_id"],
                "restaurant_id": rid,
                "name": p["name"],
                # Read-time brand grouping key; same function the canonical
                # bootstrap groups with, so both layers agree.
                "normalized_name": canonical_match_key(p.get("name", "")),
                "description": p.get("description"),
                "base_price_bdt": p.get("price_bdt") or 0,
                "image_url": p.get("image"),
                "is_sold_out": p.get("is_sold_out", False),
                "category_id": cat_id.get(p.get("category")),
                "cuisine_id": cu_id.get(p.get("cuisine")),
                "food_type_id": ft_id.get(p.get("food_type")),
                "food_sub_type_id": sub_id.get((p.get("food_type"), p.get("sub_type")))
                    if p.get("sub_type") else None,
                "is_active": True,
                "last_seen_at": seen_at,
            }

        seen_at = datetime.now(timezone.utc)
        _ts_log("fetching existing products from DB...")
        existing_rows = db.query(
            models.Product.source_product_id,
            models.Product.id,
            models.Product.restaurant_id,
            models.Product.name,
            models.Product.description,
            models.Product.base_price_bdt,
            models.Product.image_url,
            models.Product.is_sold_out,
            models.Product.category_id,
            models.Product.cuisine_id,
            models.Product.food_type_id,
            models.Product.food_sub_type_id,
            models.Product.normalized_name,
        ).all()
        existing_prod = {row.source_product_id: row.id for row in existing_rows}
        existing_sigs = {
            row.source_product_id: _prod_signature_from_row(row) for row in existing_rows
        }
        _ts_log(f"found {len(existing_prod)} existing products; building upsert batches...")
        new_prod, upd_prod, seen_spids = [], [], set()
        for i, p in enumerate(products, start=1):
            if i % 4000 == 0:
                _ts_log(f"scanned products: {i}/{len(products)}")
            rid = code_to_id.get(p.get("source_restaurant_code"))
            if rid is None:
                stats["skipped_no_restaurant"] += 1
                continue
            spid = p["product_id"]
            seen_spids.add(spid)
            vals = prod_values(p, rid)
            if spid in existing_prod:
                if existing_sigs.get(spid) == _prod_signature(vals):
                    stats["prod_unchanged"] += 1
                else:
                    upd_prod.append({**vals, "_id": existing_prod[spid]})
                    stats["prod_updated"] += 1
            else:
                new_prod.append(vals)
                stats["prod_created"] += 1

        _ts_log(
            f"batches ready: {len(new_prod)} new, {len(upd_prod)} changed, "
            f"{stats['prod_unchanged']} unchanged"
        )
        prod_id_by_spid = dict(existing_prod)
        if new_prod:
            _ts_log(f"inserting {len(new_prod)} new products...")
            for row in _bulk_insert_returning(db, models.Product, new_prod,
                                              models.Product.source_product_id, models.Product.id):
                prod_id_by_spid[row.source_product_id] = row.id
        _touch_batch_products(db, batch_rest_ids, seen_at)
        if upd_prod:
            _bulk_update_products_unnest(
                db,
                upd_prod,
                label="products updated",
            )

        # menu lifecycle: products of loaded restaurants not seen this batch -> inactive
        vanished = [pid for spid, pid in existing_prod.items() if spid not in seen_spids]
        # only within the batch's restaurants
        if vanished:
            db.execute(
                update(models.Product.__table__)
                .where(models.Product.__table__.c.id.in_(vanished),
                       models.Product.__table__.c.restaurant_id.in_(batch_rest_ids))
                .values(is_active=False))
        stats["prod_deactivated"] = len(vanished)

        # ---- Variations + flavor tags (rebuild for touched products) ----
        if batch_rest_ids:
            _ts_log("clearing variations + flavor tags for batch restaurants...")
            db.execute(
                text(
                    """
                    DELETE FROM product_variations
                    WHERE product_id IN (
                        SELECT id FROM products
                        WHERE restaurant_id = ANY(CAST(:restaurant_ids AS int[]))
                    )
                    """
                ),
                {"restaurant_ids": batch_rest_ids},
            )
            db.execute(
                text(
                    """
                    DELETE FROM product_flavor_tags
                    WHERE product_id IN (
                        SELECT id FROM products
                        WHERE restaurant_id = ANY(CAST(:restaurant_ids AS int[]))
                    )
                    """
                ),
                {"restaurant_ids": batch_rest_ids},
            )
        _ts_log("rebuilding variations + flavor tags...")
        var_rows, flav_rows = [], []
        for p in products:
            pid = prod_id_by_spid.get(p["product_id"])
            if pid is None:
                continue
            seen_labels = set()
            for i, v in enumerate(p.get("variations") or []):
                label = v.get("label") or "Regular"
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                var_rows.append({"product_id": pid, "label": label,
                                 "price_bdt": v.get("price_bdt") or 0, "sort_order": i})
            for slug in (p.get("flavor_tags") or []):
                fid = fl_existing.get(slug)
                if fid:
                    flav_rows.append({"product_id": pid, "flavor_tag_id": fid})
        flav_rows = [dict(t) for t in {tuple(sorted(d.items())) for d in flav_rows}]
        if var_rows:
            db.execute(insert(models.ProductVariation), var_rows)
        if flav_rows:
            db.execute(insert(models.ProductFlavorTag), flav_rows)

        _commit_phase(db, "products, variations, flavor tags")

        _ts_log("phase 3/3: canonical dishes + product links")
        # ---- Canonical dishes + link products ---------------------------
        # rebuild canonical set (idempotent full replace is simplest and the
        # bootstrap is deterministic). Clear links first, then recreate.
        db.execute(update(models.Product.__table__).values(canonical_dish_id=None))
        db.execute(delete(models.CanonicalDish))
        cd_rows = []
        for c in canonical:
            ft = c.get("food_type")
            st = c.get("sub_type")
            cd_rows.append({
                "name": c["name"],
                "aliases": c.get("aliases") or [],
                "food_type_id": ft_id.get(ft),
                "food_sub_type_id": sub_id.get((ft, st)) if st else None,
                "cuisine_id": cu_id.get(c.get("cuisine")),
                "category_id": cat_id.get(c.get("category")),
            })
        # insert canonicals, keep mapping by list order via RETURNING id + name
        # (name may repeat across food_types, so map by (name, food_type_id))
        created = _bulk_insert_returning(db, models.CanonicalDish, cd_rows,
                                         models.CanonicalDish.id, models.CanonicalDish.name,
                                         models.CanonicalDish.food_type_id)
        key_to_cdid = {(r.name, r.food_type_id): r.id for r in created}
        link_updates = []
        for c in canonical:
            cdid = key_to_cdid.get((c["name"], ft_id.get(c.get("food_type"))))
            if cdid is None:
                continue
            for spid in c.get("member_source_product_ids", []):
                pid = prod_id_by_spid.get(spid)
                if pid is not None:
                    link_updates.append({"_id": pid, "cdid": cdid})
        if link_updates:
            _ts_log(f"linking {len(link_updates)} products to canonical dishes...")
            _bulk_set_canonical_links(db, link_updates)
        stats["canonical_created"] = len(created)
        stats["products_linked"] = len(link_updates)

        _commit_phase(db, "canonical dishes + links")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Restaurants: {stats['rest_created']} created, {stats['rest_updated']} updated")
    print(f"Brands: {stats['orphan_chains_deleted']} orphan chain rows deleted")
    print(f"Products: {stats['prod_created']} created, {stats['prod_updated']} updated, "
          f"{stats['prod_unchanged']} unchanged, {stats['prod_deactivated']} deactivated")
    print(f"Canonical dishes: {stats['canonical_created']} created, "
          f"{stats['products_linked']} products linked")
    if stats["skipped_no_restaurant"]:
        print(f"Skipped (no restaurant): {stats['skipped_no_restaurant']}")


if __name__ == "__main__":
    main()
