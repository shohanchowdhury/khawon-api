def test_compare_lists_one_row_per_brand(temp_db, db_session):
    """Two Domino's branches + one Bella Italia serving the same canonical dish
    = 2 rows, not 3. Comparing a dish to itself across branches is not a
    comparison. restaurant_count (brands) and the row count must agree."""
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
    assert result.min_price_bdt == 199
    assert result.max_price_bdt == 250
