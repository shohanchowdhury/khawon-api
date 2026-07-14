"""
bootstrap_canonical_dishes.py

Third pipeline stage (after classify_batch.py -> load): groups individual
restaurant menu items into CANONICAL DISHES - the cross-restaurant comparison
identity that powers Khawon's core "search a dish, compare it across
restaurants" feature.

WHY THIS EXISTS (and why food_type/sub_type can't do this job):
food_type/sub_type is a deliberately COARSE browsing classification - one
sub type ("Rice/Biryani-Kacchi") intentionally holds chicken biryani, mutton
kacchi AND beef tehari together, so it's useless as a comparison axis (you'd
compare a 160tk chicken biryani against a 995tk mutton biryani). A canonical
dish is the opposite: a FINE identity that only groups rows that are actually
the same dish, so "/compare/{canonical_id}" returns a true apples-to-apples
price/rating list.

APPROACH (rule-based first pass, per owner's design decisions):
  1. Normalize each product name - conservative: strip size labels (Half/
     Full/250g/4pcs), '1:1 -' prefixes, and punctuation, but KEEP modifier
     words (Special/Shahi/Premium). Aggressive word-stripping was measured to
     add <1% coverage while risking merging genuinely-different dishes
     ("Special Chicken Kacchi" is often a bigger/premium dish, not the plain
     one), so it's deliberately not done.
  2. Group by (food_type, normalized_name) - NOT sub_type. sub_type is left
     out of the key on purpose: the classifier is sometimes inconsistent
     about a dish's sub type across restaurants ("Beef Tehari" tagged Tehari
     at 25 places, Biryani/Kacchi at 3), and those are the SAME dish - so
     including sub_type in the key wrongly fragments one dish into two
     un-comparable canonical dishes. The dish NAME is its identity; the
     canonical's own sub_type is then the majority label among its members.
     food_type IS kept in the key, because a shared name across different
     food types is often a genuinely DIFFERENT dish ("Beef Kala Bhuna" the
     curry at 8 restaurants vs the kala-bhuna PIZZA at Domino's) - merging
     those would compare a 200tk curry against a 900tk pizza. Keeping
     food_type in the key isolates them correctly. The residual cost is a
     few genuine cross-food-type same-dishes left split (a "Club Sandwich"
     classified Sandwich at some places, Burger at others) - that's a
     harmless under-merge, both halves still browsable.
  3. Promote a group to a canonical dish ONLY if it spans 2+ DIFFERENT
     restaurants - a name at a single restaurant isn't proof of a shared
     dish, so it stays unlinked (still browsable via food_type, just not
     "compared").
  4. Set Menu / combo items are EXCLUDED entirely - a "Family Feast" at two
     restaurants is two different bundles, not the same dish.

WHAT THIS IS NOT: this is exact-after-normalization matching only. It does
NOT unify genuine spelling variants (Biryani vs Biriyani) or word-order
differences (Chicken Kacchi vs Kacchi Chicken). That's deferred future work
(fuzzy / embedding-based matching) - the aliases[] list and the nullable
canonical_dish_id FK are designed so it can be layered in later without a
schema change or re-linking existing dishes.

Output: canonical_dishes.json - each record self-contained for a DB loader to
create the canonical_dishes row and backfill products.canonical_dish_id via
the member_source_product_ids list. Idempotent: same input -> same grouping,
and canonical ids are assigned deterministically (sorted) so re-runs are
stable.

Runs AFTER consolidate_variants.py - it reads that stage's output
(consolidated.json), where same-restaurant size rows are already merged into
one product each, so a canonical's price range reflects real cross-restaurant
variation, not one restaurant's portion ladder.

Usage:  python bootstrap_canonical_dishes.py [output.json]
        (reads v2_output/consolidated.json; defaults output to
         v2_output/canonical_dishes.json)
"""

import sys
import re
import json
import collections

INPUT_PATH = "v2_output/consolidated.json"
MIN_RESTAURANTS = 2          # promotion threshold (owner's call)
EXCLUDED_FOOD_TYPES = {"Set Menu"}   # combos are restaurant-specific bundles

# Size / portion / packaging tokens - stripped, because they describe the
# same dish in a different quantity, not a different dish. (pcs?/pieces? use
# optional-s so "5 Pcs" matches whole - see consolidate_variants.py note.)
SIZE_PATTERNS = [
    r"^\d+\s*:\s*\d+\s*-?\s*",                        # '1:1 -' style prefixes
    r"\b\d+\s*(?:pcs?|pieces?)\b",                    # piece counts
    r"\b\d+\s*(?:gm|gram|grams|g|kg|ml|ltr|l)\b",     # weights / volumes
    r"-?\s*\b(?:half|full|3\s*quarter|quarter|small|medium|large|reg|regular|xl)\b",
]


