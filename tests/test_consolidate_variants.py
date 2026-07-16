"""Consolidation must group with the SAME key as the canonical bootstrap
(canonical_match_key), so spelling-drifted size variants at one restaurant
merge into variations[] instead of surviving as separate products.

Real cases that motivated this (found in the live DB as intra-branch key
fusions the old consolidation missed):
  'Beef Chaap Polao Half' vs 'Beef Chap Pulao - Full'   (chap/chaap, polao/pulao)
  'Beef Fried Rice' vs 'Fried Rice With Beef'           (token order + 'with')
  'Chocolate Fudge Cookie' vs 'Chocolate Fudge Cookies' (plural)
"""

from consolidate_variants import consolidate


def _p(pid, restaurant, name, price, **kw):
    return {"product_id": pid, "restaurant": restaurant, "name": name,
            "price_bdt": price, "food_type": kw.get("food_type", "Rice"),
            "sub_type": None, "category": "Main Dish", "cuisine": "Bangladeshi",
            "flavor_tags": [], "description": "", "is_sold_out": False,
            "variations": kw.get("variations")}


def test_spelling_drift_size_variants_merge():
    out, groups, rows = consolidate([
        _p(1, "ranna-ghor", "Beef Chaap Polao Half", 170),
        _p(2, "ranna-ghor", "Beef Chap Pulao - Full", 306),
    ])
    assert len(out) == 1, "chap/chaap + polao/pulao drift must not split the dish"
    assert groups == 1 and rows == 2
    labels = {v["label"] for v in out[0]["variations"]}
    assert labels == {"Half", "Full"}
    assert out[0]["price_bdt"] == 170  # cheapest = representative 'from' price


def test_token_order_duplicates_merge():
    out, _, _ = consolidate([
        _p(1, "thai-bistro", "Beef Fried Rice", 275),
        _p(2, "thai-bistro", "Fried Rice With Beef", 275),
    ])
    assert len(out) == 1, "token order + stopword 'with' must not split the dish"


def test_plural_duplicates_merge():
    out, _, _ = consolidate([
        _p(1, "puro", "Chocolate Fudge Cookie", 290, food_type="Dessert"),
        _p(2, "puro", "Chocolate Fudge Cookies", 290, food_type="Dessert"),
    ])
    assert len(out) == 1


def test_different_dishes_do_not_merge():
    out, _, _ = consolidate([
        _p(1, "r1", "Chicken Biryani", 200),
        _p(2, "r1", "Beef Biryani", 250),
    ])
    assert len(out) == 2, "different proteins are different dishes"


def test_same_dish_different_restaurants_never_merge():
    out, _, _ = consolidate([
        _p(1, "r1", "Chicken Biryani Half", 150),
        _p(2, "r2", "Chicken Biryani Full", 280),
    ])
    assert len(out) == 2, "consolidation is strictly per-restaurant"


def test_plain_size_variants_still_merge():
    """The original behaviour (no spelling drift) must keep working."""
    out, _, _ = consolidate([
        _p(1, "smug-momo", "Steamed Chicken Momo 5 Pcs", 120),
        _p(2, "smug-momo", "Steamed Chicken Momo 8pcs", 180),
    ])
    assert len(out) == 1
    assert {v["label"] for v in out[0]["variations"]} == {"5 Pcs", "8 Pcs"}
