"""Load a classify_batch.py + match_google_places.py output pair into the DB.

USAGE:
    python load_batch.py products.json restaurants.json [--area Dhanmondi]

products.json    - classify_batch.py's *_products.json (has source_restaurant_code,
                    full product fields, and food_type_leaf/parent/cuisine/flavor_tags)
restaurants.json - classify_batch.py's *_restaurants.json, after match_google_places.py
                    has filled in google_place_id/match_status

Upserts Restaurant by source_restaurant_code and Product by
(restaurant_id, source_product_id) - safe to re-run on the same files.
FoodType/Cuisine/FlavorTag rows are get-or-created by name.

Written for bulk performance over a high-latency connection (e.g. Railway's
public Postgres proxy, ~220ms/round-trip): new rows go in via a handful of
Core multi-row INSERT ... RETURNING statements rather than one INSERT per row.
Existing rows (only on a re-run) are updated via executemany, which is slower
per-row but rare. A fresh load is therefore all bulk inserts = a few dozen
round-trips total, not tens of thousands.
"""
import argparse
import json

from sqlalchemy import bindparam, delete, insert, update

from database import SessionLocal
import models


def _restaurant_values(r, area):
    coords = r.get("coordinates") or {}
    images = r.get("images") or {}
    return {
        "source_restaurant_code": r.get("source_restaurant_code"),
        "name": r["name"],
        "address": r.get("address"),
        "area": area,
        "latitude": coords.get("latitude"),
        "longitude": coords.get("longitude"),
        "raw_cuisines": r.get("cuisines") or [],
        "foodpanda_rating": r.get("rating"),
        "foodpanda_review_number": r.get("review_number"),
        "budget": r.get("budget"),
        "image_url": images.get("hero"),
        "logo_url": images.get("logo"),
        "chain_name": r.get("chain_name"),
        "chain_code": r.get("chain_code"),
        "google_place_id": r.get("google_place_id"),
        "match_status": r.get("match_status") or "unmatched",
    }


def _product_values(p, restaurant_id, food_type_id):
    return {
        "restaurant_id": restaurant_id,
        "source_product_id": p["product_id"],
        "food_type_id": food_type_id,
        "name": p["name"],
        "description": p.get("description"),
        "price_bdt": p.get("price_bdt"),
        "image_url": p.get("image"),
        "is_sold_out": p.get("is_sold_out", False),
        "category_raw": p.get("category"),
        "dietary_attributes_raw": p.get("dietary_attributes") or [],
        "variations": p.get("variations") or [],
    }


def _bulk_insert_returning(db, model, rows, *return_cols):
    """Bulk-insert `rows` (list of dicts) in one Core statement, returning the
    requested columns. Returns the RETURNING result rows (order not assumed -
    callers key off the returned natural-key columns)."""
    if not rows:
        return []
    stmt = insert(model).returning(*return_cols)
    return list(db.execute(stmt, rows))


