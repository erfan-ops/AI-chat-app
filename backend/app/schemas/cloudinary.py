"""Cloudinary upload contracts.

Only browser-safe values ever cross this boundary: the API key is public by design
(it identifies the account on an upload), while the API secret stays server-side and
is never part of any response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# What the upload is for. The *kind* is named by the client; the folder it maps to is
# decided server-side (app/services/cloudinary_service.py), so a request can never
# choose where it writes.
UploadKind = Literal["character", "user"]


class UploadSignatureRequest(BaseModel):
    """Body for POST /cloudinary/signature. Optional: the default is a character
    avatar, which is what this endpoint signed before user pictures existed."""

    kind: UploadKind = "character"


class UploadSignatureRead(BaseModel):
    """Response for POST /cloudinary/signature.

    ``folder`` and ``timestamp`` are echoed so the browser can send exactly the
    parameters that were signed.
    """

    model_config = ConfigDict(from_attributes=True)

    signature: str
    timestamp: int
    api_key: str
    cloud_name: str
    folder: str = Field(description="Cloudinary folder the signed upload must target")
