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