def normalize_name(name):
    """Conservative normalization - collapses size/portion variants of the
    same dish, keeps everything that might distinguish two real dishes."""
    n = (name or "").lower()
    for pat in SIZE_PATTERNS:
        n = re.sub(pat, " ", n)
    n = re.sub(r"[^\w\s]", " ", n)      # punctuation -> space
    n = re.sub(r"\s+", " ", n).strip()
    return n


def load_products():
    with open(INPUT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def build_canonical_dishes(products):
    groups = collections.defaultdict(list)
    for p in products:
        if p.get("food_type") in EXCLUDED_FOOD_TYPES:
            continue
        if p.get("food_type") is None:
            continue
        norm = normalize_name(p.get("name", ""))
        if not norm:
            continue
        key = (p["food_type"], norm)
        groups[key].append(p)

    # Promote only groups spanning >= MIN_RESTAURANTS distinct restaurants.
    promoted = []
    for key, items in groups.items():
        restaurants = {x.get("restaurant") for x in items}
        if len(restaurants) >= MIN_RESTAURANTS:
            promoted.append((key, items))

    # Deterministic id assignment (sorted by the group key) so re-runs are
    # stable and diffable.
    promoted.sort(key=lambda kv: (kv[0][0], kv[0][1]))

    # Canonical-level authoritative attributes = majority label among members.
    # The classifier may disagree across restaurants for the same dish; the
    # plurality is the single best answer, and using it for grouping/filtering
    # (rather than the noisy per-product values) keeps a dish from flickering
    # in and out of a cuisine/category filter. Ties break toward a non-null,
    # then alphabetically, for determinism.
    def majority(values):
        counts = collections.Counter(values)
        return max(counts.items(), key=lambda kv: (kv[1], kv[0] is not None, str(kv[0])))[0]

    canonical_dishes = []
    for canonical_id, ((food_type, norm), items) in enumerate(promoted, start=1):
        raw_names = collections.Counter(x["name"].strip() for x in items)
        display_name = raw_names.most_common(1)[0][0]
        aliases = sorted(n for n in raw_names if n != display_name)
        prices = [x["price_bdt"] for x in items if x.get("price_bdt") is not None]
        restaurants = sorted({x.get("restaurant") for x in items})
        canonical_dishes.append({
            "canonical_id": canonical_id,
            "name": display_name,
            "aliases": aliases,
            "food_type": food_type,
            "sub_type": majority([x.get("sub_type") for x in items]),
            "cuisine": majority([x.get("cuisine") for x in items]),
            "category": majority([x.get("category") for x in items]),
            "restaurant_count": len(restaurants),
            "product_count": len(items),
            "price_min_bdt": min(prices) if prices else None,
            "price_max_bdt": max(prices) if prices else None,
            "member_source_product_ids": sorted(x["product_id"] for x in items),
        })
    return canonical_dishes


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "v2_output/canonical_dishes.json"

    products = load_products()
    eligible = [p for p in products
                if p.get("food_type") not in EXCLUDED_FOOD_TYPES
                and p.get("food_type") is not None]

    canonical_dishes = build_canonical_dishes(products)
    linked = sum(c["product_count"] for c in canonical_dishes)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(canonical_dishes, fh, indent=2, ensure_ascii=False)

    total = len(products)
    setmenu = sum(1 for p in products if p.get("food_type") in EXCLUDED_FOOD_TYPES)
    print(f"Total products:            {total}")
    print(f"Set Menu (excluded):       {setmenu}")
    print(f"Eligible for canonical:    {len(eligible)}")
    print(f"Canonical dishes created:  {len(canonical_dishes)}")
    print(f"Products linked:           {linked} ({linked/len(eligible)*100:.1f}% of eligible)")
    print(f"Products left unlinked:    {len(eligible) - linked} (singletons - still browsable)")
    print(f"\nWritten to: {output_path}")

    # Sample - biggest canonical dishes by restaurant spread
    top = sorted(canonical_dishes, key=lambda c: -c["restaurant_count"])[:15]
    print(f"\n--- Top 15 canonical dishes by restaurant spread ---")
    for c in top:
        print(f'  {c["restaurant_count"]:3} rest | {c["food_type"]}/{c["sub_type"]} | '
              f'"{c["name"]}" | {c["price_min_bdt"]:.0f}-{c["price_max_bdt"]:.0f}tk'
              f'{" | +" + str(len(c["aliases"])) + " aliases" if c["aliases"] else ""}')


if __name__ == "__main__":
    main()
