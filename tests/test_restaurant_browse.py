"""Brand-level restaurant browse (spec D14): GET /restaurants lists brands,
not branches -- 'Bella Italia' once with branch_count=3, never
'Bella Italia - Dhanmondi' as its own row. Plus the merged brand menu and
location reviews (submit to a branch, pooled per brand)."""

import itertools

_pid = itertools.count(80000)


def _brand(db, slug, name, branch_specs):
    """branch_specs: list of (code, branch_name, area). Returns (chain, branches)."""
    import models
    chain = models.RestaurantChain(chain_code=slug, name=name)
    db.add(chain)
    db.flush()
    branches = []
    for code, bname, area in branch_specs:
        r = models.Restaurant(source_restaurant_code=code, name=bname, area=area,
                              chain_id=chain.id, old_rating=4.0, old_review_count=50)
        db.add(r)
        db.flush()
        branches.append(r)
    db.commit()
    return chain, branches


def _dish(db, branch, name, price, food_type_id=None):
    import models
    p = models.Product(source_product_id=next(_pid), restaurant_id=branch.id,
                       name=name, base_price_bdt=price,
                       normalized_name=name.lower(), food_type_id=food_type_id)
    db.add(p)
    db.commit()
    return p


def _register(client, email="rev@x.com", username="reviewer"):
    client.post("/auth/register", json={"email": email, "username": username, "password": "secret1"})
    tok = client.post("/auth/login", data={"username": email, "password": "secret1"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------

def test_browse_lists_brands_not_branches(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    _brand(db_session, "bella-italia", "Bella Italia",
           [("b1", "Bella Italia - Dhanmondi", "Dhanmondi"),
            ("b2", "Bella Italia - Gulshan", "Gulshan"),
            ("b3", "Bella Italia - Uttara", "Uttara")])
    _brand(db_session, "niribily", "Niribily", [("n1", "Niribily", "Dhanmondi")])

    body = TestClient(app).get("/restaurants").json()
    assert len(body) == 2, "3 branches + 1 solo must be exactly 2 brands"
    bella = next(b for b in body if b["name"] == "Bella Italia")
    assert bella["branch_count"] == 3
    assert bella["areas"] == ["Dhanmondi", "Gulshan", "Uttara"]
    solo = next(b for b in body if b["name"] == "Niribily")
    assert solo["branch_count"] == 1


def test_catalogue_filters_by_brand_name_and_area(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    _brand(db_session, "bella-italia", "Bella Italia", [("b1", "Bella Italia - Gulshan", "Gulshan")])
    _brand(db_session, "niribily", "Niribily", [("n1", "Niribily", "Dhanmondi")])
    c = TestClient(app)

    by_name = c.get("/restaurants/catalogue", params={"q": "bella"}).json()
    assert by_name["total"] == 1 and by_name["restaurants"][0]["name"] == "Bella Italia"

    by_area = c.get("/restaurants/catalogue", params={"q": "dhanmondi"}).json()
    assert by_area["total"] == 1 and by_area["restaurants"][0]["name"] == "Niribily"


def test_catalogue_paginates_brands(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    for i in range(3):
        _brand(db_session, f"brand-{i}", f"Brand {i}", [(f"r{i}", f"Brand {i}", "Dhanmondi")])
    page = TestClient(app).get("/restaurants/catalogue", params={"offset": 0, "limit": 2}).json()
    assert page["total"] == 3 and len(page["restaurants"]) == 2


# ---------------------------------------------------------------------------
# Merged brand menu
# ---------------------------------------------------------------------------

def test_brand_menu_merges_branches_without_duplicates(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, branches = _brand(db_session, "bella-italia", "Bella Italia",
                             [("b1", "Bella 1", "Dhanmondi"), ("b2", "Bella 2", "Gulshan"),
                              ("b3", "Bella 3", "Uttara")])
    for b in branches:
        _dish(db_session, b, "Margherita", 250)          # at all 3 branches
    _dish(db_session, branches[0], "Tiramisu", 180)      # only at one

    menu = TestClient(app).get(f"/restaurants/{chain.id}/menu").json()
    assert len(menu) == 2, "3x Margherita must collapse to one card"
    margherita = next(m for m in menu if m["name"] == "Margherita")
    assert margherita["branch_count"] == 3 and margherita["brand_branch_total"] == 3
    tiramisu = next(m for m in menu if m["name"] == "Tiramisu")
    assert tiramisu["branch_count"] == 1 and tiramisu["brand_branch_total"] == 3


# ---------------------------------------------------------------------------
# Location reviews: submit to a branch, pooled per brand
# ---------------------------------------------------------------------------

def test_review_requires_branch_of_this_brand(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, branches = _brand(db_session, "bella-italia", "Bella Italia",
                             [("b1", "Bella 1", "Dhanmondi")])
    _other, other_branches = _brand(db_session, "niribily", "Niribily", [("n1", "Niribily", "Gulshan")])
    c = TestClient(app)
    H = _register(c)

    ok = c.post(f"/restaurants/{chain.id}/reviews",
                json={"branch_id": branches[0].id, "rating": 5, "comment": "great"}, headers=H)
    assert ok.status_code == 201
    assert ok.json()["branch_name"] == "Bella 1"

    wrong = c.post(f"/restaurants/{chain.id}/reviews",
                   json={"branch_id": other_branches[0].id, "rating": 1}, headers=H)
    assert wrong.status_code == 400, "a branch of another brand must be rejected"


def test_brand_reviews_pool_across_branches_with_branch_tags(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, branches = _brand(db_session, "bella-italia", "Bella Italia",
                             [("b1", "Bella Dhanmondi", "Dhanmondi"),
                              ("b2", "Bella Gulshan", "Gulshan")])
    c = TestClient(app)
    h1 = _register(c, "a@x.com", "usera")
    h2 = _register(c, "b@x.com", "userb")
    c.post(f"/restaurants/{chain.id}/reviews",
           json={"branch_id": branches[0].id, "rating": 5}, headers=h1)
    c.post(f"/restaurants/{chain.id}/reviews",
           json={"branch_id": branches[1].id, "rating": 3}, headers=h2)

    body = c.get(f"/restaurants/{chain.id}/reviews").json()
    assert body["total"] == 2
    assert {r["branch_name"] for r in body["reviews"]} == {"Bella Dhanmondi", "Bella Gulshan"}

    page = c.get(f"/restaurants/{chain.id}").json()
    assert page["display_rating"] == 4.0
    assert page["display_rating_source"] == "khawon"


def test_one_review_per_user_per_location_upserts(temp_db, db_session):
    from fastapi.testclient import TestClient
    from main import app
    chain, branches = _brand(db_session, "bella-italia", "Bella Italia",
                             [("b1", "Bella 1", "Dhanmondi")])
    c = TestClient(app)
    H = _register(c)
    c.post(f"/restaurants/{chain.id}/reviews", json={"branch_id": branches[0].id, "rating": 5}, headers=H)
    c.post(f"/restaurants/{chain.id}/reviews", json={"branch_id": branches[0].id, "rating": 2}, headers=H)
    body = c.get(f"/restaurants/{chain.id}/reviews").json()
    assert body["total"] == 1
    assert body["reviews"][0]["rating"] == 2
