"""Google's own verification path, exercised offline.

The other Google tests mock the verifier so they can test the flow. These do the
opposite: ``google.oauth2.id_token.verify_oauth2_token`` runs for real — signature,
issuer, audience and expiry — against a key pair generated here. Only the *transport*
is fake, so nothing here touches the network and nothing about the verification is
stubbed out, which is what proves this application does not merely decode a token and
trust its payload.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.exceptions import BadRequestError, ServiceUnavailableError
from app.services.google_auth_service import CachedKeySet, GoogleAuthService, key_set_of
from tests.conftest import TEST_SETTINGS

CLIENT_ID = "366133901630-c6jfg3hiirfqj4p57n2i860hs9jcl8qf.apps.googleusercontent.com"
SETTINGS = TEST_SETTINGS.model_copy(update={"google_client_id": CLIENT_ID})
ISSUER = "https://accounts.google.com"
KEY_ID = "test-key-1"


class CertificateResponse:
    """What the certificate endpoint returns: the `kid -> PEM` set, as JSON."""

    def __init__(self, keys: dict[str, str]) -> None:
        self.status = 200
        self.data = json.dumps(keys).encode("utf-8")


class FakeTransport:
    """Stands in for ``google.auth.transport.requests.Request`` — and counts calls."""

    def __init__(self, *keys: dict[str, str]) -> None:
        self.keys = keys[0] if keys else {}
        self.calls = 0

    def __call__(self, url: str, method: str = "GET", body: Any = None, **kwargs: Any) -> Any:
        self.calls += 1
        return CertificateResponse(self.keys)


class ManualClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def rsa_key_pair() -> tuple[Any, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public_pem


def google_token(private: Any, **overrides: Any) -> str:
    """A token shaped exactly as Google mints one: RS256, with a `kid` header."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "sub": "110169484474386276334",
        "email": "someone@gmail.com",
        "email_verified": True,
        "name": "Some One",
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": KEY_ID})


@pytest.fixture
def google_keys() -> tuple[Any, str]:
    """A key pair this test controls, published as Google's."""
    return rsa_key_pair()


def build(
    google_keys: tuple[Any, str], *, clock: ManualClock | None = None
) -> tuple[GoogleAuthService, FakeTransport]:
    """A service that verifies against this test's key pair, through a fake transport."""
    _private, public_pem = google_keys
    transport = FakeTransport({KEY_ID: public_pem})
    key_set = CachedKeySet(transport, clock=clock or ManualClock())
    return GoogleAuthService(SETTINGS, key_set=key_set), transport


# -- Accepted --------------------------------------------------------------------------


def test_a_properly_signed_google_token_is_accepted(google_keys: tuple[Any, str]) -> None:
    """The point of the whole thing: Google's own verification code accepts it, so its
    claims are trustworthy."""
    service, _transport = build(google_keys)

    identity = service.verify(google_token(google_keys[0]))

    assert identity.sub == "110169484474386276334"
    assert identity.email == "someone@gmail.com"
    assert identity.name == "Some One"


# -- Rejected, each for its own reason --------------------------------------------------


def test_a_token_signed_by_another_key_is_rejected(google_keys: tuple[Any, str]) -> None:
    """Well-formed, correctly shaped, signed by a key Google does not publish."""
    service, _transport = build(google_keys)
    other_private, _other_public = rsa_key_pair()

    with pytest.raises(BadRequestError):
        service.verify(google_token(other_private))


def test_a_token_for_another_application_is_rejected(google_keys: tuple[Any, str]) -> None:
    """The audience check: a token minted for a different client id is not this app's."""
    service, _transport = build(google_keys)

    with pytest.raises(BadRequestError):
        service.verify(
            google_token(google_keys[0], aud="999999999-another-app.apps.googleusercontent.com")
        )


def test_an_expired_token_is_rejected(google_keys: tuple[Any, str]) -> None:
    service, _transport = build(google_keys)
    long_ago = datetime.now(UTC) - timedelta(hours=2)

    with pytest.raises(BadRequestError):
        service.verify(
            google_token(
                google_keys[0],
                iat=int(long_ago.timestamp()),
                exp=int((long_ago + timedelta(minutes=10)).timestamp()),
            )
        )


def test_a_token_from_another_issuer_is_rejected(google_keys: tuple[Any, str]) -> None:
    service, _transport = build(google_keys)

    with pytest.raises(BadRequestError):
        service.verify(google_token(google_keys[0], iss="https://accounts.evil.example.com"))


def test_a_token_without_an_expiry_is_rejected(google_keys: tuple[Any, str]) -> None:
    service, _transport = build(google_keys)
    token = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "1"},
        google_keys[0],
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )

    with pytest.raises(BadRequestError):
        service.verify(token)


def test_a_token_with_an_unknown_key_id_is_rejected(google_keys: tuple[Any, str]) -> None:
    service, _transport = build(google_keys)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "1",
            "exp": int(datetime.now(UTC).timestamp()) + 600,
        },
        google_keys[0],
        algorithm="RS256",
        headers={"kid": "a-key-google-never-published"},
    )

    with pytest.raises(BadRequestError):
        service.verify(token)


def test_a_hand_written_token_is_rejected(google_keys: tuple[Any, str]) -> None:
    """`alg: none` with a plain payload: valid base64, no signature at all."""
    service, _transport = build(google_keys)
    header = jwt.utils.base64url_encode(json.dumps({"alg": "none"}).encode()).decode()
    payload = jwt.utils.base64url_encode(
        json.dumps({"sub": "1", "aud": CLIENT_ID, "iss": ISSUER}).encode()
    ).decode()

    with pytest.raises(BadRequestError):
        service.verify(f"{header}.{payload}.")


def test_unreachable_google_keys_are_reported_as_unavailable() -> None:
    """The one failure that is *ours*: the key set could not be fetched at all."""
    from google.auth.exceptions import TransportError

    def unreachable(url: str, **kwargs: Any) -> Any:
        raise TransportError("Could not fetch certificates")

    service = GoogleAuthService(SETTINGS, key_set=CachedKeySet(unreachable))

    with pytest.raises(ServiceUnavailableError):
        service.verify("a.b.c")


# -- The key set cache -----------------------------------------------------------------


def test_the_key_set_is_fetched_once_for_repeated_verifications(
    google_keys: tuple[Any, str],
) -> None:
    """google-auth fetches keys per verification; this application does not.

    A sign-in is rare, but a fetch per sign-in is a round trip per sign-in — and the
    endpoint it goes to is not always fast.
    """
    service, transport = build(google_keys)
    token = google_token(google_keys[0])

    service.verify(token)
    service.verify(token)

    assert transport.calls == 1


def test_the_key_set_is_fetched_again_after_the_ttl(google_keys: tuple[Any, str]) -> None:
    """Bounded staleness: a rotated key is picked up within the hour."""
    clock = ManualClock()
    service, transport = build(google_keys, clock=clock)
    token = google_token(google_keys[0])

    service.verify(token)
    clock.advance(3601)
    service.verify(token)

    assert transport.calls == 2


def test_the_cached_response_is_the_one_google_sends() -> None:
    """The cache stores the response, not a parsed form of it."""
    response = CertificateResponse({"key": "pem"})

    assert key_set_of(response) == {"key": "pem"}
