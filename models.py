from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey,
    DateTime, CheckConstraint, JSON, Boolean, Float, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    username = Column(String(50), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    reviews = relationship("Review", back_populates="user")


class FoodType(Base):
    __tablename__ = "food_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    taste_tags = Column(JSON, nullable=True)
    parent_id = Column(Integer, ForeignKey("food_types.id", ondelete="SET NULL"), nullable=True)

    restaurant_links = relationship("RestaurantFoodType", back_populates="food_type")
    parent = relationship("FoodType", remote_side=[id], back_populates="children")
    children = relationship("FoodType", back_populates="parent")


class Cuisine(Base):
    __tablename__ = "cuisines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)

    product_links = relationship("ProductCuisine", back_populates="cuisine")


class FlavorTag(Base):
    __tablename__ = "flavor_tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)

    product_links = relationship("ProductFlavorTag", back_populates="flavor_tag")


class Restaurant(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    area = Column(String(100), nullable=True)       # e.g. Dhanmondi, Gulshan
    address = Column(Text, nullable=True)
    phone = Column(String(30), nullable=True)
    google_maps_url = Column(Text, nullable=True)
    website_url = Column(Text, nullable=True)
    google_place_id = Column(String(255), nullable=True)
    image_url = Column(Text, nullable=True)

    # Scrape-sourced fields (see strip_restaurants.py / classify_batch.py pipeline)
    match_status = Column(String(20), nullable=False, server_default="unmatched")
    source_restaurant_code = Column(String(50), nullable=True, index=True)
    chain_name = Column(String(200), nullable=True)
    chain_code = Column(String(50), nullable=True)
    budget = Column(Integer, nullable=True)
    foodpanda_rating = Column(Float, nullable=True)
    foodpanda_review_number = Column(Integer, nullable=True)
    raw_cuisines = Column(JSON, nullable=True)   # unreliable restaurant-level hint; dish-level Cuisine is authoritative
    logo_url = Column(Text, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    food_type_links = relationship("RestaurantFoodType", back_populates="restaurant")
    reviews = relationship("Review", back_populates="restaurant")
    products = relationship("Product", back_populates="restaurant", cascade="all, delete-orphan")


class RestaurantFoodType(Base):
    """Join table: which restaurant serves which food type"""
    __tablename__ = "restaurant_food_types"

    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), primary_key=True)
    food_type_id = Column(Integer, ForeignKey("food_types.id", ondelete="CASCADE"), primary_key=True)

    restaurant = relationship("Restaurant", back_populates="food_type_links")
    food_type = relationship("FoodType", back_populates="restaurant_links")


class Product(Base):
    """A specific dish/menu item served by a restaurant."""
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "source_product_id", name="uq_product_restaurant_source"),
    )

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    food_type_id = Column(Integer, ForeignKey("food_types.id", ondelete="SET NULL"), nullable=True)

    source_product_id = Column(Integer, nullable=True, index=True)   # foodpanda product id, for upsert idempotency
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price_bdt = Column(Float, nullable=True)   # Float, not Integer: some foodpanda prices are fractional (e.g. 348.5)
    image_url = Column(Text, nullable=True)
    is_sold_out = Column(Boolean, nullable=False, server_default="0")
    category_raw = Column(String(255), nullable=True)          # foodpanda's own category label; weak hint only
    dietary_attributes_raw = Column(JSON, nullable=True)       # low-coverage/unreliable source hint
    variations = Column(JSON, nullable=True)                   # [{label, price_bdt}, ...]
    created_at = Column(DateTime, server_default=func.now())

    restaurant = relationship("Restaurant", back_populates="products")
    food_type = relationship("FoodType")
    reviews = relationship("Review", back_populates="product")
    cuisine_links = relationship("ProductCuisine", back_populates="product", cascade="all, delete-orphan")
    flavor_tag_links = relationship("ProductFlavorTag", back_populates="product", cascade="all, delete-orphan")


class ProductCuisine(Base):
    """Join table: which cuisines a dish belongs to (multi-label)"""
    __tablename__ = "product_cuisines"

    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    cuisine_id = Column(Integer, ForeignKey("cuisines.id", ondelete="CASCADE"), primary_key=True)

    product = relationship("Product", back_populates="cuisine_links")
    cuisine = relationship("Cuisine", back_populates="product_links")


class ProductFlavorTag(Base):
    """Join table: which flavor tags apply to a dish (multi-label)"""
    __tablename__ = "product_flavor_tags"

    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    flavor_tag_id = Column(Integer, ForeignKey("flavor_tags.id", ondelete="CASCADE"), primary_key=True)

    product = relationship("Product", back_populates="flavor_tag_links")
    flavor_tag = relationship("FlavorTag", back_populates="product_links")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    food_type_id = Column(Integer, ForeignKey("food_types.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewer_name = Column(String(100), nullable=True)
    rating = Column(Integer, CheckConstraint("rating BETWEEN 1 AND 5"), nullable=False)
    comment = Column(Text, nullable=True)
    source = Column(String(20), nullable=False, server_default="user")   # "user" vs "google"
    created_at = Column(DateTime, server_default=func.now())

    restaurant = relationship("Restaurant", back_populates="reviews")
    product = relationship("Product", back_populates="reviews")
    user = relationship("User", back_populates="reviews")
