import itertools

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
    """Pool reviews across branches so a thin review pool is not split 3 ways."""
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
