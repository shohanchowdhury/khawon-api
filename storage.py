import os

import cloudinary
import cloudinary.uploader
from fastapi import HTTPException, UploadFile

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 5 * 1024 * 1024


def _configure_cloudinary() -> None:
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    if not all([cloud_name, api_key, api_secret]):
        raise HTTPException(
            status_code=503,
            detail="Image upload is not configured. Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET.",
        )
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


async def upload_food_image(file: UploadFile) -> str:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Image must be JPEG, PNG, or WebP.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Image file is empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be 5 MB or smaller.")

    _configure_cloudinary()
    try:
        result = cloudinary.uploader.upload(
            data,
            folder="khawon/food-types",
            resource_type="image",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image upload failed: {exc}") from exc

    return result["secure_url"]
