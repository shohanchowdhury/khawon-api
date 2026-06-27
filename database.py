from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

USE_SQLITE = os.getenv("USE_SQLITE", "").lower() in ("1", "true", "yes")

if USE_SQLITE:
    DATABASE_URL = "sqlite:///./khawon_local.db"
elif os.getenv("DATABASE_PUBLIC_URL"):
    DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL")
else:
    DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError(
        "Set DATABASE_URL, DATABASE_PUBLIC_URL (Railway public host), or USE_SQLITE=1"
    )

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
