"""food_sub_type_id must actually land on the rows it links.

The sub_id lookup is BUILT inside main() and READ in two far-apart places
(prod_values and the canonical_dishes insert). dict.get() returns None on a key
miss instead of raising, so a key-shape mismatch between the build site and a
read site is completely silent: the load reports "N created / N updated" and
writes NULL every time. That is exactly what happened -- 16,385 products and
1,431 canonical dishes all carried food_sub_type_id NULL while the 111
food_sub_types rows sat there unreferenced.

Unit-testing the helpers cannot catch this: the lookup and both its readers live
inside main(), so the only thing that proves the link survives is running the
real loader end to end and reading the column back out of Postgres.
"""
import json

import pytest
from sqlalchemy import text


def _write_fixture_files(tmp_path):
    """Minimal but realistically-shaped v2 pipeline output: one restaurant, two
    products under different food_types, one of which shares a sub_type NAME
    with the other food_type ('Chicken' under both Rice and Fry) -- the case
    that only a (food_type, sub_type) composite key gets right."""
    products = [
        {
            "product_id": 101,
            "source_restaurant_code": "r1",
            "name": "Chicken Biryani",
            "description": "Aromatic rice",
            "price_bdt": 320,
            "image": "https://img/biryani.jpg",
            "is_sold_out": False,
            "category": "Main",
            "cuisine": "Bangladeshi",
            "food_type": "Rice",
            "sub_type": "Chicken",
            "flavor_tags": ["spicy"],
            "variations": [{"label": "Regular", "price_bdt": 320}],
        },
        {
            "product_id": 102,
            "source_restaurant_code": "r1",
            "name": "Chicken Fry",
            "description": "Crispy",
            "price_bdt": 180,
            "image": None,
            "is_sold_out": False,
            "category": "Main",
            "cuisine": "Bangladeshi",
            "food_type": "Fry",
            "sub_type": "Chicken",
            "flavor_tags": [],
            "variations": [],
        },
        {
            "product_id": 103,
            "source_restaurant_code": "r1",
            "name": "Plain Water",
            "price_bdt": 20,
            "food_type": "Drink",
            "sub_type": None,
            "variations": [],
        },
    ]
    canonical = [
        {
            "name": "Chicken Biryani",
            "aliases": ["Chicken Biriyani"],
            "food_type": "Rice",
            "sub_type": "Chicken",
            "cuisine": "Bangladeshi",
            "category": "Main",
            "member_source_product_ids": [101],
        },
        {
            "name": "Chicken Fry",
            "aliases": [],
            "food_type": "Fry",
            "sub_type": "Chicken",
            "cuisine": "Bangladeshi",
            "category": "Main",
            "member_source_product_ids": [102],
        },
    ]
    restaurants = [
        {
            "source_restaurant_code": "r1",
            "name": "Kacchi Bhai",
            "address": "Road 2, Dhanmondi",
            "coordinates": {"latitude": 23.74, "longitude": 90.37},
            "rating": 4.5,
            "review_number": 120,
            "budget": 2,
            "cuisines": ["Bangladeshi"],
            "images": {"hero": "https://img/hero.jpg", "logo": None},
        }
    ]
    chains = [{"slug": "kacchi-bhai", "name": "Kacchi Bhai", "member_codes": ["r1"]}]

    paths = {
        "products": tmp_path / "consolidated.json",
        "canonical": tmp_path / "canonical_dishes.json",
        "restaurants": tmp_path / "restaurants_dhanmondi_restaurants.json",
        "chains": tmp_path / "chains.json",
    }
    for key, payload in [("products", products), ("canonical", canonical),
                         ("restaurants", restaurants), ("chains", chains)]:
        paths[key].write_text(json.dumps(payload), encoding="utf-8")
    return paths


def _run_loader(tmp_path, monkeypatch):
    from load_batch import main
    paths = _write_fixture_files(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "load_batch.py",
        str(paths["products"]),
        str(paths["canonical"]),
        str(tmp_path / "restaurants_*_restaurants.json"),
        "--chains", str(paths["chains"]),
    ])
    main()


@pytest.fixture
def loaded(tmp_path, monkeypatch, temp_db, db_session):
    _run_loader(tmp_path, monkeypatch)
    return db_session


def test_product_with_a_sub_type_persists_food_sub_type_id(loaded):
    """The bug: sub_id is keyed by (food_type_id, name) but prod_values read it
    with (food_type_NAME, name). Every lookup missed, .get() swallowed it, and
    the column went out NULL."""
    row = loaded.execute(text(
        """
        SELECT fst.name AS sub_type, ft.name AS food_type
        FROM products p
        JOIN food_sub_types fst ON fst.id = p.food_sub_type_id
        JOIN food_types ft ON ft.id = fst.food_type_id
        WHERE p.source_product_id = 101
        """
    )).one_or_none()

    assert row is not None, "product 101 has a sub_type in the source but food_sub_type_id is NULL"
    assert (row.food_type, row.sub_type) == ("Rice", "Chicken")


def test_canonical_dish_with_a_sub_type_persists_food_sub_type_id(loaded):
    """Second read site, same silent miss -- canonical dishes were all NULL too."""
    row = loaded.execute(text(
        """
        SELECT fst.name AS sub_type, ft.name AS food_type
        FROM canonical_dishes cd
        JOIN food_sub_types fst ON fst.id = cd.food_sub_type_id
        JOIN food_types ft ON ft.id = fst.food_type_id
        WHERE cd.name = 'Chicken Biryani'
        """
    )).one_or_none()

    assert row is not None, "canonical dish 'Chicken Biryani' has a sub_type but food_sub_type_id is NULL"
    assert (row.food_type, row.sub_type) == ("Rice", "Chicken")


def test_sub_type_is_scoped_to_its_food_type(loaded):
    """'Chicken' exists under both Rice and Fry. A lookup keyed on the sub_type
    name alone would collapse them; the link must follow the product's own
    food_type."""
    rows = dict(loaded.execute(text(
        """
        SELECT p.source_product_id, ft.name
        FROM products p
        JOIN food_sub_types fst ON fst.id = p.food_sub_type_id
        JOIN food_types ft ON ft.id = fst.food_type_id
        WHERE p.source_product_id IN (101, 102)
        """
    )).all())

    assert rows == {101: "Rice", 102: "Fry"}


def test_product_without_a_sub_type_stays_null(loaded):
    """Guard the other direction: don't invent a link where the source has none."""
    assert loaded.execute(text(
        "SELECT food_sub_type_id FROM products WHERE source_product_id = 103"
    )).scalar() is None


def test_reload_backfills_food_sub_type_id_on_existing_rows(tmp_path, monkeypatch, loaded):
    """The trap from HANDOFF.md §9: load_batch skips writing a row whose
    signature is unchanged, so a column missing from the signature can never be
    backfilled. food_sub_type_id IS in both signature builders and the unnest
    SQL, so a second pass over rows that were NULLed by the old code must
    actually repair them. Simulate that by clearing the column and reloading.
    """
    loaded.execute(text("UPDATE products SET food_sub_type_id = NULL"))
    loaded.commit()

    _run_loader(tmp_path, monkeypatch)

    assert loaded.execute(text(
        "SELECT food_sub_type_id FROM products WHERE source_product_id = 101"
    )).scalar() is not None, "reload reported success but did not backfill food_sub_type_id"
