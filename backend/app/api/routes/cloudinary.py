"""Cloudinary upload signing.

Avatars are uploaded by the browser straight to Cloudinary; this route only mints
the signature that authorizes the upload, so neither the image bytes nor the API
secret pass through the API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_cloudinary_service, get_current_user
from app.core.config import Settings, get_settings
from app.db.models.user import User
from app.schemas.cloudinary import UploadSignatureRead, UploadSignatureRequest

router = APIRouter(tags=["cloudinary"])


@router.post(
    "/cloudinary/signature",
    response_model=UploadSignatureRead,
    summary="Sign a direct-to-Cloudinary upload",
    description=(
        "Returns the signature and parameters the browser needs to upload an image "
        "straight to Cloudinary: a `character` avatar (the default) or the caller's "
        "own `user` picture. The folder is chosen from `kind` server-side and comes "
        "back in the response — the signed request must send it back unchanged. The "
        "image never passes through this API, and the API secret is never returned. "
        "Answers 503 when the CLOUDINARY_* settings are not configured."
    ),
)
async def create_upload_signature(
    _user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    body: UploadSignatureRequest | None = None,
) -> UploadSignatureRead:
    signature = get_cloudinary_service(settings).sign_upload(
        body.kind if body is not None else "character"
    )
    return UploadSignatureRead.model_validate(signature)
