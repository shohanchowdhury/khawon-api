from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import engine
import models

from routers import auth, food_images, food_types, places, restaurants, reviews, search


def run_migrations():
    """Add new columns to existing dev databases without Alembic."""
    inspector = inspect(engine)
    if "reviews" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("reviews")}
    if "user_id" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE reviews ADD COLUMN user_id INTEGER"))

    if "food_types" in inspector.get_table_names():
        ft_columns = {col["name"] for col in inspector.get_columns("food_types")}
        if "image_url" not in ft_columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE food_types ADD COLUMN IF NOT EXISTS image_url TEXT")
                )

    if "restaurants" in inspector.get_table_names():
        rest_columns = {col["name"] for col in inspector.get_columns("restaurants")}
        if "website_url" not in rest_columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS website_url TEXT")
                )
        if "google_place_id" not in rest_columns:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS google_place_id VARCHAR(255)"
                    )
                )
        if "image_url" not in rest_columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS image_url TEXT")
                )


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
app.include_router(restaurants.router)
app.include_router(reviews.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "Bangladesh Food Finder API is running 🍜"}
