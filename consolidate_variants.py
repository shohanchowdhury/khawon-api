"""
consolidate_variants.py

Pipeline stage between classify_batch.py and bootstrap_canonical_dishes.py.

PROBLEM it fixes: the source (foodpanda) is inconsistent about how it
represents portion/size options. For most dishes the sizes live in one
product's variations[] array - but for ~420 dishes a restaurant lists each
size as a SEPARATE product row (e.g. Smug Momo has 5 distinct products
"Steamed Chicken Momo 5 Pcs / 6pcs / 7 Pcs / 8pcs / 10pc"). Left as-is this
breaks grouping consistency two ways:
  1. Canonical grouping strips the piece-count, so those 5 rows collapse into
     one canonical whose "price range" is really just ONE restaurant's
     portion ladder, not cross-restaurant variation.
  2. The separate rows can get DIFFERENT classifications (the same Domino's
     "Farmhouse" appears once as Pizza, once as Curry).

WHAT IT DOES: groups products by (restaurant, normalized_name) - the same
normalization the canonical bootstrap uses - and where a restaurant has 2+
rows for one dish, MERGES them into a single product whose variations[]
carries each size as a labelled price point. Since normalization only strips
size tokens / punctuation / whitespace, rows that collapse together differ
ONLY by size (or are exact dupes) - so the merge never combines genuinely
different dishes. Classification fields (food_type, sub_type, cuisine,
category) are resolved by majority vote across the merged rows, which also
cleans up the intra-restaurant classifier disagreement (Farmhouse).

Portion fairness across restaurants is deliberately NOT solved here (owner's
call): we keep each size's own price + label and show the range, rather than
computing per-piece/per-gram prices.

Single-row dishes pass through unchanged.

Usage:  python consolidate_variants.py [output.json]
        (reads the classify outputs v2_output/restaurants_*_products.json;
         default output v2_output/consolidated.json - deliberately NOT named
         *_products.json so it can't be re-consumed as an input on a re-run)
"""

import sys
import re
import glob
import json
import collections

# Grouping key shared with the canonical bootstrap (size strip + spelling map
# + stopword removal + sorted tokens). Using the SAME key both places means a
# restaurant's spelling-drifted size rows ('Beef Chaap Polao Half' vs
# 'Beef Chap Pulao - Full') merge here instead of surviving as two products
# that only fuse later, at display time, in the brand card.
from bootstrap_canonical_dishes import canonical_match_key

# Same size vocabulary the canonical bootstrap normalizes on. Capturing group
# so we can also EXTRACT the matched token to use as a variation label.
# NOTE ordering/optional-s: use "pcs?"/"pieces?" (optional trailing s), NOT
# "pc|pcs" - regex alternation is leftmost-match, so "pc|pcs" would match
# just "pc" in "5 Pcs" and leave a stray "s" (splitting "5 Pcs" from "10pc"
# and producing junk names). "pcs?" matches the whole token either way.
SIZE_TOKEN = re.compile(
    r"(\d+\s*(?:pcs?|pieces?)\b"
    r"|\d+\s*(?:gm|gram|grams|g|kg|ml|ltr|l)\b"
    r"|\b(?:half|full|3\s*quarter|quarter|small|medium|large|reg|regular|xl)\b)",
    re.IGNORECASE,
)
PREFIX = re.compile(r"^\d+\s*:\s*\d+\s*-?\s*")


def normalize_name(name):
    n = (name or "").lower()
    n = PREFIX.sub(" ", n)
    n = SIZE_TOKEN.sub(" ", n)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def extract_size_label(name):
    """The portion descriptor in a name, tidied for use as a variation
    label. Returns None if the name carries no size token."""
    m = SIZE_TOKEN.search(name or "")
    if not m:
        return None
    label = m.group(1).strip()
    label = re.sub(r"\s+", " ", label)
    # normalize "6pcs"/"6 Pcs" -> "6 Pcs", title-case bare words
    m2 = re.match(r"(\d+)\s*(pc|pcs|piece|pieces)$", label, re.IGNORECASE)
    if m2:
        return f"{m2.group(1)} Pcs"
    return label.title() if label.isalpha() else label


