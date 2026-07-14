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
from datetime import datetime, timezone

from sqlalchemy import bindparam, delete, insert, update

from database import SessionLocal
import models


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("products_path")          # consolidated.json
    ap.add_argument("canonical_path")         # canonical_dishes.json
    ap.add_argument("restaurants_glob")       # glob for *_restaurants.json
    ap.add_argument("--area", default="Dhanmondi")
    args = ap.parse_args()

    with open(args.products_path, encoding="utf-8") as f:
        products = json.load(f)
    with open(args.canonical_path, encoding="utf-8") as f:
        canonical = json.load(f)
    restaurants = []
    for path in sorted(glob.glob(args.restaurants_glob)):
        with open(path, encoding="utf-8") as f:
            restaurants.extend(json.load(f))

    db = SessionLocal()
    stats = collections.Counter()
    try:
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

        # ---- Chains -----------------------------------------------------
        chain_rows = {r["chain_code"]: r.get("chain_name") for r in restaurants
                      if r.get("chain_code")}
        chain_id = {r.chain_code: r.id for r in db.query(models.RestaurantChain).all()}
        missing_chain = [{"chain_code": c, "name": n or c}
                         for c, n in chain_rows.items() if c not in chain_id]
        for row in _bulk_insert_returning(db, models.RestaurantChain, missing_chain,
                                          models.RestaurantChain.chain_code,
                                          models.RestaurantChain.id):
            chain_id[row.chain_code] = row.id

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
                "area": args.area,
                "chain_id": chain_id.get(r.get("chain_code")),
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
            cols = [k for k in upd_rest[0] if k not in ("_id", "source_restaurant_code")]
            db.execute(
                update(models.Restaurant.__table__)
                .where(models.Restaurant.__table__.c.id == bindparam("_id"))
                .values({c: bindparam(c) for c in cols}),
                upd_rest,
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

        # ---- Products: upsert by source_product_id ----------------------
        def prod_values(p, rid):
            return {
                "source_product_id": p["product_id"],
                "restaurant_id": rid,
                "name": p["name"],
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
        existing_prod = {spid: pid for spid, pid in db.query(
            models.Product.source_product_id, models.Product.id
        )}
        new_prod, upd_prod, seen_spids = [], [], set()
        for p in products:
            rid = code_to_id.get(p.get("source_restaurant_code"))
            if rid is None:
                stats["skipped_no_restaurant"] += 1
                continue
            spid = p["product_id"]
            seen_spids.add(spid)
            vals = prod_values(p, rid)
            if spid in existing_prod:
                upd_prod.append({**vals, "_id": existing_prod[spid]})
                stats["prod_updated"] += 1
            else:
                new_prod.append(vals)
                stats["prod_created"] += 1

        prod_id_by_spid = dict(existing_prod)
        for row in _bulk_insert_returning(db, models.Product, new_prod,
                                          models.Product.source_product_id, models.Product.id):
            prod_id_by_spid[row.source_product_id] = row.id
        if upd_prod:
            cols = [k for k in upd_prod[0] if k not in ("_id", "source_product_id")]
            db.execute(
                update(models.Product.__table__)
                .where(models.Product.__table__.c.id == bindparam("_id"))
                .values({c: bindparam(c) for c in cols}),
                upd_prod,
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
        touched_pids = [prod_id_by_spid[s] for s in seen_spids if s in prod_id_by_spid]
        if touched_pids:
            db.execute(delete(models.ProductVariation).where(
                models.ProductVariation.product_id.in_(touched_pids)))
            db.execute(delete(models.ProductFlavorTag).where(
                models.ProductFlavorTag.product_id.in_(touched_pids)))
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
            db.execute(
                update(models.Product.__table__)
                .where(models.Product.__table__.c.id == bindparam("_id"))
                .values(canonical_dish_id=bindparam("cdid")),
                link_updates)
        stats["canonical_created"] = len(created)
        stats["products_linked"] = len(link_updates)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Restaurants: {stats['rest_created']} created, {stats['rest_updated']} updated")
    print(f"Products: {stats['prod_created']} created, {stats['prod_updated']} updated, "
          f"{stats['prod_deactivated']} deactivated")
    print(f"Canonical dishes: {stats['canonical_created']} created, "
          f"{stats['products_linked']} products linked")
    if stats["skipped_no_restaurant"]:
        print(f"Skipped (no restaurant): {stats['skipped_no_restaurant']}")


if __name__ == "__main__":
    main()
