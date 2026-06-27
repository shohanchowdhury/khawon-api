from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import engine
import models

from routers import auth, food_types, restaurants, reviews, search


def run_migrations():
    """Add new columns to existing dev databases without Alembic."""
    inspector = inspect(engine)
    if "reviews" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("reviews")}
    if "user_id" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE reviews ADD COLUMN user_id INTEGER"))


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
app.include_router(restaurants.router)
app.include_router(reviews.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "Bangladesh Food Finder API is running 🍜"}
