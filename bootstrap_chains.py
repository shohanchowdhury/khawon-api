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
