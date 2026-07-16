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
