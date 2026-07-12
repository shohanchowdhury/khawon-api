from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import engine
import models

from routers import auth, dishes, food_images, food_types, places, restaurants, reviews, search


def _add_missing_columns(inspector, table, columns_to_add):
    """Add any of columns_to_add ({name: sql_type}) missing from `table`.

    Plain ADD COLUMN, no "IF NOT EXISTS" — the existence check above is
    already the guard, and "IF NOT EXISTS" is invalid syntax on SQLite
    (only Postgres supports it), so relying on it broke local dev DBs.
    """
    if table not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns(table)}
    for col_name, col_type in columns_to_add.items():
        if col_name not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))


def run_migrations():
    """Add new columns to existing dev databases without Alembic."""
    inspector = inspect(engine)
    if "reviews" not in inspector.get_table_names():
        return

    _add_missing_columns(inspector, "reviews", {
        "user_id": "INTEGER",
        "dish_id": "INTEGER",
        "source": "VARCHAR(20) DEFAULT 'user'",
    })
    _add_missing_columns(inspector, "food_types", {
        "image_url": "TEXT",
        "taste_tags": "JSON",
        "parent_id": "INTEGER",
    })
    _add_missing_columns(inspector, "restaurants", {
        "website_url": "TEXT",
        "google_place_id": "VARCHAR(255)",
        "image_url": "TEXT",
        "match_status": "VARCHAR(20) DEFAULT 'unmatched'",
        "source_restaurant_code": "VARCHAR(50)",
        "chain_name": "VARCHAR(200)",
        "chain_code": "VARCHAR(50)",
        "budget": "INTEGER",
        "foodpanda_rating": "FLOAT",
        "foodpanda_review_number": "INTEGER",
        "raw_cuisines": "JSON",
        "logo_url": "TEXT",
        "latitude": "FLOAT",
        "longitude": "FLOAT",
    })


run_migrations()
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Bangladesh Food Finder",
    description="Search for the best restaurants in Bangladesh by food type.",
    version="1.1.0",
)

# Allow the React frontend to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this to your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(search.router)
app.include_router(food_types.router)
app.include_router(food_images.router)
app.include_router(places.router)
app.include_router(dishes.router)
app.include_router(restaurants.router)
app.include_router(reviews.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "Bangladesh Food Finder API is running 🍜"}
