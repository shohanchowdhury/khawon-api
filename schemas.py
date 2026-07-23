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


# ── Brand dish cards (a chain's branches collapsed into one card) ──

class BrandOut(BaseModel):
    id: int                          # chain_id -- use for POST bodies, never in a URL
    slug: str                        # chain_code -- THE url key: /restaurants/{slug}
    name: str

    model_config = {"from_attributes": True}


class BrandBranchOut(BaseModel):
    """One branch serving a brand dish."""
    restaurant_id: int
    restaurant_name: str
    area: Optional[str] = None
    product_id: int          # the branch's own dish row; review it via POST /reviews
    price_bdt: float
    is_sold_out: bool = False
    average_rating: Optional[float] = None
    review_count: int = 0


class BrandDishOut(BaseModel):
    """A dish as one brand serves it, collapsing that brand's branches into a
    single card. A standalone restaurant is a brand of one, so its card is
    identical in shape (branch_count == brand_branch_total == 1)."""
    brand: BrandOut
    food_type_id: Optional[int] = None
    slug: str                       # slugified normalized_name; with brand.id +
                                    # food_type_id this is the card's natural key
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    category_raw: Optional[str] = None
    food_type: Optional[FoodTypeOut] = None
    cuisines: list[CuisineOut] = []
    flavor_tags: list[FlavorTagOut] = []
    canonical_dish_id: Optional[int] = None
    # Always present. When price_varies is False, min == max and the UI shows
    # one number -- one rule, no branching.
    price_min_bdt: float
    price_max_bdt: float
    price_varies: bool = False
    branch_count: int               # branches of this brand serving the dish
    brand_branch_total: int         # branches this brand has overall
    is_sold_out_everywhere: bool = False
    # Pooled across the brand's branches.
    average_rating: Optional[float] = None
    review_count: int = 0


class BrandDishDetailOut(BrandDishOut):
    """Brand dish + per-branch breakdown (pooled headline, branch detail)."""
    branches: list[BrandBranchOut] = []


class BranchResolveOut(BaseModel):
    """Resolve a branch (location) row to its brand. Used when an old branch URL
    like /restaurant/218 needs to redirect to /restaurant/{chain_slug}, and by
    the branch admin edit form."""
    id: int
    chain_id: int
    chain_slug: str                  # redirect target: /restaurants/{chain_slug}
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    google_place_id: Optional[str] = None
    image_url: Optional[str] = None


class BrandDetailOut(BaseModel):
    """A brand and its branches. The branch list is what the future
    map/directions view renders."""
    id: int                          # chain_id -- use for POST bodies, never in a URL
    slug: str                        # chain_code -- this page's url key
    name: str
    branch_count: int
    branches: list[RestaurantSummaryOut] = []
    display_rating: Optional[float] = None
    # The count BEHIND display_rating (same source). Without it the page shows
    # a rating next to "0 reviews", which reads broken.
    display_review_count: int = 0
    display_rating_source: Optional[str] = None


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
    """One canonical dish compared across every BRAND serving it. A chain is
    one row with branch_count > 1, not one row per branch."""
    canonical_dish: CanonicalDishOut
    dishes: list[BrandDishOut]
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
    # Edit history, surfaced as a trust signal. Defaults mean "never edited",
    # so existing clients that ignore these fields keep working unchanged.
    is_edited: bool = False
    edit_count: int = 0
    original_rating: Optional[int] = None   # what it said before the first edit
    last_edited_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReviewListResult(BaseModel):
    """Paginated dish reviews."""
    reviews: list[ReviewOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 20


class RestaurantReviewCreate(BaseModel):
    """Restaurant-level review (overall experience), distinct from dish reviews.
    The brand (chain) comes from the path; branch_id says WHICH location the
    reviewer visited -- reviews attach to a location, display pools per brand."""
    branch_id: int
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class RestaurantReviewOut(BaseModel):
    id: int
    restaurant_id: int              # the branch (location) reviewed
    branch_name: Optional[str] = None
    branch_area: Optional[str] = None
    username: str
    rating: int
    comment: Optional[str] = None
    is_verified: bool = False
    created_at: datetime
    # Edit history, surfaced as a trust signal. Defaults mean "never edited",
    # so existing clients that ignore these fields keep working unchanged.
    is_edited: bool = False
    edit_count: int = 0
    original_rating: Optional[int] = None   # what it said before the first edit
    last_edited_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RestaurantReviewListResult(BaseModel):
    """Paginated restaurant-level reviews."""
    reviews: list[RestaurantReviewOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 20


class BrandListOut(BaseModel):
    """One brand in the restaurant browse list. 'Restaurant' in the API means
    brand: Bella Italia appears ONCE with branch_count=3, not per branch. A
    standalone restaurant is a brand of one (branch_count=1)."""
    id: int                          # chain_id -- use for POST bodies, never in a URL
    slug: str                        # chain_code -- link here: /restaurants/{slug}
    name: str
    branch_count: int
    areas: list[str] = []            # distinct branch areas, e.g. ["Dhanmondi", "Gulshan"]
    image_url: Optional[str] = None
    food_types: list[FoodTypeOut] = []
    cuisines: list[str] = []
    display_rating: Optional[float] = None
    display_rating_source: Optional[str] = None
    display_review_count: int = 0


class RestaurantCatalogueResult(BaseModel):
    """Paginated brand browse list -- the response of GET /restaurants."""
    restaurants: list[BrandListOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 24


class BranchListResult(BaseModel):
    """Paginated location list (admin/manage screens)."""
    branches: list[RestaurantSummaryOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 50


# ── Search ───────────────────────────────────────────────────

class FoodDetailResult(BaseModel):
    food_type: FoodTypePopularOut
    restaurants: list[BrandListOut]   # brands serving this food type, one card per brand


class DishSearchResult(BaseModel):
    """The core 'search a food' response: canonical dishes to compare
    (with stats), plus paginated brand dish cards. A chain appears ONCE:
    its branches are collapsed into a single card."""
    query: str
    canonical_matches: list[CanonicalDishMatch] = []
    total: int = 0        # number of brand cards, not product rows
    offset: int = 0
    limit: int = 20
    dishes: list[BrandDishOut] = []