def main():
    parser = argparse.ArgumentParser(description="Load a classified/matched batch into the DB.")
    parser.add_argument("products_path")
    parser.add_argument("restaurants_path")
    parser.add_argument("--area", default="Dhanmondi")
    args = parser.parse_args()

    with open(args.products_path, encoding="utf-8") as f:
        products = json.load(f)
    with open(args.restaurants_path, encoding="utf-8") as f:
        restaurants = json.load(f)

    db = SessionLocal()
    stats = {
        "restaurants_created": 0, "restaurants_updated": 0,
        "products_created": 0, "products_updated": 0,
        "food_types_created": 0, "cuisines_created": 0, "flavor_tags_created": 0,
    }

    try:
        # ---- Taxonomy: existing name -> id ----
        ft_id = {name: id_ for name, id_ in db.query(models.FoodType.name, models.FoodType.id)}
        cu_id = {name: id_ for name, id_ in db.query(models.Cuisine.name, models.Cuisine.id)}
        fl_id = {name: id_ for name, id_ in db.query(models.FlavorTag.name, models.FlavorTag.id)}

        # Collect needed taxonomy from the products file.
        ft_parent = {}   # name -> parent_name (None for roots)
        need_cuisines, need_flavors = set(), set()
        for p in products:
            leaf, parent = p.get("food_type_leaf"), p.get("food_type_parent")
            if leaf:
                ft_parent.setdefault(leaf, parent if parent and parent != leaf else None)
            if parent and parent != leaf:
                ft_parent.setdefault(parent, None)
            if p.get("cuisine"):
                need_cuisines.add(p["cuisine"])
            for name in p.get("flavor_tags", []):
                need_flavors.add(name)

        # Insert new roots first (parent None), then new leaves (parent_id resolved).
        new_roots = [n for n, par in ft_parent.items() if par is None and n not in ft_id]
        for row in _bulk_insert_returning(
            db, models.FoodType, [{"name": n} for n in new_roots],
            models.FoodType.name, models.FoodType.id,
        ):
            ft_id[row.name] = row.id
        new_leaves = [n for n, par in ft_parent.items() if par is not None and n not in ft_id]
        for row in _bulk_insert_returning(
            db, models.FoodType,
            [{"name": n, "parent_id": ft_id.get(ft_parent[n])} for n in new_leaves],
            models.FoodType.name, models.FoodType.id,
        ):
            ft_id[row.name] = row.id
        stats["food_types_created"] = len(new_roots) + len(new_leaves)

        new_cuisines = [n for n in need_cuisines if n not in cu_id]
        for row in _bulk_insert_returning(
            db, models.Cuisine, [{"name": n} for n in new_cuisines],
            models.Cuisine.name, models.Cuisine.id,
        ):
            cu_id[row.name] = row.id
        stats["cuisines_created"] = len(new_cuisines)

        new_flavors = [n for n in need_flavors if n not in fl_id]
        for row in _bulk_insert_returning(
            db, models.FlavorTag, [{"name": n} for n in new_flavors],
            models.FlavorTag.name, models.FlavorTag.id,
        ):
            fl_id[row.name] = row.id
        stats["flavor_tags_created"] = len(new_flavors)

        # ---- Restaurants: split new vs existing by source_restaurant_code ----
        codes = [r.get("source_restaurant_code") for r in restaurants]
        code_to_id = {
            code: id_
            for code, id_ in db.query(
                models.Restaurant.source_restaurant_code, models.Restaurant.id
            ).filter(models.Restaurant.source_restaurant_code.in_(codes))
        }
        new_rest_rows, update_rest_rows = [], []
        for r in restaurants:
            vals = _restaurant_values(r, args.area)
            code = vals["source_restaurant_code"]
            if code in code_to_id:
                update_rest_rows.append({**vals, "_id": code_to_id[code]})
                stats["restaurants_updated"] += 1
            else:
                new_rest_rows.append(vals)
                stats["restaurants_created"] += 1

        for row in _bulk_insert_returning(
            db, models.Restaurant, new_rest_rows,
            models.Restaurant.source_restaurant_code, models.Restaurant.id,
        ):
            code_to_id[row.source_restaurant_code] = row.id
        if update_rest_rows:
            cols = [k for k in update_rest_rows[0] if k not in ("_id", "source_restaurant_code")]
            stmt = (
                update(models.Restaurant.__table__)
                .where(models.Restaurant.__table__.c.id == bindparam("_id"))
                .values({c: bindparam(c) for c in cols})
            )
            db.execute(stmt, update_rest_rows)

        # ---- Products: split new vs existing by (restaurant_id, source_product_id) ----
        restaurant_ids = list(code_to_id.values())
        existing_products = {}
        if restaurant_ids:
            for pid, rid, spid in db.query(
                models.Product.id, models.Product.restaurant_id, models.Product.source_product_id
            ).filter(models.Product.restaurant_id.in_(restaurant_ids)):
                existing_products[(rid, spid)] = pid

        new_prod_rows, update_prod_rows = [], []
        # desired joins keyed by product natural key, resolved to product ids after insert
        want_cuisine = {}   # (rid, spid) -> cuisine_id
        want_flavors = {}   # (rid, spid) -> [flavor_id, ...]
        for p in products:
            rid = code_to_id.get(p.get("source_restaurant_code"))
            if rid is None:
                print(f"  [skip] {p['name']}: no restaurant for "
                      f"source_restaurant_code={p.get('source_restaurant_code')!r}")
                continue
            leaf = p.get("food_type_leaf")
            food_type_id = ft_id.get(leaf) if leaf else None
            vals = _product_values(p, rid, food_type_id)
            key = (rid, p["product_id"])
            if key in existing_products:
                update_prod_rows.append({**vals, "_id": existing_products[key]})
                stats["products_updated"] += 1
            else:
                new_prod_rows.append(vals)
                stats["products_created"] += 1
            if p.get("cuisine") and p["cuisine"] in cu_id:
                want_cuisine[key] = cu_id[p["cuisine"]]
            want_flavors[key] = [fl_id[n] for n in p.get("flavor_tags", []) if n in fl_id]

        pid_by_key = dict(existing_products)
        for row in _bulk_insert_returning(
            db, models.Product, new_prod_rows,
            models.Product.id, models.Product.restaurant_id, models.Product.source_product_id,
        ):
            pid_by_key[(row.restaurant_id, row.source_product_id)] = row.id
        if update_prod_rows:
            cols = [k for k in update_prod_rows[0] if k != "_id"]
            stmt = (
                update(models.Product.__table__)
                .where(models.Product.__table__.c.id == bindparam("_id"))
                .values({c: bindparam(c) for c in cols})
            )
            db.execute(stmt, update_prod_rows)

        # ---- Join tables: clear existing products' links, then bulk insert all ----
        updated_ids = [existing_products[k] for k in existing_products]
        if updated_ids:
            db.execute(delete(models.ProductCuisine).where(
                models.ProductCuisine.product_id.in_(updated_ids)))
            db.execute(delete(models.ProductFlavorTag).where(
                models.ProductFlavorTag.product_id.in_(updated_ids)))

        cuisine_join_rows, flavor_join_rows = [], []
        for key, cuisine_id in want_cuisine.items():
            pid = pid_by_key.get(key)
            if pid is not None:
                cuisine_join_rows.append({"product_id": pid, "cuisine_id": cuisine_id})
        for key, flavor_ids in want_flavors.items():
            pid = pid_by_key.get(key)
            if pid is None:
                continue
            for flavor_id in flavor_ids:
                flavor_join_rows.append({"product_id": pid, "flavor_tag_id": flavor_id})
        if cuisine_join_rows:
            db.execute(insert(models.ProductCuisine), cuisine_join_rows)
        if flavor_join_rows:
            db.execute(insert(models.ProductFlavorTag), flavor_join_rows)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Restaurants: {stats['restaurants_created']} created, {stats['restaurants_updated']} updated")
    print(f"Products: {stats['products_created']} created, {stats['products_updated']} updated")
    print(f"New FoodTypes: {stats['food_types_created']}, "
          f"new Cuisines: {stats['cuisines_created']}, "
          f"new FlavorTags: {stats['flavor_tags_created']}")


if __name__ == "__main__":
    main()
