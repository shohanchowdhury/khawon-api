"""Seed sample Bangladesh food data for local development.

Existing Postgres DBs: add the column once if missing:
  ALTER TABLE food_types ADD COLUMN IF NOT EXISTS taste_tags JSON;
Then re-run: python seed.py
"""

from sqlalchemy import inspect, text

from database import SessionLocal, engine
import models

SAMPLE_FOOD_TYPES = [
    {
        "name": "Biriyani",
        "description": "Fragrant spiced rice with meat",
        "taste_tags": ["fragrant", "spiced", "rich", "meaty", "aromatic"],
    },
    {
        "name": "Kacchi Biriyani",
        "description": "Slow-cooked mutton biriyani in dum style",
        "taste_tags": ["slow-cooked", "tender", "aromatic", "rich", "spiced"],
    },
    {
        "name": "Ramen",
        "description": "Japanese noodle soup",
        "taste_tags": ["soupy", "creamy", "spicy", "meaty", "savory", "umami"],
    },
    {
        "name": "Fuchka",
        "description": "Crispy shells with tangy tamarind water",
        "taste_tags": ["tangy", "crispy", "spicy", "sour", "street"],
    },
    {
        "name": "Chotpoti",
        "description": "Spiced chickpeas and potatoes with tamarind",
        "taste_tags": ["spicy", "tangy", "hearty", "street", "savory"],
    },
    {
        "name": "Haleem",
        "description": "Slow-cooked lentil and meat stew",
        "taste_tags": ["hearty", "slow-cooked", "spiced", "meaty", "comforting"],
    },
    {
        "name": "Khichuri",
        "description": "Comfort rice and lentil one-pot dish",
        "taste_tags": ["comforting", "mild", "hearty", "warm", "simple"],
    },
    {
        "name": "Beef Tehari",
        "description": "Spiced rice cooked with beef",
        "taste_tags": ["spiced", "meaty", "rich", "aromatic", "hearty"],
    },
    {
        "name": "Paratha",
        "description": "Flaky layered flatbread, often with egg or keema",
        "taste_tags": ["flaky", "buttery", "crispy", "savory", "warm"],
    },
    {
        "name": "Shawarma",
        "description": "Wrapped grilled meat with sauce and salad",
        "taste_tags": ["grilled", "juicy", "savory", "spiced", "wrapped"],
    },
    {
        "name": "Burger",
        "description": "Classic beef or chicken burgers",
        "taste_tags": ["juicy", "savory", "cheesy", "hearty", "grilled"],
    },
    {
        "name": "Pizza",
        "description": "Wood-fired or pan pizza slices and pies",
        "taste_tags": ["cheesy", "crispy", "saucy", "savory", "shareable"],
    },
    {
        "name": "Kebab",
        "description": "Grilled skewered meat, seekh or shami style",
        "taste_tags": ["grilled", "smoky", "spiced", "juicy", "meaty"],
    },
    {
        "name": "Mishti Doi",
        "description": "Sweet fermented yogurt dessert",
        "taste_tags": ["sweet", "creamy", "cool", "tangy", "dessert"],
    },
    {
        "name": "Pitha",
        "description": "Traditional rice cakes and winter sweets",
        "taste_tags": ["sweet", "soft", "traditional", "warm", "festive"],
    },
]

SAMPLE_RESTAURANTS = [
    {
        "name": "Sultan's Dine",
        "area": "Dhanmondi",
        "address": "Road 27, Dhanmondi, Dhaka",
        "phone": "+880 1711-000001",
        "google_maps_url": "https://maps.google.com",
        "food_types": ["Biriyani"],
    },
    {
        "name": "Tokyo Express",
        "area": "Gulshan",
        "address": "Gulshan 2, Dhaka",
        "phone": "+880 1711-000002",
        "google_maps_url": "https://maps.google.com",
        "food_types": ["Ramen"],
    },
    {
        "name": "Old Dhaka Fuchka House",
        "area": "Old Dhaka",
        "address": "Chawkbazar, Old Dhaka",
        "phone": "+880 1711-000003",
        "google_maps_url": "https://maps.google.com",
        "food_types": ["Fuchka", "Biriyani"],
    },
]

SAMPLE_REVIEWS = [
    {"restaurant": "Sultan's Dine", "food_type": "Biriyani", "reviewer_name": "Rahim", "rating": 5, "comment": "Best biriyani in Dhanmondi!"},
    {"restaurant": "Tokyo Express", "food_type": "Ramen", "reviewer_name": "Sadia", "rating": 4, "comment": "Rich broth, generous portions."},
    {"restaurant": "Old Dhaka Fuchka House", "food_type": "Fuchka", "reviewer_name": "Karim", "rating": 5, "comment": "Authentic street-style fuchka."},
]


def ensure_taste_tags_column():
    """Add taste_tags column on existing DBs (no Alembic in this repo)."""
    inspector = inspect(engine)
    if "food_types" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("food_types")}
    if "taste_tags" in columns:
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE food_types ADD COLUMN taste_tags JSON"))
        else:
            conn.execute(text("ALTER TABLE food_types ADD COLUMN taste_tags JSON"))


def seed_food_types(db):
    added = 0
    updated = 0
    for ft_data in SAMPLE_FOOD_TYPES:
        existing = db.query(models.FoodType).filter(
            models.FoodType.name.ilike(ft_data["name"])
        ).first()
        if existing:
            if existing.taste_tags != ft_data.get("taste_tags"):
                existing.taste_tags = ft_data.get("taste_tags")
                updated += 1
            continue
        db.add(models.FoodType(**ft_data))
        added += 1
    db.commit()
    return added, updated


def seed():
    models.Base.metadata.create_all(bind=engine)
    ensure_taste_tags_column()
    db = SessionLocal()

    try:
        added, updated = seed_food_types(db)
        if added:
            print(f"Added {added} food type(s).")
        if updated:
            print(f"Updated taste_tags on {updated} food type(s).")
        if not added and not updated:
            print("All sample food types already exist with taste tags.")

        if db.query(models.Restaurant).count() > 0:
            return

        food_type_map = {
            ft.name: ft.id
            for ft in db.query(models.FoodType).all()
        }

        restaurant_map = {}
        for r_data in SAMPLE_RESTAURANTS:
            food_names = r_data.pop("food_types")
            restaurant = models.Restaurant(**r_data)
            db.add(restaurant)
            db.flush()
            restaurant_map[restaurant.name] = restaurant.id

            for name in food_names:
                link = models.RestaurantFoodType(
                    restaurant_id=restaurant.id,
                    food_type_id=food_type_map[name],
                )
                db.add(link)

        for rev_data in SAMPLE_REVIEWS:
            review = models.Review(
                restaurant_id=restaurant_map[rev_data["restaurant"]],
                food_type_id=food_type_map[rev_data["food_type"]],
                reviewer_name=rev_data["reviewer_name"],
                rating=rev_data["rating"],
                comment=rev_data["comment"],
            )
            db.add(review)

        db.commit()
        print("Seeded restaurants and reviews.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
