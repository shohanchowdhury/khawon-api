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


def test_signature_includes_normalized_name():
    """load_batch skips writing a row when its signature is unchanged. If the
    signature ignores a column, that column can NEVER be backfilled on existing
    rows -- the row looks 'unchanged' forever and the write is skipped. This bit
    us for real: a full reload left all 16,402 normalized_name values NULL.

    Every column in prod_values must be in the signature.
    """
    from load_batch import _prod_signature
    base = dict(restaurant_id=1, name="Margherita", description=None,
                base_price_bdt=199, image_url=None, is_sold_out=False,
                category_id=None, cuisine_id=None, food_type_id=None,
                food_sub_type_id=None, normalized_name="margherita")
    changed = {**base, "normalized_name": "something-else"}
    assert _prod_signature(base) != _prod_signature(changed), \
        "signature ignores normalized_name -> the column would never persist"


def test_bulk_update_actually_persists_normalized_name(temp_db, db_session):
    """The real trap: _bulk_update_products_unnest is raw SQL with a hardcoded
    SET clause and hand-maintained parallel arrays. It happily reports "16402
    updated" while never writing a column that is missing from that SQL --
    which is exactly what happened. Round-trip through the DB is the only check
    that catches a stale column list wherever it lives.
    """
    from datetime import datetime, timezone

    import models
    from load_batch import _bulk_update_products_unnest

    r = models.Restaurant(source_restaurant_code="r1", name="R1")
    db_session.add(r)
    db_session.flush()
    p = models.Product(source_product_id=1, restaurant_id=r.id, name="Margherita",
                       base_price_bdt=199, normalized_name=None)
    db_session.add(p)
    db_session.commit()
    pid = p.id

    _bulk_update_products_unnest(db_session, [{
        "_id": pid, "restaurant_id": r.id, "name": "Margherita", "description": None,
        "base_price_bdt": 199, "image_url": None, "is_sold_out": False,
        "category_id": None, "cuisine_id": None, "food_type_id": None,
        "food_sub_type_id": None, "normalized_name": "margherita",
        "last_seen_at": datetime.now(timezone.utc),
    }], label="test")
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(models.Product, pid).normalized_name == "margherita", \
        "bulk update reported success but did not write normalized_name"


def test_signature_row_and_dict_forms_agree():
    """The two signature builders (DB row vs incoming dict) must stay in lockstep;
    if one gains a field and the other doesn't, every row compares 'changed'
    forever and each load rewrites the whole table."""
    from load_batch import _prod_signature, _prod_signature_from_row

    class Row:
        restaurant_id = 1
        name = "Margherita"
        description = None
        base_price_bdt = 199
        image_url = None
        is_sold_out = False
        category_id = None
        cuisine_id = None
        food_type_id = None
        food_sub_type_id = None
        normalized_name = "margherita"

    vals = dict(restaurant_id=1, name="Margherita", description=None,
                base_price_bdt=199, image_url=None, is_sold_out=False,
                category_id=None, cuisine_id=None, food_type_id=None,
                food_sub_type_id=None, normalized_name="margherita")
    assert _prod_signature_from_row(Row()) == _prod_signature(vals)
