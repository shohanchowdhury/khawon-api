"""Test fixtures. The temp_db fixture builds a throwaway Postgres database
from schema.sql and repoints the app at it.

WARNING: database.py calls load_dotenv(), which re-reads DATABASE_PUBLIC_URL
from .env and that value WINS over DATABASE_URL. So we must SET both env vars
(load_dotenv does not override an already-set var). Popping is NOT enough --
doing so silently runs the tests against the real Railway database.
"""
import os
import sys

import psycopg2
import pytest
from dotenv import load_dotenv

TEST_DB_NAME = "khawon_test"


def _admin_url() -> str:
    load_dotenv()
    return os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def temp_db():
    admin_url = _admin_url()
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")

    base, _, _ = admin_url.rpartition("/")
    url = f"{base}/{TEST_DB_NAME}"

    with open("schema.sql", encoding="utf-8") as fh:
        schema_sql = fh.read()
    tmp = psycopg2.connect(url)
    tmp.autocommit = True
    tmp.cursor().execute(schema_sql)
    tmp.close()

    # Must SET both -- see module docstring.
    os.environ["DATABASE_URL"] = url
    os.environ["DATABASE_PUBLIC_URL"] = url
    os.environ["USE_SQLITE"] = ""
    for mod in ("database", "models", "main", "dish_detail", "restaurant_reviews"):
        sys.modules.pop(mod, None)

    yield url

    import database
    database.engine.dispose()  # release pooled sessions or DROP DATABASE fails
    cur.execute(
        "select pg_terminate_backend(pid) from pg_stat_activity "
        "where datname=%s and pid<>pg_backend_pid()",
        (TEST_DB_NAME,),
    )
    cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    cur.close()
    admin.close()


@pytest.fixture
def db_session(temp_db):
    """Fresh session; rolls back after each test."""
    from database import SessionLocal
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
