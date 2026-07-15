from bootstrap_chains import brand_slug, normalize_brand_name


def test_strips_area_suffix_after_dash():
    assert normalize_brand_name("Waffle Up - Dhanmondi") == "waffle up"
    assert normalize_brand_name("Bella Italia - Uttara") == "bella italia"


def test_strips_area_token_without_dash():
    assert normalize_brand_name("Domino's Pizza Gulshan") == "domino s pizza"
    assert normalize_brand_name("KOI The Uttara") == "koi the"


def test_keeps_ampersand_and_collapses_punctuation():
    assert normalize_brand_name("Greens & Seeds - Chef's Table Dhanmondi") == "greens & seeds"


def test_distinct_brands_do_not_collapse():
    assert normalize_brand_name("Pizza Hut-Dhanmondi") != normalize_brand_name("Pizza Burg - Mohammadpur")


def test_slug_is_url_safe():
    assert brand_slug("domino s pizza") == "domino-s-pizza"
    assert brand_slug("greens & seeds") == "greens-and-seeds"


from bootstrap_chains import build_brands


def _r(code, name, chain_code=None, chain_name=None):
    return {"source_restaurant_code": code, "name": name,
            "chain_code": chain_code, "chain_name": chain_name}


def test_chain_branches_group_into_one_brand():
    brands = build_brands([
        _r("b41r", "Waffle Up - Dhanmondi", "cz5re", "Waffle Up"),
        _r("fjsq", "Waffle Up", "cz5re", "Waffle Up"),
        _r("sajp", "Waffle Up - Gulshan", "ch5ue", "Waffle Up - Kitchen"),
        _r("o4qm", "Waffle Up - Uttara", "cz5re", "Waffle Up"),
    ])
    assert len(brands) == 1
    assert sorted(brands[0]["member_codes"]) == ["b41r", "fjsq", "o4qm", "sajp"]


def test_groups_despite_split_chain_code():
    """Thai Bistro is cy7dd vs cq2yv in the source. Name wins."""
    brands = build_brands([
        _r("rfaa", "Thai Bistro - Banani", "cy7dd"),
        _r("s3lj", "Thai Bistro - Gulshan 2", "cq2yv"),
    ])
    assert len(brands) == 1


def test_groups_despite_missing_chain_code():
    """Rice & More has chain_code None on one branch."""
    brands = build_brands([
        _r("t37j", "Rice & More", None),
        _r("waua", "Rice & More - Uttara", "ci0an"),
    ])
    assert len(brands) == 1


def test_standalone_restaurant_is_a_brand_of_one():
    brands = build_brands([_r("avx4", "Niribily Hotel & Restaurant", None)])
    assert len(brands) == 1
    assert brands[0]["member_codes"] == ["avx4"]


def test_distinct_restaurants_do_not_false_merge():
    brands = build_brands([
        _r("s6so", "Pizza Hut-Dhanmondi", None),
        _r("u4cw", "Pizza Burg - Mohammadpur", None),
    ])
    assert len(brands) == 2


def test_display_name_prefers_chain_name():
    brands = build_brands([
        _r("gs3j", "Domino's Pizza - Dhanmondi", "cu0zf", "Domino's Pizza"),
        _r("wteu", "Domino's Pizza Gulshan", "cu0zf", "Domino's Pizza"),
    ])
    assert brands[0]["name"] == "Domino's Pizza"


def test_display_name_falls_back_to_shortest_raw_name():
    brands = build_brands([
        _r("a", "Habanero", None),
        _r("b", "Habanero - Dhanmondi", None),
    ])
    assert brands[0]["name"] == "Habanero"


def test_override_pins_a_restaurant_to_a_brand(monkeypatch):
    import bootstrap_chains
    monkeypatch.setitem(bootstrap_chains.BRAND_OVERRIDES, "zzz1", "totally-different")
    brands = build_brands([
        _r("zzz1", "Habanero", None),
        _r("zzz2", "Habanero - Dhanmondi", None),
    ])
    assert len(brands) == 2
