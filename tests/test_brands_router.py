"""Brand page + brand dish detail, served under /restaurants/{chain_id}.

'Restaurant' in the API means brand: {restaurant_id} in the path is the
chain_id, and a standalone restaurant is a brand of one.
"""


def _seed_brand(db):
    import models
    chain = models.RestaurantChain(chain_code="domino-s-pizza", name="Domino's Pizza")
    db.add(chain)
    db.flush()
    ft = models.FoodType(name="Pizza")
    db.add(ft)
    db.flush()
    prods = []
    for i, (code, price) in enumerate([("gs3j", 199), ("wteu", 199), ("s1b9", 348)]):
        r = models.Restaurant(source_restaurant_code=code, name=f"Dominos {code}",
                              area="Dhanmondi", chain_id=chain.id, old_rating=4.5,
                              old_review_count=100)
        db.add(r)
        db.flush()
        p = models.Product(source_product_id=6000 + i, restaurant_id=r.id,
                           name="Margherita", base_price_bdt=price,
                           normalized_name="margherita", food_type_id=ft.id)
        db.add(p)
        prods.append(p)
    db.commit()
    return chain, ft


def test_brand_page_lists_branches(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, _ = _seed_brand(db_session)
    body = TestClient(app).get(f"/restaurants/{chain.id}").json()
    assert body["name"] == "Domino's Pizza"
    assert body["branch_count"] == 3
    assert len(body["branches"]) == 3
    assert body["display_rating_source"] == "foodpanda"  # no khawon reviews yet


def test_brand_page_404s_for_unknown_brand(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    assert TestClient(app).get("/restaurants/999999").status_code == 404


def test_brand_dish_detail_shows_per_branch_breakdown(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, ft = _seed_brand(db_session)
    body = TestClient(app).get(f"/restaurants/{chain.id}/dishes/{ft.id}/margherita").json()
    assert body["name"] == "Margherita"
    assert body["branch_count"] == 3
    assert body["price_varies"] is True
    assert len(body["branches"]) == 3
    # each branch exposes its own product_id so it can be reviewed
    assert all(b["product_id"] for b in body["branches"])


def test_brand_dish_detail_404s_for_unknown_slug(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, ft = _seed_brand(db_session)
    assert TestClient(app).get(f"/restaurants/{chain.id}/dishes/{ft.id}/nope").status_code == 404
