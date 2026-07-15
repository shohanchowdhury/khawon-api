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
