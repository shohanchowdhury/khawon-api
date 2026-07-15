from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


# ── Users ────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)

class UserOut(BaseModel):
    id: int
    email: EmailStr
    username: str
    created_at: datetime

    model_config = {"from_attributes": True}

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Food Types ──────────────────────────────────────────────

class FoodTypeCreate(BaseModel):
    name: str
    description: Optional[str] = None

class FoodTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    parent_id: Optional[int] = None

    model_config = {"from_attributes": True}

class FoodTypePopularOut(FoodTypeOut):
    restaurant_count: int = 0
    review_count: int = 0
    average_rating: Optional[float] = None


class FoodSubTypeOut(BaseModel):
    id: int
    name: str
    food_type_id: int
    dish_count: int
    image_urls: list[str]


class FoodSubTypeListResult(BaseModel):
    food_type: FoodTypeOut
    sub_types: list[FoodSubTypeOut]


class FoodImageSearchResult(BaseModel):
    id: str
    image_url: str
    thumbnail_url: str
    title: Optional[str] = None
    source_url: Optional[str] = None


class FoodImageSearchResponse(BaseModel):
    photos: list[FoodImageSearchResult] = []
    search_help: Optional[str] = None


# ── Cuisines & Flavor Tags ──────────────────────────────────

class CuisineOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class FlavorTagOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


# ── Restaurants ─────────────────────────────────────────────

class RestaurantCreate(BaseModel):
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    google_maps_url: Optional[str] = None
    website_url: Optional[str] = None
    google_place_id: Optional[str] = None
    google_photo_name: Optional[str] = None

    # Scrape-sourced fields (all optional — populated by the data pipeline loader, not the admin form)
    match_status: Optional[str] = None
    source_restaurant_code: Optional[str] = None
    chain_name: Optional[str] = None
    chain_code: Optional[str] = None
    budget: Optional[int] = None
    foodpanda_rating: Optional[float] = None
    foodpanda_review_number: Optional[int] = None
    raw_cuisines: Optional[list[str]] = None
    logo_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class RestaurantOut(BaseModel):
    id: int
    name: str
    area: Optional[str]
    address: Optional[str]
    phone: Optional[str]
    google_maps_url: Optional[str]
    website_url: Optional[str] = None
    google_place_id: Optional[str] = None
    image_url: Optional[str] = None
    food_types: list[FoodTypeOut] = []
    average_rating: Optional[float] = None
    review_count: int = 0
    # Server-resolved rating to display: khawon's own if it has reviews, else
    # the foodpanda scraped rating as fallback. `display_rating_source` tells
    # the UI which one it is ('khawon' | 'foodpanda' | None).
    display_rating: Optional[float] = None
    display_review_count: int = 0
    display_rating_source: Optional[str] = None

    match_status: Optional[str] = None
    source_restaurant_code: Optional[str] = None
    chain_name: Optional[str] = None
    chain_code: Optional[str] = None
    budget: Optional[int] = None
    foodpanda_rating: Optional[float] = None
    foodpanda_review_number: Optional[int] = None
    raw_cuisines: Optional[list[str]] = None
    logo_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    model_config = {"from_attributes": True}


class RestaurantSummaryOut(BaseModel):
    """Lightweight restaurant shape for embedding inside dish search results."""
    id: int
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    image_url: Optional[str] = None
    google_place_id: Optional[str] = None
    # Resolved restaurant rating for inline cards (khawon-else-foodpanda).
    display_rating: Optional[float] = None
    display_rating_source: Optional[str] = None

    model_config = {"from_attributes": True}


class PlacePhotoOut(BaseModel):
    name: str
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    attribution: Optional[str] = None


class PlaceSearchResult(BaseModel):
    place_id: str
    name: str
    address: Optional[str] = None
    area: Optional[str] = None
    phone: Optional[str] = None
    google_maps_url: Optional[str] = None
    website_url: Optional[str] = None
    photos: list[PlacePhotoOut] = []
    photos_help: Optional[str] = None


