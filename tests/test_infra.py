def test_temp_db_has_schema(temp_db, db_session):
    from sqlalchemy import text
    n = db_session.execute(text(
        "select count(*) from information_schema.tables "
        "where table_schema='public' and table_type='BASE TABLE'"
    )).scalar()
    # 20 core tables + restaurant_review_edits + product_review_edits
    assert n == 22


def test_temp_db_is_not_the_real_database(temp_db):
    assert temp_db.endswith("/khawon_test")
