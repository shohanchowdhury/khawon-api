import os

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile

load_dotenv()

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 5 * 1024 * 1024


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


def _configure_cloudinary() -> None:
    cloudinary_url = _env("CLOUDINARY_URL")
    if cloudinary_url:
        cloudinary.config(cloudinary_url=cloudinary_url, secure=True)
        return

    cloud_name = _env("CLOUDINARY_CLOUD_NAME")
    api_key = _env("CLOUDINARY_API_KEY")
    api_secret = _env("CLOUDINARY_API_SECRET")
    missing = [
        name
        for name, value in (
            ("CLOUDINARY_CLOUD_NAME", cloud_name),
            ("CLOUDINARY_API_KEY", api_key),
            ("CLOUDINARY_API_SECRET", api_secret),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Image upload is not configured. Missing: {', '.join(missing)}",
        )
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


def upload_image_bytes(data: bytes, folder: str) -> str:
    if not data:
        raise HTTPException(status_code=400, detail="Image file is empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be 5 MB or smaller.")

    _configure_cloudinary()
    try:
        result = cloudinary.uploader.upload(
            data,
            folder=folder,
            resource_type="image",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image upload failed: {exc}") from exc

    return result["secure_url"]


async def upload_food_image(file: UploadFile) -> str:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Image must be JPEG, PNG, or WebP.",
        )

    data = await file.read()
    return upload_image_bytes(data, folder="khawon/food-types")
