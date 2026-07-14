from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, dishes, food_images, food_types, places, restaurants, reviews

# SQL-first: the database schema is created and migrated by applying
# schema.sql (it uses Postgres-native features - PostGIS geography, generated
# columns, GiST/trigram indexes - that don't round-trip through the ORM).
# The app must NOT create_all() or run ad-hoc column migrations here; models.py
# is a thin read/write mapping over the tables schema.sql already created.

app = FastAPI(
    title="Khawon",
    description="Search for a dish and compare it across Dhaka restaurants, with reviews.",
    version="2.0.0",
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
app.include_router(food_types.router)
app.include_router(food_images.router)
app.include_router(places.router)
app.include_router(dishes.router)
app.include_router(restaurants.router)
app.include_router(reviews.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "Bangladesh Food Finder API is running 🍜"}
