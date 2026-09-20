"""Signed direct-to-Cloudinary uploads.

The API never receives image bytes: it only signs a small, fixed parameter set and
hands it to the browser, which posts the file to Cloudinary itself. The API secret
exists only here, server-side.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudinary.utils import api_sign_request

from app.core.config import Settings
from app.core.time import unix_timestamp
from app.exceptions import ServiceUnavailableError

# Every avatar is stored under this folder so the account stays tidy.
UPLOAD_FOLDER = "characters"


@dataclass(frozen=True)
class UploadSignature:
    """What the browser needs to perform the signed upload.

    ``folder`` is signed, so the client must send it back byte-identical or
    Cloudinary rejects the request with "Invalid Signature".
    """

    signature: str
    timestamp: int
    api_key: str
    cloud_name: str
    folder: str


class CloudinaryService:
    """Mints Cloudinary upload signatures. Stateless apart from its settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def sign_upload(self) -> UploadSignature:
        """Sign a minimal upload payload (``folder`` + ``timestamp``)."""
        settings = self._settings
        if not (
            settings.cloudinary_cloud_name
            and settings.cloudinary_api_key
            and settings.cloudinary_api_secret
        ):
            raise ServiceUnavailableError("Cloudinary is not configured")

        timestamp = unix_timestamp()
        # Only these two keys are signed; everything else the browser sends
        # (file/api_key/cloud_name/resource_type) is excluded by Cloudinary's rules.
        # api_sign_request needs no cloudinary.config(): the secret is passed in.
        signature = api_sign_request(
            {"folder": UPLOAD_FOLDER, "timestamp": timestamp},
            settings.cloudinary_api_secret,
        )
        return UploadSignature(
            signature=signature,
            timestamp=timestamp,
            api_key=settings.cloudinary_api_key,
            cloud_name=settings.cloudinary_cloud_name,
            folder=UPLOAD_FOLDER,
        )
