from sqlalchemy import text


def test_orphan_chains_are_deleted(temp_db, db_session):
    """The DB already holds chains keyed by foodpanda's chain_code. Once every
    restaurant points at a brand slug, those old rows are unreferenced cruft in
    a table the brand model depends on."""
    import models
    from load_batch import delete_orphan_chains

    stale = models.RestaurantChain(chain_code="ck6kl", name="Shawarma Damasco")  # foodpanda-era
    live = models.RestaurantChain(chain_code="waffle-up", name="Waffle Up")
    db_session.add_all([stale, live])
    db_session.flush()
    db_session.add(models.Restaurant(source_restaurant_code="b41r", name="Waffle Up",
                                     chain_id=live.id))
    db_session.commit()

    removed = delete_orphan_chains(db_session)
    db_session.commit()

    assert removed == 1
    codes = {r[0] for r in db_session.execute(text("select chain_code from restaurant_chains"))}
    assert codes == {"waffle-up"}


def test_delete_orphan_chains_keeps_referenced_chains(temp_db, db_session):
    import models
    from load_batch import delete_orphan_chains

    a = models.RestaurantChain(chain_code="brand-a", name="A")
    b = models.RestaurantChain(chain_code="brand-b", name="B")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add_all([
        models.Restaurant(source_restaurant_code="r1", name="A1", chain_id=a.id),
        models.Restaurant(source_restaurant_code="r2", name="B1", chain_id=b.id),
    ])
    db_session.commit()

    assert delete_orphan_chains(db_session) == 0
    db_session.commit()
    assert db_session.execute(text("select count(*) from restaurant_chains")).scalar() == 2
