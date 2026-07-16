"""Branch (location) endpoints -- admin/contribute operations on ONE location.

The public browse surface is brand-level (routers/restaurants.py, where
{restaurant_id} = chain_id). These endpoints manage the underlying branch
rows: the contribute form adds a location, edits its details/photo, and can
inspect a single branch's raw menu.

Creating a branch creates a brand of one (every restaurant has a chain_id --
that invariant is what lets the rest of the API GROUP BY brand with no
special-casing). The pipeline's bootstrap_chains regroups user-added
locations into existing brands on the next run if their names match.
"""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from dish_detail import get_dishes_for_restaurant
import models
from places import fetch_place_photo_bytes
import schemas
from storage import upload_image_bytes

router = APIRouter(prefix="/branches", tags=["Branches"])


async def _resolve_branch_image(
    google_photo_name: str | None,
    image: UploadFile | None,
) -> str | None:
    if image and image.filename:
        data = await image.read()
        return upload_image_bytes(data, folder="khawon/restaurants")
    if google_photo_name and google_photo_name.strip():
        photo_bytes, _ = fetch_place_photo_bytes(google_photo_name.strip(), max_width=1600)
        return upload_image_bytes(photo_bytes, folder="khawon/restaurants")
    return None


def _get_branch(db: Session, branch_id: int) -> models.Restaurant:
    b = db.query(models.Restaurant).filter(models.Restaurant.id == branch_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Branch not found")
    return b


def _branch_out(b: models.Restaurant) -> schemas.RestaurantSummaryOut:
    return schemas.RestaurantSummaryOut(
        id=b.id, name=b.name, area=b.area, address=b.address,
        image_url=b.hero_image_url, google_place_id=b.google_place_id,
    )


@router.get("/", response_model=list[schemas.RestaurantSummaryOut])
def list_branches(db: Session = Depends(get_db)):
    """All active locations for admin/manage screens."""
    branches = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.is_active.is_(True))
        .order_by(models.Restaurant.name)
        .all()
    )
    return [_branch_out(b) for b in branches]


@router.post("/", response_model=schemas.RestaurantSummaryOut, status_code=201)
async def create_branch(
    name: str = Form(...),
    area: str | None = Form(None),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    google_place_id: str | None = Form(None),
    google_photo_name: str | None = Form(None),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new location (requires sign-in). Becomes a brand of one; the
    pipeline regroups it into an existing brand later if the name matches."""
    image_url = await _resolve_branch_image(google_photo_name, image)

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "restaurant"
    code = f"user-{slug}-{uuid.uuid4().hex[:8]}"

    chain = models.RestaurantChain(chain_code=code, name=name)
    db.add(chain)
    db.flush()

    branch = models.Restaurant(
        source_restaurant_code=code,
        name=name,
        area=area or None,
        address=address or None,
        phone=phone or None,
        google_place_id=google_place_id or None,
        hero_image_url=image_url,
        chain_id=chain.id,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return _branch_out(branch)


@router.put("/{branch_id}", response_model=schemas.RestaurantSummaryOut)
async def update_branch(
    branch_id: int,
    name: str = Form(...),
    area: str | None = Form(None),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    google_place_id: str | None = Form(None),
    google_photo_name: str | None = Form(None),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    b = _get_branch(db, branch_id)
    b.name = name
    b.area = area or None
    b.address = address or None
    b.phone = phone or None
    b.google_place_id = google_place_id or None

    new_image_url = await _resolve_branch_image(google_photo_name, image)
    if new_image_url:
        b.hero_image_url = new_image_url

    db.commit()
    db.refresh(b)
    return _branch_out(b)


@router.put("/{branch_id}/photo", response_model=schemas.RestaurantSummaryOut)
async def update_branch_photo(
    branch_id: int,
    google_photo_name: Annotated[str | None, Form()] = None,
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    b = _get_branch(db, branch_id)
    new_image_url = await _resolve_branch_image(google_photo_name, image)
    if not new_image_url:
        raise HTTPException(
            status_code=400,
            detail="Provide a Google photo selection or upload an image file.",
        )
    b.hero_image_url = new_image_url
    db.commit()
    db.refresh(b)
    return _branch_out(b)


@router.delete("/{branch_id}", status_code=204)
def delete_branch(
    branch_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    b = _get_branch(db, branch_id)
    db.delete(b)
    db.commit()


@router.get("/{branch_id}", response_model=schemas.BranchResolveOut)
def get_branch(branch_id: int, db: Session = Depends(get_db)):
    """Resolve a location row to its brand (chain_id). Public read for URL
    redirects and branch admin edit forms."""
    b = _get_branch(db, branch_id)
    return schemas.BranchResolveOut(
        id=b.id,
        chain_id=b.chain_id,
        name=b.name,
        area=b.area,
        address=b.address,
        phone=b.phone,
        google_place_id=b.google_place_id,
        image_url=b.hero_image_url,
    )


@router.get("/{branch_id}/dishes", response_model=list[schemas.DishOut])
def get_branch_dishes(branch_id: int, db: Session = Depends(get_db)):
    """ONE location's raw menu (admin/inspection). The public menu is the
    brand-level merged one at /restaurants/{id}/menu."""
    _get_branch(db, branch_id)
    return get_dishes_for_restaurant(db, branch_id)
