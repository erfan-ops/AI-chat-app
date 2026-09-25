"""Sign in with Google: verify a Google credential, and provision the account.

The browser gets a signed ID token from Google Identity Services and posts it here.
Nothing in that token is trusted until ``google.oauth2.id_token`` has verified it
against Google's published keys — signature, issuer, audience (this application's
client id) and expiry — so a token minted for another application, an expired one, or
one that was simply hand-written never reaches the user lookup below.

Three rules shape the rest:

- **``sub`` is the identity, never the email.** It is Google's stable identifier for
  an account; an address can be reassigned by a provider, and two Google accounts can
  never share a ``sub``.
- **A Google sign-in never rewrites the profile.** Display name and picture are used
  once, when the account is created, and never again — they belong to the user, who
  can change them in this application's own settings (docs/google-signin-notes.md).
- **An email that already has an account is not silently adopted.** See
  ``identify`` for why.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.email import normalize_email
from app.core.logging import get_logger, structured
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from app.schemas.users import USERNAME_MAX_LENGTH
from app.services.cloudinary_service import CloudinaryService, UploadedImage

logger = get_logger("app.services.google")

# The only USERS.STATUS value a new account is ever created with (there is no check
# constraint; the API defines the set — see docs/database.md). Spelled here rather
# than imported from the auth service, which imports this module.
ACTIVE_STATUS = "ACTIVE"

# Google serves the profile picture square, at a size given in the URL: the claim
# carries `=s96-c`, and this application's avatars are 512x512 masters, so the size is
# rewritten before downloading.
_GOOGLE_PICTURE_SIZE = re.compile(r"=s\d+(-c)?$")
AVATAR_SIZE = 512
# A profile picture, not a photo library: refuse anything implausible before holding
# it in memory, and only ever fetch from a host Google actually serves.
MAX_AVATAR_BYTES = 5 * 1024 * 1024
_AVATAR_HOSTS = ("googleusercontent.com", "ggpht.com")

# How many username candidates to try before falling back to a random one. This is a
# bound on work, not a security control: the lookup is the only cost.
_USERNAME_ATTEMPTS = 20

# How long Google's public keys are kept before being fetched again. google-auth has no
# cache of its own — it fetches the key set on every verification — while Google's own
# response says `cache-control: public, max-age=24760`, so an hour is conservative. The
# cost of being stale is bounded: during a key rotation Google keeps publishing the old
# key for far longer than this, so the worst case is one refused sign-in that succeeds
# a moment later.
KEY_SET_TTL_SECONDS = 3600


class CachedKeySet:
    """Google's public keys, held for an hour.

    google-auth takes a ``request`` callable and uses it to fetch the key set on every
    verification. Passing this one instead means the keys are fetched once an hour
    rather than once a sign-in — which on a slow link is the difference between an
    instant sign-in and a ten-second one, and it stops a burst of sign-ins from becoming
    a burst of requests to Google.
    """

    def __init__(
        self,
        request: Callable[..., Any],
        *,
        ttl_seconds: int = KEY_SET_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._request = request
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[float, Any] | None = None

    @property
    def cached(self) -> bool:
        return self._cached is not None

    def __call__(self, url: str, method: str = "GET", body: Any = None, **kwargs: Any) -> Any:
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        response = self._request(url, method=method, body=body, **kwargs)
        self._cached = (now, response)
        return response


def key_set_of(response: Any) -> dict[str, str]:
    """The ``kid -> PEM`` mapping a certificate response carries (used by tests)."""
    return json.loads(response.data.decode("utf-8"))


Verifier = Callable[[str, str], dict[str, Any]]


class AvatarDownloader(Protocol):
    """Just enough of ``httpx.AsyncClient`` to fetch one image, and to fake it."""

    async def get(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class GoogleIdentity:
    """The verified claims this application uses, and nothing else."""

    sub: str
    email: str
    name: str | None
    picture: str | None


class GoogleAuthService:
    """Verifies Google credentials and resolves them to an application account."""

    def __init__(
        self,
        settings: Settings,
        *,
        verifier: Verifier | None = None,
        downloader: AvatarDownloader | None = None,
        key_set: CachedKeySet | None = None,
    ) -> None:
        self._settings = settings
        # A supplied verifier replaces Google's library entirely (tests do this); the
        # key set is only built when the real verification path is used.
        self._keys = key_set
        self._verify = verifier or self._verify_with_google
        self._downloader = downloader

    def _verify_with_google(self, credential: str, client_id: str) -> dict[str, Any]:
        """Google's own verification, against Google's published keys.

        The library checks signature, issuer, audience and expiry, so nothing in the
        claims is believed before it holds up. The key set it needs comes through
        ``CachedKeySet``; a ``TransportError`` from that fetch means the keys could not
        be had at all — a transport problem, not a bad credential.
        """
        self._keys = self._keys or CachedKeySet(Request())
        return id_token.verify_oauth2_token(credential, self._keys, client_id)

    # -- verification --------------------------------------------------------------

    def verify(self, credential: str) -> GoogleIdentity:
        """The verified identity behind a credential, or a 400/503.

        Every rejection is the same opaque answer: which of "bad signature", "wrong
        audience" or "expired" it was is not something a caller should be able to
        probe for.
        """
        client_id = self._settings.google_client_id
        if not client_id:
            raise ServiceUnavailableError("Google sign-in is not configured")
        try:
            claims = self._verify(credential, client_id)
        except TransportError as exc:
            # Google's key set could not be fetched, so nothing could be checked. The
            # credential may be perfectly good: this is our problem, not the caller's.
            structured(
                logger, logging.ERROR, "google verification unavailable", error=type(exc).__name__
            )
            raise ServiceUnavailableError("Google sign-in is unavailable right now") from exc
        except (GoogleAuthError, ValueError) as exc:
            # Everything else means the credential itself did not hold up: a bad
            # signature, the wrong audience, an expired token, or a structure that is
            # not a JWT. Note that google-auth's MalformedError — raised when no key
            # verifies the signature — is a ValueError subclass, not a transport
            # failure, so it must never be reported as an outage.
            structured(logger, logging.WARNING, "google credential rejected", reason=str(exc)[:120])
            raise BadRequestError("That Google sign-in could not be verified") from exc

        sub = claims.get("sub")
        email = claims.get("email")
        if not isinstance(sub, str) or not sub:
            raise BadRequestError("That Google account has no usable identifier")
        if claims.get("email_verified") is not True or not isinstance(email, str):
            # Every address this application stores is treated as verified — it is what
            # OTP delivery and the uniqueness rules assume — so an unverified one is
            # refused rather than written.
            raise BadRequestError("That Google account has no verified email address")
        try:
            canonical_email = normalize_email(email)
        except BadRequestError as exc:
            structured(logger, logging.WARNING, "google email rejected", reason=str(exc)[:120])
            raise BadRequestError("That Google account has no usable email address") from exc

        name = claims.get("name")
        picture = claims.get("picture")
        return GoogleIdentity(
            sub=sub,
            email=canonical_email,
            name=name.strip()[:100] if isinstance(name, str) and name.strip() else None,
            picture=picture if isinstance(picture, str) and picture else None,
        )

    # -- account resolution --------------------------------------------------------

    async def identify(
        self,
        db: AsyncSession,
        *,
        credential: str,
        uploads: CloudinaryService,
    ) -> tuple[User, bool]:
        """The account behind a credential, creating it on first sight.

        Returns ``(user, created)``. An existing account comes back untouched: the
        only field a later sign-in changes is ``last_login_at``, written by
        ``AuthService._issue_session``.
        """
        identity = self.verify(credential)
        repo = UserRepository(db)
        existing = await repo.get_by_google_sub(identity.sub)
        if existing is not None:
            return existing, False

        holder = await repo.get_by_email(identity.email)
        if holder is not None:
            # Deliberately *not* adopted, even though Google verified that address:
            # that would make a Google sign-in a way into an account created with a
            # password, without the password ever being presented. See
            # docs/google-signin-notes.md for the decision and the alternative.
            structured(logger, logging.WARNING, "google sign-in refused: email already in use")
            raise ConflictError(
                "That email address already has an account. Sign in with your username "
                "and password instead."
            )

        return await self._create_user(db, identity=identity, uploads=uploads), True

    async def _create_user(
        self, db: AsyncSession, *, identity: GoogleIdentity, uploads: CloudinaryService
    ) -> User:
        """Create the account, copying the Google picture into this account's storage."""
        repo = UserRepository(db)
        now = utcnow()
        user = User(
            username=await self._available_username(repo, identity),
            # No password: the column is nullable, password login is refused while it
            # is NULL (there is nothing to compare against, so nothing to guess), and
            # the user can set one later from settings.
            password_hash=None,
            google_sub=identity.sub,
            email=identity.email,
            display_name=identity.name,
            status=ACTIVE_STATUS,
            created_at=now,
            updated_at=now,
        )

        avatar = await self._store_avatar(identity, uploads=uploads)
        if avatar is not None:
            user.avatar_url = avatar.url
        try:
            await repo.add(user)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            if avatar is not None:
                # Nothing will ever point at this picture now, so it is removed rather
                # than left orphaned in the Cloudinary account.
                await uploads.delete_asset(avatar.public_id)
            # UK_USERS_GOOGLE_SUB / UK_USERS_USERNAME / UK_USERS_EMAIL: another request
            # took the identity, the name or the address between the checks above and
            # here. The constraint is the real guarantee; the lookups are the shortcut.
            raise ConflictError("Could not create an account for that Google account") from exc
        await db.refresh(user)
        structured(
            logger,
            logging.INFO,
            "account created from google",
            user_id=user.id,
            has_avatar=avatar is not None,
        )
        return user

    async def _store_avatar(
        self, identity: GoogleIdentity, *, uploads: CloudinaryService
    ) -> UploadedImage | None:
        """Download Google's picture and store it under this application's Cloudinary.

        Best effort by design: an account without a picture is a perfectly good
        account — the UI falls back to the initial — and it must not be the reason a
        sign-in fails. A failure is logged by reason, never by URL.
        """
        if not identity.picture:
            return None
        try:
            content, content_type = await self._download_picture(identity.picture)
            return await uploads.upload_avatar(content, content_type=content_type, kind="user")
        except (httpx.HTTPError, ServiceUnavailableError, BadRequestError) as exc:
            structured(
                logger, logging.WARNING, "google picture not stored", error=type(exc).__name__
            )
            return None

    async def _download_picture(self, url: str) -> tuple[bytes, str]:
        """Fetch the picture, at avatar size, from a host Google actually serves."""
        target = _avatar_url(url)
        if not any(host in target for host in _AVATAR_HOSTS):
            raise BadRequestError("Google returned an unexpected picture address")

        if self._downloader is not None:
            response = await self._downloader.get(target, timeout=10.0, follow_redirects=True)
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(target, timeout=10.0, follow_redirects=True)
        response.raise_for_status()

        content_type = str(response.headers.get("content-type", ""))
        if not content_type.startswith("image/"):
            raise BadRequestError("Google returned something that is not an image")
        content: bytes = response.content
        if not content:
            raise BadRequestError("Google returned an empty picture")
        if len(content) > MAX_AVATAR_BYTES:
            raise BadRequestError("That Google picture is too large")
        return content, content_type

    async def _available_username(self, repo: UserRepository, identity: GoogleIdentity) -> str:
        """A free username derived from the Google account.

        Accounts are identified by username here, so one must exist even though this
        user never chose it. The email's local part is the most recognisable source
        (``ali.reza@gmail.com`` → ``ali.reza``); it is stripped to the characters the
        application allows, and a numeric suffix is added until one is free. Nothing
        about it is secret — it is not a credential, and it only ever signs in a user
        who has set a password.
        """
        base = _username_base(identity)
        for attempt in range(_USERNAME_ATTEMPTS):
            candidate = base if attempt == 0 else _suffixed(base, attempt)
            if await repo.get_by_username(candidate) is None:
                return candidate
        # Every recognisable name is taken: fall back to something short and random,
        # rather than refuse a sign-in over a display detail.
        return _suffixed(base[:20], secrets.randbelow(10**6))


