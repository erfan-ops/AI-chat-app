"""Cloudinary image hosting.

Normally the API never receives image bytes: it signs a small, fixed parameter set
and the browser posts the file to Cloudinary itself. One flow cannot work that way —
a Google sign-in has to copy the profile picture *it* was given into this account —
so there is exactly one server-side upload here, using the same credentials, the same
folder convention and the same 512x512 master as the browser path. The API secret
exists only here, server-side.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass

import cloudinary
import cloudinary.uploader
from cloudinary.utils import api_sign_request

from app.core.config import Settings
from app.core.logging import get_logger, structured
from app.core.time import unix_timestamp
from app.exceptions import BadRequestError, ServiceUnavailableError

logger = get_logger("app.services.cloudinary")

# Where each kind of avatar is stored. Both hold the same thing — a 512x512 master
# uploaded by the browser — but keeping them apart lets each be found, kept or purged
# on its own. The client names a *kind*, never a path: the folder is chosen here, so
# no client-supplied value is ever signed.
UPLOAD_FOLDERS: dict[str, str] = {"character": "characters", "user": "users"}
DEFAULT_UPLOAD_KIND = "character"


@dataclass(frozen=True)
class UploadedImage:
    """One stored image: where to serve it from, and what to delete it by."""

    url: str
    public_id: str


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
    """Image hosting: upload signatures for clients, and uploads for the API itself."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- the API's own uploads -----------------------------------------------------

    def _configure_sdk(self) -> None:
        """Point the SDK's process-wide config at this application's credentials.

        The SDK reads ``cloudinary.config()`` globals, which it also populates from
        ``CLOUDINARY_URL`` at import. Assigning them from ``Settings`` keeps the
        configuration in one place — the same thing ``EmailService`` does with
        ``resend.api_key`` — rather than depending on the environment.
        """
        settings = self._settings
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )

    def _require_configured(self) -> None:
        settings = self._settings
        if not (
            settings.cloudinary_cloud_name
            and settings.cloudinary_api_key
            and settings.cloudinary_api_secret
        ):
            raise ServiceUnavailableError("Cloudinary is not configured")

    async def upload_avatar(
        self, content: bytes, *, content_type: str = "image/jpeg", kind: str = "user"
    ) -> UploadedImage:
        """Upload image bytes the API fetched itself, and describe what was stored.

        The SDK is blocking, so the call runs in a worker thread — the same treatment
        the NTP client gets (``TimeService.synchronize``). An unknown ``kind`` is
        refused rather than turned into a folder, so the writable set stays in
        ``UPLOAD_FOLDERS``. The returned ``public_id`` is what ``delete_asset`` needs
        if the caller has to undo the upload.
        """
        self._require_configured()
        folder = UPLOAD_FOLDERS.get(kind)
        if folder is None:
            raise BadRequestError("Unknown upload kind")
        self._configure_sdk()
        # A data URI rather than bare bytes: it is how the image's own format travels
        # with it, so Cloudinary stores what it was given (a JPEG stays a JPEG) instead
        # of having to guess from the first bytes.
        payload = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
        response = await asyncio.to_thread(
            cloudinary.uploader.upload,
            payload,
            folder=folder,
            resource_type="image",
            # Google hands out a square crop at the size we ask for, so nothing is
            # transformed on the way in: what is stored is the 512x512 master, and
            # delivery does the resizing (utils/cloudinary.ts in the frontend).
        )
        return UploadedImage(url=str(response["secure_url"]), public_id=str(response["public_id"]))

    async def delete_asset(self, public_id: str) -> bool:
        """Remove an upload, best effort. Used to undo an avatar nothing points at.

        A failure here is logged and reported, never raised: the caller is already on
        an error path, and an orphaned image must not turn one failure into two.
        """
        try:
            self._require_configured()
            self._configure_sdk()
            result = await asyncio.to_thread(
                cloudinary.uploader.destroy, public_id, resource_type="image"
            )
        except Exception as exc:
            structured(
                logger,
                logging.WARNING,
                "could not remove orphaned upload",
                public_id=public_id,
                error=type(exc).__name__,
            )
            return False
        return str(result.get("result")) == "ok"

    # -- signatures for the browser ------------------------------------------------

    def sign_upload(self, kind: str = DEFAULT_UPLOAD_KIND) -> UploadSignature:
        """Sign a minimal upload payload (``folder`` + ``timestamp``).

        ``kind`` picks the folder from ``UPLOAD_FOLDERS``; an unknown one is rejected
        rather than signed, so the set of writable folders lives in one place.
        """
        self._require_configured()
        folder = UPLOAD_FOLDERS.get(kind)
        if folder is None:
            raise BadRequestError("Unknown upload kind")

        settings = self._settings

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
