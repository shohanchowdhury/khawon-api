"""
bootstrap_canonical_dishes.py

Third pipeline stage (after classify_batch.py -> load): groups individual
restaurant menu items into CANONICAL DISHES - the cross-restaurant comparison
identity that powers Khawon's core "search a dish, compare it across
restaurants" feature.

Grouping passes:
  1. Conservative size/prefix normalization (unchanged).
  2. Spelling + plural + token-order normalization for match keys.
  3. Fuzzy cluster merge within the same food_type for near-identical keys.

Set Menu / combo items are EXCLUDED entirely.

Usage:
    python bootstrap_canonical_dishes.py [output.json]
    python bootstrap_canonical_dishes.py [output.json] --input v2_output/consolidated.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from difflib import SequenceMatcher

INPUT_PATH = "v2_output/consolidated.json"
# Comparison is across BRANDS, not branches: the same dish at three branches of
# one chain is not a cross-restaurant comparison, it is the same dish. Branch
# dedupe is the chain layer's job (see bootstrap_chains.py).
MIN_BRANDS = 2
EXCLUDED_FOOD_TYPES = {"Set Menu"}
FUZZY_MERGE_THRESHOLD = 0.92

SIZE_PATTERNS = [
    r"^\d+\s*:\s*\d+\s*-?\s*",
    r"\b\d+\s*(?:pcs?|pieces?)\b",
    r"\b\d+\s*(?:gm|gram|grams|g|kg|ml|ltr|l)\b",
    r"-?\s*\b(?:half|full|3\s*quarter|quarter|small|medium|large|reg|regular|xl)\b",
]

# Phrase-level normalizations (longest first).
PHRASE_NORMALIZATIONS = [
    (r"milk\s*shakes?", "milkshake"),
    (r"cheese\s*cakes?", "cheesecake"),
    (r"cashew\s*nuts?", "cashew"),
    (r"kala\s*bhuna", "kalabhuna"),
    (r"double\s*decker", "doubledecker"),
]

# Whole-word spelling variants seen across Dhaka menus.
SPELLING_MAP = {
    "biriyani": "biryani",
    "biriani": "biryani",
    "chilli": "chili",
    "chily": "chili",
    "singara": "shingara",
    "shingra": "shingara",
    "duble": "double",
    "chap": "chaap",
    "cookies": "cookie",
    "nuggets": "nugget",
    "drumsticks": "drumstick",
    "vegetables": "vegetable",
    "shakes": "shake",
    "smoothies": "smoothie",
    "noodles": "noodle",
    "momos": "momo",
    "parathas": "paratha",
    "naans": "naan",
    "rotis": "roti",
    "luchis": "luchi",
    "khichuris": "khichuri",
    "teharies": "tehari",
    "kachis": "kacchi",
    "kachchi": "kacchi",
    "polaos": "polao",
    "pilafs": "polao",
    "pilau": "polao",
    "pulao": "polao",
    "wings": "wing",
    "burgers": "burger",
    "pizzas": "pizza",
    "sandwiches": "sandwich",
    "wraps": "wrap",
    "rolls": "roll",
    "soups": "soup",
    "salads": "salad",
    "kebabs": "kebab",
    "kababs": "kebab",
    "kabab": "kebab",
    "lovers": "lover",
}

# If these modifier tokens differ between two names, do not fuzzy-merge.
DISTINCT_MODIFIERS = {
    "shahi", "malai", "hyderabadi", "kashmiri", "bombay", "handi", "dum",
    "special", "premium", "deluxe", "royal", "classic", "naga", "tikka",
    "makhani", "garlic", "schezwan", "szechuan", "bbq", "smoky", "crispy",
    "grilled", "fried", "steamed", "roasted", "smoked", "spicy", "hot",
    "veg", "vegetable", "plain", "masala", "butter", "cheese", "egg",
}

PROTEIN_TOKENS = {
    "chicken", "beef", "mutton", "fish", "prawn", "shrimp", "paneer",
    "egg", "duck", "lamb", "pork", "squid", "crab", "lobster", "turkey",
    "vegetable", "veg", "mushroom", "tofu",
}


def normalize_name(name: str) -> str:
    """Strip sizes/prefixes/punctuation. Keeps distinguishing modifier words."""
    n = (name or "").lower()
    for pat in SIZE_PATTERNS:
        n = re.sub(pat, " ", n)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def apply_spelling_map(text: str) -> str:
    n = text
    for pattern, replacement in PHRASE_NORMALIZATIONS:
        n = re.sub(pattern, replacement, n)
    tokens = n.split()
    return " ".join(SPELLING_MAP.get(tok, tok) for tok in tokens)


def canonical_match_key(name: str) -> str:
    """Match key: conservative normalize -> spelling -> sorted tokens."""
    n = normalize_name(name)
    if not n:
        return n
    n = apply_spelling_map(n)
    tokens = [t for t in n.split() if t not in {"with", "and", "the", "a", "an", "of"}]
    if not tokens:
        return n
    return " ".join(sorted(tokens))


def _token_set(key: str) -> set[str]:
    return set(key.split())


def _protein_signature(key: str) -> frozenset[str]:
    tokens = _token_set(key)
    found = tokens & PROTEIN_TOKENS
    if "veg" in found:
        found = (found - {"vegetable"}) | {"veg"}
    return frozenset(found)


def _modifiers_compatible(a: str, b: str) -> bool:
    ma = _token_set(a) & DISTINCT_MODIFIERS
    mb = _token_set(b) & DISTINCT_MODIFIERS
    return ma == mb


def _proteins_compatible(a: str, b: str) -> bool:
    return _protein_signature(a) == _protein_signature(b)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _can_merge_keys(a: str, b: str) -> bool:
    if a == b:
        return True
    if not _modifiers_compatible(a, b):
        return False
    if not _proteins_compatible(a, b):
        return False
    return _similarity(a, b) >= FUZZY_MERGE_THRESHOLD


def _merge_promoted_groups(promoted: list[tuple[tuple[str, str], list[dict]]]) -> list[tuple[tuple[str, str], list[dict]]]:
    """Union-find fuzzy merge within each food_type bucket."""
    by_food_type: dict[str, list[tuple[tuple[str, str], list[dict]]]] = collections.defaultdict(list)
    for entry in promoted:
        by_food_type[entry[0][0]].append(entry)

    merged: list[tuple[tuple[str, str], list[dict]]] = []
    for food_type, entries in by_food_type.items():
        parent = list(range(len(entries)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                if _can_merge_keys(entries[i][0][1], entries[j][0][1]):
                    union(i, j)

        clusters: dict[int, list[int]] = collections.defaultdict(list)
        for i in range(len(entries)):
            clusters[find(i)].append(i)

        for indices in clusters.values():
            rep_key = min(entries[i][0][1] for i in indices)
            combined_items: list[dict] = []
            for i in indices:
                combined_items.extend(entries[i][1])
            merged.append(((food_type, rep_key), combined_items))

    merged.sort(key=lambda kv: (kv[0][0], kv[0][1]))
    return merged


def build_canonical_dishes(
    products: list[dict], code_to_brand: dict[str, str]
) -> tuple[list[dict], int]:
    """code_to_brand maps source_restaurant_code -> brand slug (chains.json)."""
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for p in products:
        if p.get("food_type") in EXCLUDED_FOOD_TYPES or p.get("food_type") is None:
            continue
        match_key = canonical_match_key(p.get("name", ""))
        if not match_key:
            continue
        groups[(p["food_type"], match_key)].append(p)

    def brand_of(item: dict) -> str:
        code = item.get("source_restaurant_code")
        # unmapped code -> treat the restaurant as its own brand
        return code_to_brand.get(code, f"__unmapped__{code}")

    promoted: list[tuple[tuple[str, str], list[dict]]] = []
    for key, items in groups.items():
        if len({brand_of(x) for x in items}) >= MIN_BRANDS:
            promoted.append((key, items))

    before_merge = len(promoted)
    promoted = _merge_promoted_groups(promoted)
    merge_count = before_merge - len(promoted)

    def majority(values):
        counts = collections.Counter(values)
        return max(counts.items(), key=lambda kv: (kv[1], kv[0] is not None, str(kv[0])))[0]

    canonical_dishes = []
    for canonical_id, ((food_type, _match_key), items) in enumerate(promoted, start=1):
        raw_names = collections.Counter(x["name"].strip() for x in items)
        display_name = raw_names.most_common(1)[0][0]
        aliases = sorted(n for n in raw_names if n != display_name)
        prices = [x["price_bdt"] for x in items if x.get("price_bdt") is not None]
        brands = sorted({brand_of(x) for x in items})
        canonical_dishes.append({
            "canonical_id": canonical_id,
            "name": display_name,
            "aliases": aliases,
            "food_type": food_type,
            "sub_type": majority([x.get("sub_type") for x in items]),
            "cuisine": majority([x.get("cuisine") for x in items]),
            "category": majority([x.get("category") for x in items]),
            "restaurant_count": len(brands),  # brands, not branches
            "product_count": len(items),
            "price_min_bdt": min(prices) if prices else None,
            "price_max_bdt": max(prices) if prices else None,
            "member_source_product_ids": sorted(x["product_id"] for x in items),
        })
    return canonical_dishes, merge_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_path", nargs="?", default="v2_output/canonical_dishes.json")
    parser.add_argument("--input", default=INPUT_PATH)
    parser.add_argument("--chains", default="v2_output/chains.json",
                        help="bootstrap_chains.py output; promotion needs brands, not branches")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as fh:
        products = json.load(fh)
    with open(args.chains, encoding="utf-8") as fh:
        brands_json = json.load(fh)
    code_to_brand = {code: b["slug"] for b in brands_json for code in b["member_codes"]}

    eligible = [
        p for p in products
        if p.get("food_type") not in EXCLUDED_FOOD_TYPES and p.get("food_type") is not None
    ]

    canonical_dishes, merge_count = build_canonical_dishes(products, code_to_brand)
    linked = sum(c["product_count"] for c in canonical_dishes)

    with open(args.output_path, "w", encoding="utf-8") as fh:
        json.dump(canonical_dishes, fh, indent=2, ensure_ascii=False)

    total = len(products)
    setmenu = sum(1 for p in products if p.get("food_type") in EXCLUDED_FOOD_TYPES)
    print(f"Total products:            {total}")
    print(f"Set Menu (excluded):       {setmenu}")
    print(f"Eligible for canonical:    {len(eligible)}")
    print(f"Fuzzy groups merged:       {merge_count}")
    print(f"Canonical dishes created:  {len(canonical_dishes)}  (2+ distinct BRANDS)")
    print(f"Products linked:           {linked} ({linked / len(eligible) * 100:.1f}% of eligible)")
    print(f"Products left unlinked:    {len(eligible) - linked} (singletons - still browsable)")
    print(f"\nWritten to: {args.output_path}")

    # Spot-check known spelling splits
    for needle in ("Biryani", "Biriyani", "Chili", "Chilli"):
        hits = [c for c in canonical_dishes if needle.lower() in c["name"].lower()]
        if hits:
            top = max(hits, key=lambda c: c["restaurant_count"])
            print(f'  "{needle}" best match: "{top["name"]}" @ {top["restaurant_count"]} restaurants')

    top = sorted(canonical_dishes, key=lambda c: -c["restaurant_count"])[:15]
    print(f"\n--- Top 15 canonical dishes by restaurant spread ---")
    for c in top:
        alias_note = f' | +{len(c["aliases"])} aliases' if c["aliases"] else ""
        print(
            f'  {c["restaurant_count"]:3} rest | {c["food_type"]}/{c["sub_type"]} | '
            f'"{c["name"]}" | {c["price_min_bdt"]:.0f}-{c["price_max_bdt"]:.0f}tk{alias_note}'
        )


if __name__ == "__main__":
    main()
