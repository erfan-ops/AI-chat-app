"""Sign in with Google: verification, account creation, and what never changes.

Google and Cloudinary are both mocked — a fake verifier stands in for
``id_token.verify_oauth2_token`` and a recording fake for the upload — so no test
touches Google's key set, a real credential, or the network.

Two properties are worth stating up front, because they are the ones a future change
is most likely to break:

- the *verified* claims are the only input to account creation, and
- a later sign-in changes nothing on an existing account except when it was last used.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_cloudinary_service, get_google_auth_service, get_settings
from app.core.config import Settings
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.main import app
from app.schemas.users import UserRead
from app.services.cloudinary_service import UploadedImage
from app.services.google_auth_service import GoogleAuthService
from tests.conftest import (
    TEST_PASSWORD,
    TEST_SETTINGS,
    FakeClock,
    RecordingSmsService,
    auth_user,
    login_user,
)

CLIENT_ID = "366133901630-c6jfg3hiirfqj4p57n2i860hs9jcl8qf.apps.googleusercontent.com"
UPLOADED = "https://res.cloudinary.com/demo/image/upload/v1/users/from-google.webp"
GOOGLE_PICTURE = "https://lh3.googleusercontent.com/a/ACg8ocExample=s96-c"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"pretend-this-is-an-image"

# Every pin TEST_SETTINGS carries, plus a configured Google client: a developer's .env
# must not be able to change how this suite behaves.
CONFIGURED = TEST_SETTINGS.model_copy(update={"google_client_id": CLIENT_ID})
UNCONFIGURED = TEST_SETTINGS.model_copy(update={"google_client_id": ""})


def default_claims(**overrides: Any) -> dict[str, Any]:
    """The claims Google sends for a normal account, as `verify_oauth2_token` returns
    them — already verified by the time this application sees them."""
    claims: dict[str, Any] = {
        "sub": "110169484474386276334",
        "email": "Ali.Reza@gmail.com",
        "email_verified": True,
        "name": "Ali Reza",
        "picture": GOOGLE_PICTURE,
    }
    claims.update(overrides)
    return claims


class FakeVerifier:
    """Stands in for Google: returns whatever claims the test set, or raises."""

    def __init__(self, claims: dict[str, Any] | None = None, error: Exception | None = None):
        self.claims = claims if claims is not None else default_claims()
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, credential: str, client_id: str) -> dict[str, Any]:
        self.calls.append((credential, client_id))
        if self.error is not None:
            raise self.error
        return self.claims


class RecordingCloudinary:
    """Stands in for CloudinaryService: records what would be uploaded, never goes out."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.fail = False

    async def upload_avatar(
        self, content: bytes, *, content_type: str = "image/jpeg", kind: str = "user"
    ) -> UploadedImage:
        if self.fail:
            from app.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError("Cloudinary is not configured")
        public_id = f"{kind}/google-avatar-{len(self.uploads)}"
        self.uploads.append(
            {
                "content": content,
                "content_type": content_type,
                "kind": kind,
                "public_id": public_id,
            }
        )
        return UploadedImage(url=UPLOADED, public_id=public_id)

    async def delete_asset(self, public_id: str) -> bool:
        self.deleted.append(public_id)
        return True


class FakeResponse:
    def __init__(self, *, status_code: int, content_type: str, content: bytes) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class FakeDownloader:
    """An ``httpx.AsyncClient`` stand-in for the host Google serves pictures from."""

    def __init__(
        self, *, status: int = 200, content_type: str = "image/jpeg", content: bytes = IMAGE_BYTES
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.content = content
        self.requested: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(
            status_code=self.status, content_type=self.content_type, content=self.content
        )


@pytest_asyncio.fixture
async def verifier() -> FakeVerifier:
    return FakeVerifier()


@pytest_asyncio.fixture
async def cloudinary() -> RecordingCloudinary:
    return RecordingCloudinary()


def use_google(
    *,
    verifier: FakeVerifier,
    cloudinary: RecordingCloudinary,
    downloader: FakeDownloader | None = None,
    settings: Settings = CONFIGURED,
) -> None:
    """Point the endpoint at fakes for one test."""
    app.dependency_overrides[get_google_auth_service] = lambda: GoogleAuthService(
        settings, verifier=verifier, downloader=downloader or FakeDownloader()
    )
    app.dependency_overrides[get_cloudinary_service] = lambda: cloudinary
    app.dependency_overrides[get_settings] = lambda: settings


@pytest_asyncio.fixture(autouse=True)
async def google(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    """Every test gets the fakes, so none of them can reach Google by accident."""
    use_google(verifier=verifier, cloudinary=cloudinary)


async def sign_in(client: AsyncClient, credential: str = "a-google-credential-value") -> Any:
    return await client.post("/auth/google", json={"credential": credential})


async def stored(session_factory: async_sessionmaker[AsyncSession], user_id: int) -> User:
    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.id == user_id))).first()
        assert user is not None
        return user


