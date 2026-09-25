"""Cloudinary upload-signature tests.

The signature is the only thing the API contributes to an avatar upload, so these
tests pin the two properties that matter: the API secret never leaves the server,
and the signature is exactly what Cloudinary's documented algorithm produces for
the parameters the browser is told to send.
"""

from __future__ import annotations

import hashlib
import time

import pytest_asyncio
from httpx import AsyncClient

from app.api.dependencies import get_settings
from app.core.config import Settings
from app.main import app
from tests.conftest import TEST_SETTINGS, auth_user

CLOUD_NAME = "demo-cloud"
API_KEY = "123456789012345"
API_SECRET = "test-cloudinary-secret"
FOLDER = "characters"
USER_FOLDER = "users"

# Same JWT secret/database URL as the shared test settings: get_token_manager is
# lru_cached on the settings object, so a different secret would invalidate every
# token issued by the auth_user helper.
CONFIGURED_SETTINGS = Settings(
    jwt_secret=TEST_SETTINGS.jwt_secret,
    database_url=TEST_SETTINGS.database_url,
    cloudinary_cloud_name=CLOUD_NAME,
    cloudinary_api_key=API_KEY,
    cloudinary_api_secret=API_SECRET,
)

# Pinned explicitly rather than relying on the shared settings: a developer's real
# .env is picked up by Settings(), which would otherwise make this "unconfigured"
# case configured.
UNCONFIGURED_SETTINGS = Settings(
    jwt_secret=TEST_SETTINGS.jwt_secret,
    database_url=TEST_SETTINGS.database_url,
    cloudinary_cloud_name="",
    cloudinary_api_key="",
    cloudinary_api_secret="",
)


@pytest_asyncio.fixture
async def unconfigured_cloudinary(client: AsyncClient) -> None:
    """Point the app at settings without Cloudinary credentials for one test."""
    app.dependency_overrides[get_settings] = lambda: UNCONFIGURED_SETTINGS
    yield
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS


@pytest_asyncio.fixture
async def configured_cloudinary(client: AsyncClient) -> None:
    """Point the app at Cloudinary-configured settings for one test.

    Depends on ``client`` so the fixture's own setup/teardown ordering is
    guaranteed; the conftest clears dependency overrides afterwards anyway.
    """
    app.dependency_overrides[get_settings] = lambda: CONFIGURED_SETTINGS
    yield
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS


async def test_signature_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/cloudinary/signature")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


async def test_signature_unavailable_when_not_configured(
    client: AsyncClient, unconfigured_cloudinary: None
) -> None:
    headers, _user = await auth_user(client, "alice")

    response = await client.post("/cloudinary/signature", headers=headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Cloudinary is not configured"}


async def test_signature_matches_documented_algorithm(
    client: AsyncClient, configured_cloudinary: None
) -> None:
    headers, _user = await auth_user(client, "alice")

    response = await client.post("/cloudinary/signature", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()

    # Exactly the browser-safe fields — no secret, no extras.
    assert set(body) == {"signature", "timestamp", "api_key", "cloud_name", "folder"}
    assert body["cloud_name"] == CLOUD_NAME
    assert body["api_key"] == API_KEY
    assert body["folder"] == FOLDER
    assert abs(body["timestamp"] - int(time.time())) < 60

    # Recompute independently: sort the signed fields, join with "&", append the
    # secret, SHA-1 hex. (Comparing against the raw form is valid because no value
    # contains characters Cloudinary's encoding step would escape.)
    to_sign = f"folder={FOLDER}&timestamp={body['timestamp']}{API_SECRET}"
    assert body["signature"] == hashlib.sha1(to_sign.encode()).hexdigest()

    assert API_SECRET not in response.text


async def test_a_user_picture_is_signed_into_its_own_folder(
    client: AsyncClient, configured_cloudinary: None
) -> None:
    """The same upload flow, a different destination: the client names a kind, and the
    folder that gets signed is chosen here."""
    headers, _user = await auth_user(client, "alice")

    response = await client.post("/cloudinary/signature", json={"kind": "user"}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["folder"] == USER_FOLDER
    to_sign = f"folder={USER_FOLDER}&timestamp={body['timestamp']}{API_SECRET}"
    assert body["signature"] == hashlib.sha1(to_sign.encode()).hexdigest()


async def test_an_unknown_upload_kind_is_not_signed(
    client: AsyncClient, configured_cloudinary: None
) -> None:
    """Only the kinds this application knows are writable, so a client cannot ask for
    an arbitrary folder."""
    headers, _user = await auth_user(client, "alice")

    response = await client.post(
        "/cloudinary/signature", json={"kind": "../../private"}, headers=headers
    )

    assert response.status_code == 422
    assert API_SECRET not in response.text
