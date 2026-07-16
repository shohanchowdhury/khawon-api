from sqlalchemy import text


def test_products_has_normalized_name_column(temp_db, db_session):
    """The temp DB is built from schema.sql, so this proves fresh databases get
    the column."""
    row = db_session.execute(text(
        "select data_type from information_schema.columns "
        "where table_name='products' and column_name='normalized_name'"
    )).first()
    assert row is not None, "products.normalized_name missing from schema.sql"
    assert row[0] == "text"


def test_normalized_name_is_indexed(temp_db, db_session):
    idx = {r[0] for r in db_session.execute(text(
        "select indexname from pg_indexes where tablename='products'"
    ))}
    assert "idx_products_normalized_name" in idx
