"""Bootstrap canonical dishes from loaded dish data (rule-based first pass).

The canonical dish is the unit of cross-restaurant comparison ("Chicken
Kacchi" as a concept, which many restaurants' menu items map to). This
script derives them from real data:

  1. Normalize every active dish's name (strip "1:1 -"-style scrape
     prefixes, sizes/counts/parentheticals, punctuation, casing).
  2. Group dishes by (food_type_id, normalized_name).
  3. Where a group spans >= 2 DISTINCT restaurants, create a CanonicalDish
     (name = most common raw spelling, aliases = all observed variants)
     and link the group's dishes to it.

Singleton dishes stay unmapped - still searchable by name, just not
comparable yet. LLM/manual refinement of the long tail is future work.

Idempotent: re-running re-derives groups, reuses existing canonical dishes
by name, and only fills in missing links.

USAGE:
    python bootstrap_canonical_dishes.py            (local: set USE_SQLITE=1)
"""
import re
from collections import Counter, defaultdict

from database import SessionLocal
import models

# "1:1 - Steamed rice...", "2:1 -" etc. scrape ratio prefixes
RATIO_PREFIX = re.compile(r"^\s*\d+\s*:\s*\d+\s*-?\s*")
# trailing/embedded size or count markers
SIZE_MARKERS = re.compile(
    r"\b(small|medium|large|regular|full|half|mini|jumbo|family|combo|"
    r"\d+\s*(pcs?|pieces?|inch|\"|ltr|l|ml|gm|g|kg))\b\.?",
    re.IGNORECASE,
)
PARENTHETICAL = re.compile(r"\([^)]*\)")
NON_ALNUM = re.compile(r"[^a-z0-9ঀ-৿ ]+")   # keep Bangla script
MULTISPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    s = raw.lower().strip()
    s = RATIO_PREFIX.sub("", s)
    s = PARENTHETICAL.sub(" ", s)
    s = SIZE_MARKERS.sub(" ", s)
    s = NON_ALNUM.sub(" ", s)
    s = MULTISPACE.sub(" ", s).strip()
    return s


def main():
    db = SessionLocal()
    try:
        dishes = (
            db.query(models.Dish)
            .filter(models.Dish.is_active.is_(True))
            .all()
        )

        groups = defaultdict(list)   # (food_type_id, normalized_name) -> [Dish, ...]
        for d in dishes:
            norm = normalize_name(d.name)
            if not norm:
                continue
            groups[(d.food_type_id, norm)].append(d)

        existing_by_name = {
            c.name: c for c in db.query(models.CanonicalDish).all()
        }

        created = 0
        linked = 0
        multi_restaurant_groups = 0

        for (food_type_id, norm), members in groups.items():
            restaurant_ids = {d.restaurant_id for d in members}
            if len(restaurant_ids) < 2:
                continue
            multi_restaurant_groups += 1

            # Canonical display name = most common raw spelling (cleaned of the
            # ratio prefix), aliases = every observed distinct raw variant.
            raw_names = [RATIO_PREFIX.sub("", d.name).strip() for d in members]
            display_name = Counter(raw_names).most_common(1)[0][0]
            aliases = sorted({n for n in raw_names if n != display_name})

            canonical = existing_by_name.get(display_name)
            if canonical is None:
                canonical = models.CanonicalDish(
                    name=display_name,
                    food_type_id=food_type_id,
                    aliases=aliases or None,
                )
                db.add(canonical)
                db.flush()
                existing_by_name[display_name] = canonical
                created += 1
            elif aliases:
                merged = sorted(set(canonical.aliases or []) | set(aliases))
                canonical.aliases = merged or None

            for d in members:
                if d.canonical_dish_id != canonical.id:
                    d.canonical_dish_id = canonical.id
                    linked += 1

        db.commit()

        total = len(dishes)
        mapped = db.query(models.Dish).filter(
            models.Dish.canonical_dish_id.isnot(None),
            models.Dish.is_active.is_(True),
        ).count()
        print(f"Active dishes: {total}")
        print(f"Multi-restaurant name groups: {multi_restaurant_groups}")
        print(f"Canonical dishes created this run: {created} "
              f"(total now: {db.query(models.CanonicalDish).count()})")
        print(f"Dish links set this run: {linked}")
        print(f"Coverage: {mapped}/{total} dishes mapped to a canonical dish "
              f"({100 * mapped / total:.1f}%)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
