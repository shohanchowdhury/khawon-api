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
    description: Optional[str]
    image_url: Optional[str] = None
    taste_tags: Optional[list[str]] = None

    model_config = {"from_attributes": True}

class FoodTypePopularOut(FoodTypeOut):
    restaurant_count: int = 0
    review_count: int = 0
    average_rating: Optional[float] = None


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
    food_type_ids: list[int] = []   # which food types this restaurant serves

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


# ── Products (dishes) ───────────────────────────────────────

class ProductVariationOut(BaseModel):
    label: Optional[str] = None
    price_bdt: Optional[float] = None


class ProductCreate(BaseModel):
    restaurant_id: int
    food_type_id: Optional[int] = None
    source_product_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    price_bdt: Optional[float] = None
    image_url: Optional[str] = None
    is_sold_out: bool = False
    category_raw: Optional[str] = None
    dietary_attributes_raw: Optional[list[str]] = None
    variations: Optional[list[ProductVariationOut]] = None
    cuisine_ids: list[int] = []
    flavor_tag_ids: list[int] = []


class ProductOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price_bdt: Optional[float] = None
    image_url: Optional[str] = None
    is_sold_out: bool = False
    category_raw: Optional[str] = None
    variations: Optional[list[ProductVariationOut]] = None
    food_type: Optional[FoodTypeOut] = None
    cuisines: list[CuisineOut] = []
    flavor_tags: list[FlavorTagOut] = []
    restaurant: RestaurantSummaryOut
    average_rating: Optional[float] = None
    review_count: int = 0

    model_config = {"from_attributes": True}


class RestaurantWithProductsOut(RestaurantOut):
    products: list[ProductOut] = []


# ── Reviews ─────────────────────────────────────────────────

class ReviewCreate(BaseModel):
    restaurant_id: int
    food_type_id: int
    product_id: Optional[int] = None
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class ReviewOut(BaseModel):
    id: int
    restaurant_id: int
    food_type_id: int
    product_id: Optional[int] = None
    reviewer_name: Optional[str]
    rating: int
    comment: Optional[str]
    source: str = "user"
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Search ───────────────────────────────────────────────────

class SearchResult(BaseModel):
    food_type: FoodTypeOut
    restaurants: list[RestaurantOut]


class FoodDetailResult(BaseModel):
    food_type: FoodTypePopularOut
    restaurants: list[RestaurantOut]


class DishSearchResult(BaseModel):
    """The core 'search a dish, compare across restaurants' response shape."""
    query: str
    results: list[ProductOut]