# ── Dishes ──────────────────────────────────────────────────

class DishVariationOut(BaseModel):
    label: Optional[str] = None
    price_bdt: Optional[float] = None


class DishCreate(BaseModel):
    restaurant_id: int
    food_type_id: Optional[int] = None
    source_dish_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    price_bdt: Optional[float] = None
    image_url: Optional[str] = None
    is_sold_out: bool = False
    category_raw: Optional[str] = None
    dietary_attributes_raw: Optional[list[str]] = None
    variations: Optional[list[DishVariationOut]] = None
    cuisine_ids: list[int] = []
    flavor_tag_ids: list[int] = []


class DishOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price_bdt: Optional[float] = None
    image_url: Optional[str] = None
    is_sold_out: bool = False
    is_active: bool = True
    category_raw: Optional[str] = None
    variations: Optional[list[DishVariationOut]] = None
    food_type: Optional[FoodTypeOut] = None
    canonical_dish_id: Optional[int] = None
    cuisines: list[CuisineOut] = []
    flavor_tags: list[FlavorTagOut] = []
    restaurant: RestaurantSummaryOut
    average_rating: Optional[float] = None
    review_count: int = 0

    model_config = {"from_attributes": True}


class RestaurantWithDishesOut(RestaurantOut):
    dishes: list[DishOut] = []


# ── Canonical dishes (the unit of cross-restaurant comparison) ──

class CanonicalDishOut(BaseModel):
    id: int
    name: str
    food_type: Optional[FoodTypeOut] = None
    aliases: Optional[list[str]] = None
    image_url: Optional[str] = None

    model_config = {"from_attributes": True}


class CanonicalDishMatch(CanonicalDishOut):
    """A canonical dish surfaced by search, with comparison stats."""
    restaurant_count: int = 0
    dish_count: int = 0
    average_rating: Optional[float] = None
    min_price_bdt: Optional[float] = None
    max_price_bdt: Optional[float] = None


class DishCompareResult(BaseModel):
    """One canonical dish compared across every restaurant serving it."""
    canonical_dish: CanonicalDishOut
    dishes: list[DishOut]
    total: int = 0
    offset: int = 0
    limit: int = 20
    average_rating: Optional[float] = None
    min_price_bdt: Optional[float] = None
    max_price_bdt: Optional[float] = None


# ── Reviews ─────────────────────────────────────────────────

class ReviewCreate(BaseModel):
    dish_id: int
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class ReviewOut(BaseModel):
    id: int
    dish_id: int
    restaurant_id: int          # derived through the dish
    dish_name: Optional[str] = None
    username: str               # from the user account; accounts are required
    rating: int
    comment: Optional[str]
    is_verified: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewListResult(BaseModel):
    """Paginated dish reviews."""
    reviews: list[ReviewOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 20


class RestaurantReviewCreate(BaseModel):
    """Restaurant-level review (overall experience), distinct from dish reviews.
    restaurant_id comes from the path."""
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class RestaurantReviewOut(BaseModel):
    id: int
    restaurant_id: int
    username: str
    rating: int
    comment: Optional[str] = None
    is_verified: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class RestaurantReviewListResult(BaseModel):
    """Paginated restaurant-level reviews."""
    reviews: list[RestaurantReviewOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 20


class RestaurantCatalogueResult(BaseModel):
    """Paginated restaurant browse list."""
    restaurants: list[RestaurantOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 24


# ── Search ───────────────────────────────────────────────────

class FoodDetailResult(BaseModel):
    food_type: FoodTypePopularOut
    restaurants: list[RestaurantOut]


class DishSearchResult(BaseModel):
    """The core 'search a food' response: canonical dishes to compare
    (with stats), plus the flat dish matches."""
    query: str
    canonical_matches: list[CanonicalDishMatch] = []
    total: int = 0
    offset: int = 0
    limit: int = 20
    dishes: list[DishOut] = []