def clean_display_name(name):
    """Strip a trailing size token from a name for a clean base display
    name ('Steamed Chicken Momo 5 Pcs' -> 'Steamed Chicken Momo')."""
    n = PREFIX.sub("", name or "")
    n = SIZE_TOKEN.sub(" ", n)
    n = re.sub(r"\s{2,}", " ", n).strip(" -–")
    return n or (name or "").strip()


def majority(values):
    """Plurality value; ties break toward a non-null, then alphabetically."""
    counts = collections.Counter(values)
    return max(counts.items(), key=lambda kv: (kv[1], kv[0] is not None, str(kv[0])))[0]


def build_variations(members):
    """One labelled price point per size the restaurant offers, deduped."""
    seen = {}
    for p in members:
        label = extract_size_label(p["name"])
        # fold in any real variations the row already carried
        row_vars = p.get("variations") or [{"label": None, "price_bdt": p["price_bdt"]}]
        for v in row_vars:
            vlabel = v.get("label") or label   # prefer the row's own label, else the size in its name
            price = v.get("price_bdt")
            if price is None:
                continue
            key = (vlabel, price)
            if key not in seen:
                seen[key] = {"label": vlabel, "price_bdt": price}
    variations = sorted(seen.values(), key=lambda v: v["price_bdt"])
    return variations


def consolidate(products):
    # canonical_match_key, not the local normalize_name: the local one only
    # strips sizes/punctuation, so spelling drift (chap/chaap, polao/pulao,
    # plural, token order) splits what is one dish at one restaurant. Grouping
    # is still strictly per-restaurant, so the fusion risk of the looser key
    # stays confined to a single menu -- where same-key rows really are the
    # same dish (or foodpanda listing it twice).
    groups = collections.defaultdict(list)
    for p in products:
        groups[(p["restaurant"], canonical_match_key(p["name"]))].append(p)

    out = []
    merged_groups = 0
    merged_rows = 0
    for (restaurant, norm), members in groups.items():
        if len(members) == 1:
            out.append(members[0])
            continue

        merged_groups += 1
        merged_rows += len(members)
        members_sorted = sorted(members, key=lambda p: p["price_bdt"])
        rep = members_sorted[0]                     # cheapest/smallest = representative "from" price
        variations = build_variations(members_sorted)

        # display name: cleanest base among the members
        name_counts = collections.Counter(clean_display_name(p["name"]) for p in members)
        display_name = name_counts.most_common(1)[0][0]

        consolidated = dict(rep)                    # start from representative, then override
        consolidated["name"] = display_name
        consolidated["price_bdt"] = variations[0]["price_bdt"] if variations else rep["price_bdt"]
        consolidated["variations"] = variations
        consolidated["food_type"] = majority([p.get("food_type") for p in members])
        consolidated["sub_type"] = majority([p.get("sub_type") for p in members])
        consolidated["category"] = majority([p.get("category") for p in members])
        consolidated["cuisine"] = majority([p.get("cuisine") for p in members])
        # union of flavor tags, longest description, available if ANY size is
        consolidated["flavor_tags"] = sorted({t for p in members for t in (p.get("flavor_tags") or [])})
        consolidated["description"] = max((p.get("description") or "" for p in members), key=len)
        consolidated["is_sold_out"] = all(p.get("is_sold_out") for p in members)
        # record the folded-in source ids so a re-scrape / audit can trace them
        consolidated["merged_source_product_ids"] = sorted(p["product_id"] for p in members)
        out.append(consolidated)

    return out, merged_groups, merged_rows


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "v2_output/consolidated.json"

    products = []
    for path in sorted(glob.glob("v2_output/restaurants_*_products.json")):
        with open(path, encoding="utf-8") as fh:
            products.extend(json.load(fh))

    out, merged_groups, merged_rows = consolidate(products)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    print(f"Input products:                  {len(products)}")
    print(f"Multi-row dishes consolidated:   {merged_groups} groups ({merged_rows} rows -> {merged_groups} products)")
    print(f"Output products:                 {len(out)}  (net -{len(products) - len(out)})")
    print(f"\nWritten to: {output_path}")


if __name__ == "__main__":
    main()
