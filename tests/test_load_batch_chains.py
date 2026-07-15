from sqlalchemy import text


def test_every_restaurant_gets_a_chain_id(temp_db, db_session):
    """Standalone restaurants must be brands of one -- chain_id is never NULL."""
    import models
    from load_batch import upsert_chains

    chains = [
        {"slug": "waffle-up", "name": "Waffle Up", "member_codes": ["b41r", "fjsq"]},
        {"slug": "niribily", "name": "Niribily", "member_codes": ["avx4"]},
    ]
    for code, name in [("b41r", "Waffle Up - Dhanmondi"), ("fjsq", "Waffle Up"),
                       ("avx4", "Niribily Hotel & Restaurant")]:
        db_session.add(models.Restaurant(source_restaurant_code=code, name=name))
    db_session.commit()

    code_to_chain_id = upsert_chains(db_session, chains)
    for code in ("b41r", "fjsq", "avx4"):
        db_session.execute(
            text("update restaurants set chain_id=:cid where source_restaurant_code=:c"),
            {"cid": code_to_chain_id[code], "c": code},
        )
    db_session.commit()

    assert db_session.execute(
        text("select count(*) from restaurants where chain_id is null")).scalar() == 0
    # the two Waffle Up branches share one brand
    assert code_to_chain_id["b41r"] == code_to_chain_id["fjsq"]
    assert code_to_chain_id["avx4"] != code_to_chain_id["b41r"]
    # brand slug is stored as chain_code
    slugs = {r[0] for r in db_session.execute(text("select chain_code from restaurant_chains"))}
    assert slugs == {"waffle-up", "niribily"}


def test_upsert_chains_is_idempotent(temp_db, db_session):
    from load_batch import upsert_chains
    chains = [{"slug": "waffle-up", "name": "Waffle Up", "member_codes": ["b41r"]}]
    first = upsert_chains(db_session, chains)
    db_session.commit()
    second = upsert_chains(db_session, chains)
    db_session.commit()
    assert first == second
    assert db_session.execute(text("select count(*) from restaurant_chains")).scalar() == 1
