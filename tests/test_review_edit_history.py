"""Edit history for both review stacks.

History rows are written by DB TRIGGERS, not application code, so these tests
drive the models directly -- if the trigger is missing from schema.sql the
tests fail regardless of what Python does.
"""
import itertools

_pid = itertools.count(41000)
_uid = itertools.count(1)


def _user(db):
    import models
    n = next(_uid)
    u = models.User(email=f"u{n}@t.t", display_name=f"u{n}", password_hash="x")
    db.add(u)
    db.flush()
    return u


def _product(db):
    import models
    chain = models.RestaurantChain(chain_code=f"c{next(_pid)}", name="Brand")
    db.add(chain)
    db.flush()
    r = models.Restaurant(source_restaurant_code=f"r{next(_pid)}", name="Branch", chain_id=chain.id)
    db.add(r)
    db.flush()
    p = models.Product(source_product_id=next(_pid), restaurant_id=r.id,
                       name="Dish", base_price_bdt=100, normalized_name="dish")
    db.add(p)
    db.flush()
    return r, p


def _product_review(db, **kw):
    import models
    u = _user(db)
    _, p = _product(db)
    rv = models.ProductReview(user_id=u.id, product_id=p.id, status="approved", **kw)
    db.add(rv)
    db.commit()
    return rv


# --------------------------------------------------------------- the trigger --

def test_creating_a_review_writes_no_history(temp_db, db_session):
    import models
    rv = _product_review(db_session, rating=5, body="Great")
    assert db_session.query(models.ProductReviewEdit).filter_by(review_id=rv.id).count() == 0


def test_editing_a_review_preserves_the_previous_version(temp_db, db_session):
    import models
    rv = _product_review(db_session, rating=5, body="Best burger in Dhaka")

    rv.rating = 1
    rv.body = "Actually terrible"
    db_session.commit()

    (old,) = db_session.query(models.ProductReviewEdit).filter_by(review_id=rv.id).all()
    assert old.rating == 5, "history must hold the PREVIOUS value, not the new one"
    assert old.body == "Best burger in Dhaka"
    assert rv.rating == 1, "the live row is still the current version"


def test_vote_counters_do_not_count_as_an_edit(temp_db, db_session):
    """helpful_count changes fire UPDATE constantly. If they created history
    rows, every review would look edited and the trust signal would be noise."""
    import models
    rv = _product_review(db_session, rating=4, body="Good")

    rv.helpful_count = 7
    rv.not_helpful_count = 2
    db_session.commit()

    assert db_session.query(models.ProductReviewEdit).filter_by(review_id=rv.id).count() == 0


def test_moderation_status_change_is_captured(temp_db, db_session):
    import models
    rv = _product_review(db_session, rating=5, body="Spam")

    rv.status = "rejected"
    db_session.commit()

    (old,) = db_session.query(models.ProductReviewEdit).filter_by(review_id=rv.id).all()
    assert old.status == "approved"


def test_editing_to_identical_values_writes_no_history(temp_db, db_session):
    """Resubmitting the same content is not an edit."""
    import models
    rv = _product_review(db_session, rating=3, body="Fine")

    rv.rating = 3
    rv.body = "Fine"
    db_session.commit()

    assert db_session.query(models.ProductReviewEdit).filter_by(review_id=rv.id).count() == 0


def test_body_null_transitions_are_detected(temp_db, db_session):
    """IS DISTINCT FROM, not <>: a plain comparison against NULL yields NULL,
    so adding text to a bodyless review would slip through unrecorded."""
    import models
    rv = _product_review(db_session, rating=4, body=None)

    rv.body = "Adding a comment later"
    db_session.commit()

    (old,) = db_session.query(models.ProductReviewEdit).filter_by(review_id=rv.id).all()
    assert old.body is None


def test_deleting_a_review_cascades_its_history(temp_db, db_session):
    import models
    rv = _product_review(db_session, rating=5, body="One")
    rv.rating = 2
    db_session.commit()
    review_id = rv.id
    assert db_session.query(models.ProductReviewEdit).filter_by(review_id=review_id).count() == 1

    db_session.delete(rv)
    db_session.commit()

    assert db_session.query(models.ProductReviewEdit).filter_by(review_id=review_id).count() == 0


def test_restaurant_reviews_have_the_same_history(temp_db, db_session):
    """Both stacks, not just products."""
    import models
    u = _user(db_session)
    r, _ = _product(db_session)
    rv = models.RestaurantReview(user_id=u.id, restaurant_id=r.id,
                                 rating=5, body="Lovely", status="approved")
    db_session.add(rv)
    db_session.commit()

    rv.rating = 2
    db_session.commit()

    (old,) = db_session.query(models.RestaurantReviewEdit).filter_by(review_id=rv.id).all()
    assert old.rating == 5


# ------------------------------------------------------------ the read side --

def test_edit_stats_reports_original_rating_across_several_edits(temp_db, db_session):
    import models
    from review_edits import edit_stats
    rv = _product_review(db_session, rating=5, body="v1")

    for rating, body in ((4, "v2"), (3, "v3"), (1, "v4")):
        rv.rating, rv.body = rating, body
        db_session.commit()

    stats = edit_stats(db_session, models.ProductReviewEdit, [rv.id])[rv.id]
    assert stats["edit_count"] == 3
    assert stats["original_rating"] == 5, "original = oldest history row, not the latest"
    assert stats["last_edited_at"] is not None


def test_edit_stats_omits_unedited_reviews(temp_db, db_session):
    import models
    from review_edits import edit_stats
    rv = _product_review(db_session, rating=5, body="untouched")
    assert edit_stats(db_session, models.ProductReviewEdit, [rv.id]) == {}


def test_edit_stats_handles_an_empty_id_list(temp_db, db_session):
    import models
    from review_edits import edit_stats
    assert edit_stats(db_session, models.ProductReviewEdit, []) == {}


def test_listing_surfaces_the_edit_signal(temp_db, db_session):
    """The whole point: a reader can see a 5-star review was rewritten to 1."""
    from dish_detail import get_reviews_for_dish
    rv = _product_review(db_session, rating=5, body="Amazing")
    product_id = rv.product_id
    rv.rating = 1
    rv.body = "Changed my mind"
    db_session.commit()

    (out,), total = get_reviews_for_dish(db_session, product_id)
    assert total == 1
    assert out.is_edited is True
    assert out.edit_count == 1
    assert out.original_rating == 5
    assert out.rating == 1


def test_unedited_reviews_report_clean_defaults(temp_db, db_session):
    from dish_detail import get_reviews_for_dish
    rv = _product_review(db_session, rating=4, body="As written")

    (out,), _ = get_reviews_for_dish(db_session, rv.product_id)
    assert out.is_edited is False
    assert out.edit_count == 0
    assert out.original_rating is None
    assert out.last_edited_at is None