def _avatar_url(url: str) -> str:
    """The URL to fetch, asking for a 512px square where that is expressible."""
    if _GOOGLE_PICTURE_SIZE.search(url):
        return _GOOGLE_PICTURE_SIZE.sub(f"=s{AVATAR_SIZE}-c", url)
    tail = url.rsplit("/", 1)[-1]
    # No size parameter and none in the way: the CDN honours one appended to any path.
    # A URL that already carries unknown parameters is left exactly as it is rather
    # than guessed at.
    return url if "=" in tail else f"{url}=s{AVATAR_SIZE}-c"


def _username_base(identity: GoogleIdentity) -> str:
    """The first candidate username: from the address, then the Google name."""
    local = identity.email.split("@", 1)[0]
    for source in (local, (identity.name or "").replace(" ", ".")):
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", source).strip("._-")
        if len(cleaned) >= 3:
            return cleaned[:USERNAME_MAX_LENGTH]
    # Nothing usable in either — a two-character local part, a name in a script this
    # application's rules reject — so start from a plain placeholder.
    return "user"


def _suffixed(base: str, attempt: int) -> str:
    """``ali`` + 3 → ``ali3``, trimmed so the result still fits the column."""
    suffix = str(attempt + 1)
    return f"{base[: USERNAME_MAX_LENGTH - len(suffix)]}{suffix}"
