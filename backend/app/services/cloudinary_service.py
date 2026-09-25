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
from app.exceptions import BadRequestError, ServiceUnavailableError

# Where each kind of avatar is stored. Both hold the same thing — a 512x512 master
# uploaded by the browser — but keeping them apart lets each be found, kept or purged
# on its own. The client names a *kind*, never a path: the folder is chosen here, so
# no client-supplied value is ever signed.
UPLOAD_FOLDERS: dict[str, str] = {"character": "characters", "user": "users"}
DEFAULT_UPLOAD_KIND = "character"


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

    def sign_upload(self, kind: str = DEFAULT_UPLOAD_KIND) -> UploadSignature:
        """Sign a minimal upload payload (``folder`` + ``timestamp``).

        ``kind`` picks the folder from ``UPLOAD_FOLDERS``; an unknown one is rejected
        rather than signed, so the set of writable folders lives in one place.
        """
        settings = self._settings
        if not (
            settings.cloudinary_cloud_name
            and settings.cloudinary_api_key
            and settings.cloudinary_api_secret
        ):
            raise ServiceUnavailableError("Cloudinary is not configured")

        folder = UPLOAD_FOLDERS.get(kind)
        if folder is None:
            raise BadRequestError("Unknown upload kind")

        timestamp = unix_timestamp()
        # Only these two keys are signed; everything else the browser sends
        # (file/api_key/cloud_name/resource_type) is excluded by Cloudinary's rules.
        # api_sign_request needs no cloudinary.config(): the secret is passed in.
        signature = api_sign_request(
            {"folder": folder, "timestamp": timestamp},
            settings.cloudinary_api_secret,
        )
        return UploadSignature(
            signature=signature,
            timestamp=timestamp,
            api_key=settings.cloudinary_api_key,
            cloud_name=settings.cloudinary_cloud_name,
            folder=folder,
        )
