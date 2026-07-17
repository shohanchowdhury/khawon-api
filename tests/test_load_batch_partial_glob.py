"""A partial restaurants_glob must not touch products outside the batch.

main() deletes product_variations / product_flavor_tags scoped to the CURRENT
batch (restaurant_id = ANY(batch_rest_ids)), but the rebuild loop right after it
resolved pid through prod_id_by_spid -- which is seeded from EVERY product row
in the database, not just the batch. So a product whose restaurant was not in
the glob got its variations re-inserted having never been deleted:

    UniqueViolation: duplicate key value violates unique constraint
    "product_variations_product_id_label_key"

It stayed hidden because every real load passed a glob covering all 451
restaurants. It surfaced on 2026-07-17 when consolidated.json was reloaded with
only the Dhanmondi restaurants file.

The rebuild loop must skip out-of-batch products, NOT widen the DELETE -- a
wider DELETE would wipe variations for restaurants the batch never loaded.
"""
import json

import pytest
from sqlalchemy import text


def _write_fixture_files(tmp_path):
    """Two restaurants in two area files, one consolidated.json covering both.

    Each product carries variations and flavor tags, since both tables are
    rebuilt by the same loop and both have a per-product unique constraint.
    """
    products = [
        {
            "product_id": 101,
            "source_restaurant_code": "r1",
            "name": "Chicken Biryani",
            "price_bdt": 320,
            "category": "Main",
            "cuisine": "Bangladeshi",
            "food_type": "Rice",
            "sub_type": "Chicken",
            "flavor_tags": ["spicy"],
            "variations": [
                {"label": "Regular", "price_bdt": 320},
                {"label": "Large", "price_bdt": 450},
            ],
        },
        {
            "product_id": 201,
            "source_restaurant_code": "r2",
            "name": "Beef Tehari",
            "price_bdt": 280,
            "category": "Main",
            "cuisine": "Bangladeshi",
            "food_type": "Rice",
            "sub_type": "Beef",
            "flavor_tags": ["spicy"],
            "variations": [
                {"label": "Regular", "price_bdt": 280},
                {"label": "Large", "price_bdt": 400},
            ],
        },
    ]
    canonical = [
        {
            "name": "Chicken Biryani",
            "aliases": [],
            "food_type": "Rice",
            "sub_type": "Chicken",
            "cuisine": "Bangladeshi",
            "category": "Main",
            "member_source_product_ids": [101],
        },
    ]
    dhanmondi = [
        {
            "source_restaurant_code": "r1",
            "name": "Kacchi Bhai",
            "address": "Road 2, Dhanmondi",
            "coordinates": {"latitude": 23.74, "longitude": 90.37},
            "cuisines": ["Bangladeshi"],
        }
    ]
    gulshan = [
        {
            "source_restaurant_code": "r2",
            "name": "Tehari Ghor",
            "address": "Road 11, Gulshan",
            "coordinates": {"latitude": 23.79, "longitude": 90.41},
            "cuisines": ["Bangladeshi"],
        }
    ]
    chains = [
        {"slug": "kacchi-bhai", "name": "Kacchi Bhai", "member_codes": ["r1"]},
        {"slug": "tehari-ghor", "name": "Tehari Ghor", "member_codes": ["r2"]},
    ]

    paths = {
        "products": tmp_path / "consolidated.json",
        "canonical": tmp_path / "canonical_dishes.json",
        "dhanmondi": tmp_path / "restaurants_dhanmondi_restaurants.json",
        "gulshan": tmp_path / "restaurants_gulshan_restaurants.json",
        "chains": tmp_path / "chains.json",
    }
    for key, payload in [("products", products), ("canonical", canonical),
                         ("dhanmondi", dhanmondi), ("gulshan", gulshan),
                         ("chains", chains)]:
        paths[key].write_text(json.dumps(payload), encoding="utf-8")
    return paths


def _run_loader(tmp_path, monkeypatch, restaurants_glob):
    from load_batch import main
    paths = _write_fixture_files(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "load_batch.py",
        str(paths["products"]),
        str(paths["canonical"]),
        restaurants_glob,
        "--chains", str(paths["chains"]),
    ])
    main()


@pytest.fixture
def loaded_both(tmp_path, monkeypatch, temp_db, db_session):
    """Full load: glob covers both areas, so both restaurants are in the batch."""
    _run_loader(tmp_path, monkeypatch, str(tmp_path / "restaurants_*_restaurants.json"))
    return db_session


def _variation_labels(session, source_product_id):
    return sorted(session.execute(text(
        """
        SELECT v.label
        FROM product_variations v
        JOIN products p ON p.id = v.product_id
        WHERE p.source_product_id = :spid
        """
    ), {"spid": source_product_id}).scalars().all())


def test_reload_with_partial_glob_does_not_duplicate_out_of_batch_variations(
    tmp_path, monkeypatch, loaded_both
):
    """The bug: reloading the same consolidated.json with a Dhanmondi-only glob
    re-inserted Gulshan's variations, which the batch-scoped DELETE never
    removed -> UniqueViolation on (product_id, label)."""
    _run_loader(tmp_path, monkeypatch, str(tmp_path / "restaurants_dhanmondi_*.json"))

    assert _variation_labels(loaded_both, 101) == ["Large", "Regular"]


def test_reload_with_partial_glob_leaves_out_of_batch_variations_intact(
    tmp_path, monkeypatch, loaded_both
):
    """Skipping out-of-batch products must not become deleting them: Gulshan's
    variations were loaded by the first pass and the second pass has no mandate
    over them."""
    _run_loader(tmp_path, monkeypatch, str(tmp_path / "restaurants_dhanmondi_*.json"))

    assert _variation_labels(loaded_both, 201) == ["Large", "Regular"]


def test_reload_with_partial_glob_leaves_out_of_batch_flavor_tags_intact(
    tmp_path, monkeypatch, loaded_both
):
    """Same loop rebuilds product_flavor_tags, same (product_id, flavor_tag_id)
    uniqueness, same exposure."""
    _run_loader(tmp_path, monkeypatch, str(tmp_path / "restaurants_dhanmondi_*.json"))

    count = loaded_both.execute(text(
        """
        SELECT count(*)
        FROM product_flavor_tags ft
        JOIN products p ON p.id = ft.product_id
        WHERE p.source_product_id = 201
        """
    )).scalar()
    assert count == 1
