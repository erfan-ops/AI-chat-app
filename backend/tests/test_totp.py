"""Authenticator codes: the secret, the otpauth URI, and what verification accepts.

The RFC 6238 vectors are the independent check on the parameters. They are the values
the standard publishes for SHA1, so a code derived from them proves this speaks the
same algorithm Google Authenticator does — rather than merely agreeing with itself.

The clock is always the ``FakeTimeService`` from ``conftest``: a test can move it,
and none of them can reach an NTP server.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import re
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from app.core.contact import stored_secret
from app.exceptions import ServiceUnavailableError
from app.services.totp_service import (
    TOTP_DIGEST,
    TOTP_DIGITS,
    TOTP_INTERVAL_SECONDS,
    TOTP_ISSUER,
    TOTP_VALID_WINDOW,
    TotpService,
    totp_validator,
)
from tests.conftest import FakeTimeService

BASE32 = re.compile(r"^[A-Z2-7]{32}$")

# RFC 6238 Appendix B: the SHA1 secret is the ASCII string "12345678901234567890", and
# each timestamp has a published 8-digit code. The 6-digit form is that value modulo
# 10**6 — the same truncation with a shorter modulus.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = [
    (59, "94287082"),
    (1_111_111_109, "07081804"),
    (1_111_111_111, "14050471"),
    (1_234_567_890, "89005924"),
    (2_000_000_000, "69279037"),
    (20_000_000_000, "65353130"),
]

# An instant that is not near a 30-second boundary, so "one period earlier" is
# unambiguous when the tests walk the window.
BASE_TIME = 1_750_000_000.0


def _code_at(totp: TotpService, secret: str, unix_time: float) -> str:
    return totp.code_at(secret=secret, unix_time=unix_time)


# -- Parameters -----------------------------------------------------------------------


def test_parameters_are_the_ones_authenticator_apps_assume() -> None:
    """SHA1, six digits, thirty seconds — and a window of one period each way.

    Apps assume these defaults when an otpauth URI omits them, so advertising anything
    else would mean the app and the server disagree about what a code is.
    """
    assert TOTP_DIGEST is hashlib.sha1
    assert (TOTP_DIGITS, TOTP_INTERVAL_SECONDS) == (6, 30)
    # Previous, current and next period. Wider would accept a code a minute old.
    assert TOTP_VALID_WINDOW == 1


@pytest.mark.parametrize(("unix_time", "published"), RFC_VECTORS)
def test_codes_match_the_published_rfc_6238_vectors(
    unix_time: int, published: str, totp_service: TotpService
) -> None:
    code = _code_at(totp_service, RFC_SECRET, unix_time)

    assert code == published[-TOTP_DIGITS:]
    assert len(code) == TOTP_DIGITS


# -- The secret -----------------------------------------------------------------------


def test_the_generated_secret_fills_the_column(totp_service: TotpService) -> None:
    secret = totp_service.generate_secret()

    assert BASE32.match(secret), secret
    # 20 random bytes is exactly 32 Base32 characters — the width of USERS.TOTP_SECRET.
    assert len(base64.b32decode(secret)) == 20


def test_every_generated_secret_is_different(totp_service: TotpService) -> None:
    assert len({totp_service.generate_secret() for _ in range(50)}) == 50


def test_the_generator_cannot_be_derived_from_the_account(totp_service: TotpService) -> None:
    """Nothing about a user can feed the generator: there is nothing to pass it."""
    assert list(inspect.signature(totp_service.generate_secret).parameters) == []


def test_a_stored_secret_is_read_back_without_its_padding() -> None:
    """``TOTP_SECRET`` is ``CHAR(32)``, which blank-pads whatever Oracle stores."""
    assert stored_secret("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ") == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert stored_secret("ABCDEFGH" + " " * 24) == "ABCDEFGH"
    assert stored_secret(None) is None
    assert stored_secret("   ") is None


# -- The provisioning URI -------------------------------------------------------------


def test_the_uri_names_the_issuer_and_the_users_username(totp_service: TotpService) -> None:
    secret = totp_service.generate_secret()

    uri = totp_service.provisioning_uri(secret=secret, username="alice")

    parsed = urlparse(uri)
    assert parsed.scheme == "otpauth"
    assert parsed.netloc == "totp"
    # The issuer is in the label *and* in the query, spelled identically — apps read
    # the query and show the label, so a mismatch shows one name under another.
    assert unquote(parsed.path) == f"/{TOTP_ISSUER}:alice"
    query = parse_qs(parsed.query)
    assert query["issuer"] == [TOTP_ISSUER]
    assert query["secret"] == [secret]


def test_a_username_that_needs_encoding_survives_the_uri(totp_service: TotpService) -> None:
    uri = totp_service.provisioning_uri(secret=RFC_SECRET, username="ali reza+z")

    assert unquote(urlparse(uri).path) == f"/{TOTP_ISSUER}:ali reza+z"
    # Encoded in the wire form, so a space never truncates the label.
    assert "ali%20reza%2Bz" in uri


# -- Verification ---------------------------------------------------------------------


def test_the_current_code_is_accepted(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    secret = totp_service.generate_secret()
    code = _code_at(totp_service, secret, time_service.get_current_unix_time())

    assert totp_service.verify(secret=secret, code=code) is True


def test_the_previous_and_next_periods_are_accepted(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    """Clock drift between the phone and the server is expected; ±30s covers it."""
    secret = totp_service.generate_secret()
    now = time_service.get_current_unix_time()

    for offset in (-TOTP_INTERVAL_SECONDS, 0, TOTP_INTERVAL_SECONDS):
        code = _code_at(totp_service, secret, now + offset)
        assert totp_service.verify(secret=secret, code=code) is True, offset


def test_codes_outside_the_window_are_rejected(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    secret = totp_service.generate_secret()
    now = time_service.get_current_unix_time()

    for offset in (-120, -60, 60, 120):
        code = _code_at(totp_service, secret, now + offset)
        assert totp_service.verify(secret=secret, code=code) is False, offset


def test_a_code_from_another_secret_is_rejected(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    secret, other = totp_service.generate_secret(), totp_service.generate_secret()
    code = _code_at(totp_service, other, time_service.get_current_unix_time())

    assert totp_service.verify(secret=secret, code=code) is False


def test_a_mistyped_code_is_rejected(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    secret = totp_service.generate_secret()
    correct = _code_at(totp_service, secret, time_service.get_current_unix_time())
    mistyped = f"{correct[0]}{(int(correct[1]) + 1) % 10}{correct[2:]}"

    assert mistyped != correct
    assert totp_service.verify(secret=secret, code=mistyped) is False
    assert totp_service.verify(secret=secret, code="not a code") is False
    assert totp_service.verify(secret=secret, code="") is False


def test_verification_uses_the_corrected_clock_not_the_servers_own() -> None:
    """The code is computed from corrected time, so a drifting server still agrees."""
    one_hour_behind = FakeTimeService(local=1_750_000_000.0, offset=3600.0)
    corrected = TotpService(one_hour_behind)
    secret = corrected.generate_secret()
    code = _code_at(corrected, secret, 1_750_003_600.0)

    assert corrected.verify(secret=secret, code=code) is True
    # The same code is not what the uncorrected clock would produce.
    uncorrected = TotpService(FakeTimeService(local=1_750_000_000.0, offset=0.0))
    assert uncorrected.verify(secret=secret, code=code) is False


def test_verification_refuses_when_the_clock_is_not_trustworthy(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    """A stale or never-synchronized offset must not be used to check a code.

    Accepting codes against a clock nobody has confirmed is exactly the silent
    failure this refuses: the caller gets 503 and can use another method.
    """
    secret = totp_service.generate_secret()
    code = _code_at(totp_service, secret, time_service.get_current_unix_time())

    time_service.usable = False

    with pytest.raises(ServiceUnavailableError):
        totp_service.verify(secret=secret, code=code)


# -- The validator handed to the challenge store ---------------------------------------


def test_the_validator_checks_a_code_against_that_users_secret(
    totp_service: TotpService, time_service: FakeTimeService
) -> None:
    secret = totp_service.generate_secret()
    user = type("User", (), {"totp_secret": secret})()
    check = totp_validator(user, totp_service)

    current = _code_at(totp_service, secret, time_service.get_current_unix_time())
    mistyped = f"{current[0]}{(int(current[1]) + 1) % 10}{current[2:]}"

    assert check(current) is True
    assert check(mistyped) is False


def test_the_validator_refuses_a_user_with_no_enrolled_secret(totp_service: TotpService) -> None:
    """Fails closed: a challenge with nothing to check against cannot be satisfied."""
    for stored in (None, "", "   "):
        user = type("User", (), {"totp_secret": stored})()
        with pytest.raises(ServiceUnavailableError):
            totp_validator(user, totp_service)