async def sub_count(session_factory: async_sessionmaker[AsyncSession], sub: str) -> int:
    async with session_factory() as db:
        total = await db.scalar(
            select(func.count()).select_from(User).where(User.google_sub == sub)
        )
    return int(total or 0)


# -- Creating an account ---------------------------------------------------------------


async def test_a_first_google_sign_in_creates_the_account(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], verifier: FakeVerifier
) -> None:
    response = await sign_in(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "ali.reza@gmail.com"  # canonical, not as Google sent it
    assert body["user"]["display_name"] == "Ali Reza"
    assert body["user"]["avatar_url"] == UPLOADED
    assert body["user"]["has_password"] is False
    assert body["user"]["status"] == "ACTIVE"
    # The credential went to the verifier with this application's client id: checking
    # the audience is the whole reason the call happens.
    assert verifier.calls == [("a-google-credential-value", CLIENT_ID)]

    row = await stored(session_factory, body["user"]["id"])
    assert row.google_sub == "110169484474386276334"
    assert row.password_hash is None
    assert row.last_login_at is not None


async def test_the_username_is_generated_from_the_email(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Accounts are identified by username here, so one is derived — never asked for."""
    body = (await sign_in(client)).json()

    assert body["user"]["username"] == "ali.reza"
    assert (await stored(session_factory, body["user"]["id"])).username == "ali.reza"


async def test_a_taken_username_is_stepped_past(client: AsyncClient) -> None:
    await auth_user(client, "ali.reza")

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "ali.reza2"


async def test_a_placeholder_is_used_when_the_address_yields_nothing(
    client: AsyncClient, verifier: FakeVerifier
) -> None:
    """A one-character local part and a name in a script the rules reject leave
    nothing to build on — the account is still created, under a plain name."""
    verifier.claims = default_claims(email="a@gmail.com", name="姓 名")

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "user"


# -- The Google picture ----------------------------------------------------------------


async def test_the_picture_is_copied_into_this_accounts_cloudinary(
    client: AsyncClient, cloudinary: RecordingCloudinary
) -> None:
    """Google's URL is never what gets stored: the image is copied and hosted here."""
    await sign_in(client)

    assert len(cloudinary.uploads) == 1
    upload = cloudinary.uploads[0]
    assert upload["kind"] == "user"
    assert upload["content"] == IMAGE_BYTES
    assert upload["content_type"] == "image/jpeg"


async def test_the_picture_is_requested_at_avatar_size(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    """Google serves a square crop at whatever size the URL asks for."""
    downloader = FakeDownloader()
    use_google(verifier=verifier, cloudinary=cloudinary, downloader=downloader)

    await sign_in(client)

    assert downloader.requested == [GOOGLE_PICTURE.replace("=s96-c", "=s512-c")]


async def test_a_picture_that_cannot_be_fetched_still_creates_the_account(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    """An avatar is a nicety; the account must not depend on one."""
    use_google(verifier=verifier, cloudinary=cloudinary, downloader=FakeDownloader(status=404))

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert response.json()["user"]["avatar_url"] is None
    assert cloudinary.uploads == []


async def test_something_that_is_not_an_image_is_not_stored(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    use_google(
        verifier=verifier,
        cloudinary=cloudinary,
        downloader=FakeDownloader(content_type="text/html"),
    )

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert cloudinary.uploads == []
    assert response.json()["user"]["avatar_url"] is None


async def test_a_picture_hosted_elsewhere_is_not_fetched(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    """An arbitrary URL in a claim must never become an outbound request from the API."""
    verifier.claims = default_claims(picture="https://evil.example.com/tracker.png")
    downloader = FakeDownloader()
    use_google(verifier=verifier, cloudinary=cloudinary, downloader=downloader)

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert downloader.requested == []
    assert response.json()["user"]["avatar_url"] is None


async def test_a_failing_upload_still_creates_the_account(
    client: AsyncClient, cloudinary: RecordingCloudinary
) -> None:
    cloudinary.fail = True

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert response.json()["user"]["avatar_url"] is None


async def test_an_upload_is_undone_when_the_account_cannot_be_created(
    client: AsyncClient,
    cloudinary: RecordingCloudinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The picture is uploaded before the insert, so a refused insert must remove it —
    otherwise every failed first sign-in leaves an image nothing points at."""

    async def refuse(self: UserRepository, user: User) -> User:
        raise IntegrityError("insert", {}, Exception("UK_USERS_EMAIL"))

    monkeypatch.setattr(UserRepository, "add", refuse)

    response = await sign_in(client)

    assert response.status_code == 409
    assert len(cloudinary.uploads) == 1
    assert cloudinary.deleted == [cloudinary.uploads[0]["public_id"]]


# -- Signing in again ------------------------------------------------------------------


async def test_a_known_google_account_signs_in(client: AsyncClient, verifier: FakeVerifier) -> None:
    first = (await sign_in(client)).json()

    second = await sign_in(client)

    assert second.status_code == 200, second.text
    assert second.json()["user"]["id"] == first["user"]["id"]
    assert len(verifier.calls) == 2


async def test_a_later_sign_in_never_rewrites_the_profile(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    """The rule this feature is most likely to trip over.

    Display name and picture are the user's own from the moment the account exists —
    they can change both in settings, and a later Google sign-in must not undo that.
    """
    created = (await sign_in(client)).json()
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    assert (
        await client.patch(
            "/me",
            json={
                "display_name": "Ali R.",
                "avatar_url": "https://res.cloudinary.com/demo/image/upload/v1/users/mine.webp",
            },
            headers=headers,
        )
    ).status_code == 200
    # Google, meanwhile, would hand over a different name and picture.
    verifier.claims = default_claims(
        name="Ali Reza (Google)", picture="https://lh3.googleusercontent.com/a/other=s96-c"
    )
    cloudinary.uploads.clear()

    again = (await sign_in(client)).json()["user"]

    assert again["id"] == created["user"]["id"]
    assert again["display_name"] == "Ali R."
    assert again["avatar_url"].endswith("users/mine.webp")
    assert cloudinary.uploads == []
    assert cloudinary.deleted == []


async def test_one_google_identity_cannot_become_two_accounts(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    cloudinary: RecordingCloudinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup can lose a race; UK_USERS_GOOGLE_SUB cannot.

    Two requests with the same fresh credential can both find no account and both try
    to create one. The constraint is what makes the second impossible, and the failure
    is reported as a conflict rather than a 500 — with the upload it made along the way
    cleaned up, since nothing will point at it.
    """
    await sign_in(client)
    cloudinary.uploads.clear()

    async def missing(self: UserRepository, *args: Any) -> User | None:
        # Pretend the winner has not committed yet, which is exactly the race: both
        # lookups come back empty and creation is attempted a second time.
        return None

    monkeypatch.setattr(UserRepository, "get_by_google_sub", missing)
    monkeypatch.setattr(UserRepository, "get_by_email", missing)

    response = await sign_in(client)

    assert response.status_code == 409
    assert len(cloudinary.uploads) == 1
    assert cloudinary.deleted == [cloudinary.uploads[0]["public_id"]]
    # Still exactly one account for that Google identity.
    assert await sub_count(session_factory, "110169484474386276334") == 1


async def test_the_database_refuses_a_second_account_for_one_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The constraint itself, at the level it lives (Oracle has it too)."""
    now = utcnow()
    async with session_factory() as db:
        for username in ("first", "second"):
            db.add(
                User(
                    username=username,
                    google_sub="one-and-the-same-sub",
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                break
        else:
            raise AssertionError("a second account with the same sub was accepted")

    assert await sub_count(session_factory, "one-and-the-same-sub") == 1


async def test_a_disabled_account_cannot_sign_in_with_google(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    created = (await sign_in(client)).json()["user"]
    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.id == created["id"]))).first()
        assert user is not None
        user.status = "DISABLED"
        await db.commit()

    response = await sign_in(client)

    assert response.status_code == 403


# -- The email that already has an account ---------------------------------------------


async def test_an_existing_email_is_not_taken_over(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    verifier: FakeVerifier,
) -> None:
    """A Google sign-in must not become a way into a password account.

    Google's address is verified and this application stores verified addresses — but
    adopting the account here would grant access without the password protecting it,
    so the sign-in is refused and nothing is written (docs/google-signin-notes.md).
    """
    now = utcnow()
    async with session_factory() as db:
        db.add(
            User(
                username="existing",
                password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-a-real-hash",
                email="ali.reza@gmail.com",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    response = await sign_in(client)

    assert response.status_code == 409
    assert "already has an account" in response.json()["detail"]
    # The claim in the refused credential was not attached to that account either.
    assert await sub_count(session_factory, "110169484474386276334") == 0
    assert len(verifier.calls) == 1


# -- Rejected credentials --------------------------------------------------------------


async def test_an_invalid_credential_is_rejected(
    client: AsyncClient, verifier: FakeVerifier
) -> None:
    verifier.error = ValueError("Token has wrong audience")

    response = await sign_in(client)

    assert response.status_code == 400
    assert response.json() == {"detail": "That Google sign-in could not be verified"}


async def test_the_audience_checked_is_this_applications_client_id(
    client: AsyncClient, verifier: FakeVerifier
) -> None:
    """The library is handed the client id, so a token minted for another application
    fails there rather than here."""
    verifier.error = ValueError("Token has wrong audience")

    await sign_in(client)

    assert verifier.calls[0][1] == CLIENT_ID


async def test_an_expired_credential_is_rejected(
    client: AsyncClient, verifier: FakeVerifier
) -> None:
    verifier.error = ValueError("Token expired")

    assert (await sign_in(client)).status_code == 400


async def test_a_hand_written_credential_creates_nothing(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    verifier: FakeVerifier,
) -> None:
    """The claim is not "we read the token", it is "Google signed it"."""

    def segment(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    forged = f"{segment({'alg': 'none'})}.{segment(default_claims())}.signature"
    verifier.error = ValueError("Wrong number of segments")

    response = await sign_in(client, forged)

    assert response.status_code == 400
    assert verifier.calls == [(forged, CLIENT_ID)]
    assert await sub_count(session_factory, "110169484474386276334") == 0


async def test_a_google_outage_is_not_a_bad_credential(
    client: AsyncClient, verifier: FakeVerifier
) -> None:
    """Google's key set being unreachable must not read as "your token is bad"."""
    from google.auth.exceptions import TransportError

    verifier.error = TransportError("connection refused")

    response = await sign_in(client)

    assert response.status_code == 503


async def test_an_unverified_email_is_refused(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], verifier: FakeVerifier
) -> None:
    verifier.claims = default_claims(email_verified=False)

    response = await sign_in(client)

    assert response.status_code == 400
    assert "verified email" in response.json()["detail"]
    assert await sub_count(session_factory, "110169484474386276334") == 0


async def test_an_identity_without_a_subject_is_refused(
    client: AsyncClient, verifier: FakeVerifier
) -> None:
    verifier.claims = default_claims(sub="")

    assert (await sign_in(client)).status_code == 400


async def test_an_unconfigured_google_client_is_a_503(
    client: AsyncClient, verifier: FakeVerifier, cloudinary: RecordingCloudinary
) -> None:
    use_google(verifier=verifier, cloudinary=cloudinary, settings=UNCONFIGURED)

    response = await sign_in(client)

    assert response.status_code == 503
    assert response.json() == {"detail": "Google sign-in is not configured"}


async def test_a_credential_shaped_like_nothing_is_rejected_by_the_schema(
    client: AsyncClient,
) -> None:
    assert (await client.post("/auth/google", json={"credential": "short"})).status_code == 422


# -- Two-step verification -------------------------------------------------------------


async def test_google_sign_in_still_needs_the_second_factor(
    client: AsyncClient,
    clock: FakeClock,
    sms: RecordingSmsService,
    verifier: FakeVerifier,
) -> None:
    """A second factor this application owns cannot be skipped by using another way in."""
    created = (await sign_in(client)).json()
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    started = await client.post(
        "/me/otp/enable", json={"mobile_number": "9123456789"}, headers=headers
    )
    assert started.status_code == 200, started.text
    assert (
        await client.post(
            "/me/otp/verify",
            json={"challenge_id": started.json()["challenge_id"], "code": sms.last_code},
            headers=headers,
        )
    ).status_code == 200
    clock.advance(61)

    response = await sign_in(client)

    assert response.status_code == 200, response.text
    assert response.json()["otp_required"] is True
    assert response.json()["delivery_method"] == "SMS"
    assert "access_token" not in response.json()

    completed = await client.post(
        "/auth/login/otp",
        json={"challenge_id": response.json()["challenge_id"], "code": sms.last_code},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["user"]["id"] == created["user"]["id"]


# -- What the account can still do ------------------------------------------------------


async def test_a_passwordless_account_cannot_sign_in_with_a_password(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Including with the literal the timing-protection hash is built from: the dummy
    is used to spend time, and must never become a password an account answers to."""
    created = (await sign_in(client)).json()["user"]

    for password in (TEST_PASSWORD, "dummy-password-for-timing", ""):
        response = await client.post(
            "/auth/login", json={"username": created["username"], "password": password}
        )
        assert response.status_code in (401, 422), (password, response.text)

    assert (await stored(session_factory, created["id"])).password_hash is None


async def test_a_passwordless_account_can_set_a_first_password(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """There is no current password to prove, and the account must not be stuck
    without one — losing the Google account would otherwise lose the account."""
    created = (await sign_in(client)).json()
    headers = {"Authorization": f"Bearer {created['access_token']}"}

    changed = await client.post(
        "/me/password",
        json={"current_password": "", "new_password": "a-real-password-123"},
        headers=headers,
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["has_password"] is True
    assert (await stored(session_factory, created["user"]["id"])).password_hash is not None
    login = await login_user(client, created["user"]["username"], "a-real-password-123")
    assert login["user"]["id"] == created["user"]["id"]


async def test_changing_a_password_still_needs_the_current_one(client: AsyncClient) -> None:
    """The leniency above is for accounts with no password; nobody else gets it."""
    headers, _user = await auth_user(client, "alice")

    refused = await client.post(
        "/me/password",
        json={"current_password": "", "new_password": "a-real-password-123"},
        headers=headers,
    )

    assert refused.status_code == 400
    assert refused.json() == {"detail": "That password is incorrect"}


async def test_the_profile_says_where_the_password_stands(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")

    assert (await client.get("/me", headers=headers)).json()["has_password"] is True


def test_the_schema_carries_the_answer_and_never_the_hash() -> None:
    """`UserRead` is the only user shape any endpoint returns."""
    assert "password_hash" not in UserRead.model_fields
    assert "has_password" in UserRead.model_fields


def test_the_generated_username_always_fits_the_application_rules() -> None:
    """A sign-in must not fail because a display detail could not be made to fit."""
    from app.schemas.users import USERNAME_MAX_LENGTH
    from app.services.google_auth_service import GoogleIdentity, _suffixed, _username_base

    for email, name in (
        ("ali.reza@gmail.com", "Ali Reza"),
        ("a.b@gmail.com", None),
        ("x@gmail.com", "姓 名"),
        ("a" * 60 + "@gmail.com", None),
        ("...@gmail.com", ".."),
    ):
        base = _username_base(GoogleIdentity(sub="s", email=email, name=name, picture=None))
        for candidate in (base, _suffixed(base, 1), _suffixed(base, 19)):
            assert len(candidate) <= USERNAME_MAX_LENGTH, candidate
            assert len(candidate) >= 3 and candidate.strip() == candidate, repr(candidate)
