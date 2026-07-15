"""bootstrap_chains.py

Brand identity for Khawon. Groups restaurants into BRANDS so a chain's
branches collapse into one identity.

Why not trust the source chain_code? It is wrong for ~21% of real brands: it
SPLITS them (Waffle Up across cz5re/ch5ue, New Hanif Biryani across
ck1ob/cr7gd, Thai Bistro across cy7dd/cq2yv) and MISSES them entirely
(Rice & More has chain_code=None on one branch). So chain_code is a signal,
never the grouping key.

Grouping is by normalized name, with BRAND_OVERRIDES pinning exceptions.
Every restaurant gets a brand -- a standalone restaurant is a brand of one --
so downstream code can always GROUP BY brand with no special-casing.

Usage:
    python bootstrap_chains.py --restaurants "v2_output/restaurants_*_restaurants.json" \
                               --out v2_output/chains.json
    python bootstrap_chains.py --restaurants "..." --review    # print groups, write nothing
"""

from __future__ import annotations

import argparse
import collections
import glob as globlib
import json
import re

# Area/branch tokens that appear in restaurant names but are not part of the
# brand. Longest first so "gulshan avenue" is removed before "gulshan".
AREA_TOKENS = [
    "jashimuddin avenue", "azampur railgate", "gulshan avenue", "shimanto square",
    "noorjahan road", "elephant road", "tajmohol road", "central road",
    "middle badda", "sat masjid", "satmosjid", "shatmosjid", "keari plaza",
    "nakhalpara", "centrepoint", "shahjadpur", "panthapath", "mohammadpur",
    "green road", "kalabagan", "baridhara", "dhanmondi", "mohakhali",
    "sukrabad", "hatirpool", "shyamoli", "flagship", "jigatola", "zigatola",
    "gulshan", "uttara", "banani", "airport", "badda",
]


def normalize_brand_name(name: str) -> str:
    """Brand key: lowercase, drop a ' - <branch>' suffix, drop area tokens,
    drop punctuation. 'Waffle Up - Dhanmondi' and 'Waffle Up' both -> 'waffle up'."""
    s = (name or "").lower().strip()
    # Drop everything after the first ' - ' (branch/outlet suffix), e.g.
    # "Greens & Seeds - Chef's Table Dhanmondi" -> "greens & seeds".
    s = re.split(r"\s+[-–—]\s+", s)[0]
    # Also handle "Ledor- Dhanmondi" / "Mithai- Jigatola" (no space before dash).
    s = re.split(r"[-–—]\s+", s)[0]
    s = re.sub(r"[^a-z0-9 &]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for token in AREA_TOKENS:
        s = re.sub(rf"\b{re.escape(token)}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Trailing outlet numbers: "gulshan 2" already lost 'gulshan'; drop the digit.
    s = re.sub(r"\s+\d+$", "", s).strip()
    return s


def brand_slug(normalized: str) -> str:
    """URL-safe stable key derived from the normalized brand name."""
    s = normalized.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# source_restaurant_code -> brand slug. One mechanism covers both directions:
# assign the same slug to force a MERGE, a different slug to force a SPLIT.
# Populated from the owner's one-time review of the candidate groups
# (`python bootstrap_chains.py --review`). Empty means normalization alone was
# correct for every group.
BRAND_OVERRIDES: dict[str, str] = {}


def _display_name(members: list[dict]) -> str:
    """Brand display name. chain_name is unreliable for GROUPING but good for
    DISPLAY ('Domino's Pizza' beats 'Domino's Pizza Gulshan'); fall back to the
    shortest raw name, which is usually the branch-less one."""
    chain_names = [m.get("chain_name") for m in members if m.get("chain_name")]
    if chain_names:
        return collections.Counter(chain_names).most_common(1)[0][0]
    return min((m["name"].strip() for m in members), key=len)


def build_brands(restaurants: list[dict]) -> list[dict]:
    """Group restaurants into brands. Every restaurant lands in exactly one
    brand; a standalone restaurant is a brand of one."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in restaurants:
        code = r["source_restaurant_code"]
        slug = BRAND_OVERRIDES.get(code) or brand_slug(normalize_brand_name(r["name"]))
        if not slug:
            slug = brand_slug(code)  # degenerate name -> unique brand of one
        groups[slug].append(r)

    brands = [
        {
            "slug": slug,
            "name": _display_name(members),
            "member_codes": sorted(m["source_restaurant_code"] for m in members),
        }
        for slug, members in groups.items()
    ]
    brands.sort(key=lambda b: b["slug"])
    return brands


def load_restaurants(glob_pattern: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(globlib.glob(glob_pattern)):
        with open(path, encoding="utf-8") as fh:
            rows.extend(json.load(fh))
    if not rows:
        raise SystemExit(f"No restaurants matched: {glob_pattern}")
    return rows


def _print_review_report(brands: list[dict], restaurants: list[dict]) -> None:
    """Print the multi-location candidate groups for the owner's one-time review.
    Anything wrong here gets pinned in BRAND_OVERRIDES."""
    by_code = {r["source_restaurant_code"]: r for r in restaurants}
    multi = [b for b in brands if len(b["member_codes"]) > 1]
    print(f"\n--- {len(multi)} candidate multi-location brands (review these) ---")
    for b in sorted(multi, key=lambda x: (-len(x["member_codes"]), x["slug"])):
        codes = [by_code[c].get("chain_code") for c in b["member_codes"]]
        flag = "  <-- source chain_code SPLIT/MISSING" if len(set(codes)) > 1 or None in codes else ""
        print(f'\n{b["slug"]!r} -> "{b["name"]}" ({len(b["member_codes"])} locations){flag}')
        for code in b["member_codes"]:
            r = by_code[code]
            print(f'    {code}  {r["name"]}   chain_code={r.get("chain_code")}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restaurants", required=True,
                    help='glob for classify *_restaurants.json, e.g. "v2_output/restaurants_*_restaurants.json"')
    ap.add_argument("--out", default="v2_output/chains.json")
    ap.add_argument("--review", action="store_true",
                    help="print candidate groups for human review; write nothing")
    args = ap.parse_args()

    restaurants = load_restaurants(args.restaurants)
    brands = build_brands(restaurants)

    multi = [b for b in brands if len(b["member_codes"]) > 1]
    print(f"Restaurants:            {len(restaurants)}")
    print(f"Brands:                 {len(brands)}")
    print(f"  multi-location:       {len(multi)}")
    print(f"  standalone (of one):  {len(brands) - len(multi)}")
    print(f"Overrides applied:      {len(BRAND_OVERRIDES)}")

    if args.review:
        _print_review_report(brands, restaurants)
        return

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(brands, fh, indent=2, ensure_ascii=False)
    print(f"\nWritten to: {args.out}")


if __name__ == "__main__":
    main()
