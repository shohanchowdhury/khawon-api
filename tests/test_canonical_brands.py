from bootstrap_canonical_dishes import build_canonical_dishes


def _p(pid, name, code, food_type="Pizza", price=100.0):
    return {"product_id": pid, "name": name, "source_restaurant_code": code,
            "restaurant": code, "food_type": food_type, "sub_type": None,
            "cuisine": "Italian", "category": "Main Dish", "price_bdt": price}


def test_same_dish_across_branches_of_one_brand_is_not_canonical():
    """Three Domino's branches selling Margherita is ONE brand -- nothing to
    compare, so it must not be promoted."""
    products = [_p(1, "Margherita", "gs3j"), _p(2, "Margherita", "wteu"),
                _p(3, "Margherita", "s1b9")]
    code_to_brand = {"gs3j": "domino-s-pizza", "wteu": "domino-s-pizza", "s1b9": "domino-s-pizza"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert dishes == []


def test_same_dish_across_two_brands_is_canonical():
    products = [_p(1, "Margherita", "gs3j"), _p(2, "Margherita", "s8mp")]
    code_to_brand = {"gs3j": "domino-s-pizza", "s8mp": "bella-italia"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1
    assert dishes[0]["restaurant_count"] == 2


def test_restaurant_count_counts_brands_not_branches():
    """Two Domino's branches + one Bella Italia == 2 brands, not 3."""
    products = [_p(1, "Margherita", "gs3j"), _p(2, "Margherita", "wteu"),
                _p(3, "Margherita", "s8mp")]
    code_to_brand = {"gs3j": "domino-s-pizza", "wteu": "domino-s-pizza", "s8mp": "bella-italia"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1
    assert dishes[0]["restaurant_count"] == 2
    assert dishes[0]["product_count"] == 3


def test_two_standalone_restaurants_still_qualify():
    products = [_p(1, "Margherita", "aaaa"), _p(2, "Margherita", "bbbb")]
    code_to_brand = {"aaaa": "brand-a", "bbbb": "brand-b"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1


def test_fuzzy_merge_still_works():
    """Regression guard: spelling variants must still merge across brands."""
    products = [_p(1, "Chicken Biryani", "aaaa", food_type="Rice"),
                _p(2, "Chicken Biriyani", "bbbb", food_type="Rice")]
    code_to_brand = {"aaaa": "brand-a", "bbbb": "brand-b"}
    dishes, _ = build_canonical_dishes(products, code_to_brand)
    assert len(dishes) == 1
