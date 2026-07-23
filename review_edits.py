"""Edit-history summaries for both review stacks.

Rows in `*_review_edits` are written by database triggers (see schema.sql) and
hold PREVIOUS versions of a review; the live row is always the current one. The
oldest history row is therefore what the review originally said.

That is the bit worth showing a reader: a 5-star review later rewritten to 1
star -- or the reverse -- is exactly the pattern that erodes trust in ratings,
and the survey that motivated this product flagged fake/paid reviews unprompted.

Batched on purpose. Review lists are paginated, so a per-review lookup here
would be an N+1 on a hot read path.
"""
from sqlalchemy.orm import Session


def edit_stats(db: Session, edit_model, review_ids: list[int]) -> dict[int, dict]:
    """Map review_id -> {edit_count, original_rating, last_edited_at}.

    `edit_model` is models.ProductReviewEdit or models.RestaurantReviewEdit.
    Reviews that were never edited are absent from the result entirely, so
    callers should treat a missing key as "not edited" rather than expecting
    zeroes.
    """
    if not review_ids:
        return {}

    # Ordered oldest-first per review, so the first row seen is the original
    # version and the last one seen is the most recent edit. Tie-breaking on id
    # keeps this deterministic when several edits land in one transaction and
    # share a timestamp.
    rows = (
        db.query(edit_model.review_id, edit_model.rating, edit_model.superseded_at)
        .filter(edit_model.review_id.in_(review_ids))
        .order_by(edit_model.review_id, edit_model.superseded_at, edit_model.id)
        .all()
    )

    stats: dict[int, dict] = {}
    for review_id, rating, superseded_at in rows:
        entry = stats.get(review_id)
        if entry is None:
            stats[review_id] = {
                "edit_count": 1,
                "original_rating": rating,      # first row = oldest = original
                "last_edited_at": superseded_at,
            }
        else:
            entry["edit_count"] += 1
            entry["last_edited_at"] = superseded_at
    return stats
