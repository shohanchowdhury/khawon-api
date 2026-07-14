"""SQLAlchemy models - SQL-first.

schema.sql is the source of truth for the database (it uses Postgres-native
features - PostGIS geography, generated columns, GiST/trigram indexes - that
don't round-trip through the ORM). These models are thin mappings over the
tables schema.sql creates, used for querying/writing from the app. They do
NOT drive DDL: main.py must not call create_all(); the schema is applied by
running schema.sql against the database.

The restaurants.geog generated GEOGRAPHY column is intentionally NOT mapped
here (needs geoalchemy2 and is generated anyway) - geo "near me" queries run
as raw SQL against ST_DWithin / <-> on that column.
"""

from sqlalchemy import (
    Column, Integer, SmallInteger, BigInteger, String, Text, ForeignKey,
    DateTime, CheckConstraint, Boolean, Numeric, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


# --------------------------------------------------------------------------
# Lookup / reference
# --------------------------------------------------------------------------
class Cuisine(Base):
    __tablename__ = "cuisines"
    id = Column(SmallInteger, primary_key=True)
    name = Column(Text, nullable=False, unique=True)


class FlavorTag(Base):
    __tablename__ = "flavor_tags"
    id = Column(SmallInteger, primary_key=True)
    slug = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)


class FoodCategory(Base):
    __tablename__ = "food_categories"
    id = Column(SmallInteger, primary_key=True)
    name = Column(Text, nullable=False, unique=True)


class FoodType(Base):
    __tablename__ = "food_types"
    id = Column(SmallInteger, primary_key=True)
    name = Column(Text, nullable=False, unique=True)

    sub_types = relationship("FoodSubType", back_populates="food_type")


class FoodSubType(Base):
    __tablename__ = "food_sub_types"
    __table_args__ = (UniqueConstraint("food_type_id", "name"),)
    id = Column(SmallInteger, primary_key=True)
    food_type_id = Column(SmallInteger, ForeignKey("food_types.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)

    food_type = relationship("FoodType", back_populates="sub_types")


# --------------------------------------------------------------------------
# Canonical dishes - cross-restaurant comparison identity
# --------------------------------------------------------------------------
class CanonicalDish(Base):
    __tablename__ = "canonical_dishes"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    aliases = Column(ARRAY(Text), nullable=False, server_default="{}")
    food_type_id = Column(SmallInteger, ForeignKey("food_types.id", ondelete="SET NULL"))
    food_sub_type_id = Column(SmallInteger, ForeignKey("food_sub_types.id", ondelete="SET NULL"))
    cuisine_id = Column(SmallInteger, ForeignKey("cuisines.id", ondelete="SET NULL"))
    category_id = Column(SmallInteger, ForeignKey("food_categories.id", ondelete="SET NULL"))
    image_url = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    food_type = relationship("FoodType")
    food_sub_type = relationship("FoodSubType")
    cuisine = relationship("Cuisine")
    category = relationship("FoodCategory")
    products = relationship("Product", back_populates="canonical_dish")


# --------------------------------------------------------------------------
# Restaurant domain
# --------------------------------------------------------------------------
class RestaurantChain(Base):
    __tablename__ = "restaurant_chains"
    id = Column(Integer, primary_key=True)
    chain_code = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)


class Restaurant(Base):
    __tablename__ = "restaurants"
    id = Column(Integer, primary_key=True)
    source_restaurant_code = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    address = Column(Text)
    latitude = Column(Numeric(10, 8))
    longitude = Column(Numeric(11, 8))
    # geog (GEOGRAPHY, generated) intentionally unmapped - see module docstring
    rating = Column(Numeric(2, 1))
    review_count = Column(Integer, nullable=False, server_default="0")
    old_rating = Column(Numeric(2, 1))
    old_review_count = Column(Integer)
    budget_tier = Column(SmallInteger)
    phone = Column(Text)
    city = Column(Text, nullable=False, server_default="Dhaka")
    area = Column(Text)
    chain_id = Column(Integer, ForeignKey("restaurant_chains.id", ondelete="SET NULL"))
    hero_image_url = Column(Text)
    logo_image_url = Column(Text)
    google_place_id = Column(Text)
    match_status = Column(Text, nullable=False, server_default="unmatched")
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    chain = relationship("RestaurantChain")
    products = relationship("Product", back_populates="restaurant", cascade="all, delete-orphan")
    cuisine_links = relationship("RestaurantCuisine", back_populates="restaurant", cascade="all, delete-orphan")


class RestaurantCuisine(Base):
    __tablename__ = "restaurant_cuisines"
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), primary_key=True)
    cuisine_id = Column(SmallInteger, ForeignKey("cuisines.id", ondelete="CASCADE"), primary_key=True)

    restaurant = relationship("Restaurant", back_populates="cuisine_links")
    cuisine = relationship("Cuisine")


