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

import collections
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