class RestaurantSource(Base):
    __tablename__ = "restaurant_sources"
    __table_args__ = (UniqueConstraint("restaurant_id", "source_name"),)
    id = Column(Integer, primary_key=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    source_name = Column(Text, nullable=False)
    source_url = Column(Text)
    last_scraped_at = Column(DateTime(timezone=True))
    raw_metadata = Column(JSONB)


# --------------------------------------------------------------------------
# Product / menu domain
# --------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    source_product_id = Column(BigInteger, nullable=False, unique=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text)
    base_price_bdt = Column(Numeric(10, 2), nullable=False)
    image_url = Column(Text)
    is_sold_out = Column(Boolean, nullable=False, server_default="false")
    category_id = Column(SmallInteger, ForeignKey("food_categories.id", ondelete="SET NULL"))
    cuisine_id = Column(SmallInteger, ForeignKey("cuisines.id", ondelete="SET NULL"))
    food_type_id = Column(SmallInteger, ForeignKey("food_types.id", ondelete="SET NULL"))
    food_sub_type_id = Column(SmallInteger, ForeignKey("food_sub_types.id", ondelete="SET NULL"))
    canonical_dish_id = Column(Integer, ForeignKey("canonical_dishes.id", ondelete="SET NULL"))
    is_active = Column(Boolean, nullable=False, server_default="true")
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    rating = Column(Numeric(2, 1))
    review_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    restaurant = relationship("Restaurant", back_populates="products")
    category = relationship("FoodCategory")
    cuisine = relationship("Cuisine")
    food_type = relationship("FoodType")
    food_sub_type = relationship("FoodSubType")
    canonical_dish = relationship("CanonicalDish", back_populates="products")
    variations = relationship("ProductVariation", back_populates="product", cascade="all, delete-orphan")
    flavor_tag_links = relationship("ProductFlavorTag", back_populates="product", cascade="all, delete-orphan")
    reviews = relationship("ProductReview", back_populates="product", cascade="all, delete-orphan")


class ProductVariation(Base):
    __tablename__ = "product_variations"
    __table_args__ = (UniqueConstraint("product_id", "label"),)
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    label = Column(Text, nullable=False, server_default="Regular")
    price_bdt = Column(Numeric(10, 2), nullable=False)
    sort_order = Column(SmallInteger, nullable=False, server_default="0")

    product = relationship("Product", back_populates="variations")


class ProductFlavorTag(Base):
    __tablename__ = "product_flavor_tags"
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    flavor_tag_id = Column(SmallInteger, ForeignKey("flavor_tags.id", ondelete="CASCADE"), primary_key=True)

    product = relationship("Product", back_populates="flavor_tag_links")
    flavor_tag = relationship("FlavorTag")


# --------------------------------------------------------------------------
# Users & reviews (restaurant-level and product-level, parallel stacks)
# --------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email IS NOT NULL OR phone IS NOT NULL"),)
    id = Column(Integer, primary_key=True)
    email = Column(Text, unique=True)
    phone = Column(Text, unique=True)
    password_hash = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    restaurant_reviews = relationship("RestaurantReview", back_populates="user")
    product_reviews = relationship("ProductReview", back_populates="user")

    # Back-compat read aliases so existing API code (which predates the
    # username->display_name / hashed_password->password_hash rename) keeps
    # working for serialization and password checks. Writes use the real
    # column names (display_name / password_hash) directly.
    @property
    def username(self) -> str:
        return self.display_name

    @property
    def hashed_password(self) -> str:
        return self.password_hash


class RestaurantReview(Base):
    __tablename__ = "restaurant_reviews"
    __table_args__ = (UniqueConstraint("user_id", "restaurant_id"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    rating = Column(SmallInteger, CheckConstraint("rating BETWEEN 1 AND 5"), nullable=False)
    body = Column(Text)
    status = Column(Text, nullable=False, server_default="pending")
    is_verified_visit = Column(Boolean, nullable=False, server_default="false")
    helpful_count = Column(Integer, nullable=False, server_default="0")
    not_helpful_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="restaurant_reviews")
    restaurant = relationship("Restaurant")
    photos = relationship("RestaurantReviewPhoto", back_populates="review", cascade="all, delete-orphan")


class RestaurantReviewPhoto(Base):
    __tablename__ = "restaurant_review_photos"
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("restaurant_reviews.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(Text, nullable=False)
    sort_order = Column(SmallInteger, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    review = relationship("RestaurantReview", back_populates="photos")


class RestaurantReviewVote(Base):
    __tablename__ = "restaurant_review_votes"
    review_id = Column(Integer, ForeignKey("restaurant_reviews.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_helpful = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ProductReview(Base):
    __tablename__ = "product_reviews"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    rating = Column(SmallInteger, CheckConstraint("rating BETWEEN 1 AND 5"), nullable=False)
    body = Column(Text)
    status = Column(Text, nullable=False, server_default="pending")
    is_verified_order = Column(Boolean, nullable=False, server_default="false")
    helpful_count = Column(Integer, nullable=False, server_default="0")
    not_helpful_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="product_reviews")
    product = relationship("Product", back_populates="reviews")
    photos = relationship("ProductReviewPhoto", back_populates="review", cascade="all, delete-orphan")


class ProductReviewPhoto(Base):
    __tablename__ = "product_review_photos"
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("product_reviews.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(Text, nullable=False)
    sort_order = Column(SmallInteger, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    review = relationship("ProductReview", back_populates="photos")


class ProductReviewVote(Base):
    __tablename__ = "product_review_votes"
    review_id = Column(Integer, ForeignKey("product_reviews.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_helpful = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
