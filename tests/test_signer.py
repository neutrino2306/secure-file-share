import pytest

from app import signer
from app.signer import VerifyResult

SECRET = b"unit-test-secret-0123456789abcdefghij"
FILE_ID = "a" * 32
LINK_ID = "b" * 32
NOW = 1_800_000_000
EXPIRES = NOW + 3600


def _sig(file_id: str = FILE_ID, link_id: str = LINK_ID, expires: int = EXPIRES) -> str:
    return signer.sign(SECRET, file_id, link_id, expires)


def test_valid_signature_verifies() -> None:
    result = signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), _sig(), NOW)
    assert result is VerifyResult.VALID


def test_signature_is_deterministic_and_url_safe() -> None:
    sig = _sig()
    assert sig == _sig()
    assert "=" not in sig and "+" not in sig and "/" not in sig


@pytest.mark.parametrize(
    ("file_id", "link_id", "expires"),
    [
        ("c" * 32, LINK_ID, str(EXPIRES)),  # link pointed at another file
        (FILE_ID, "c" * 32, str(EXPIRES)),  # different link id
        (FILE_ID, LINK_ID, str(EXPIRES + 1)),  # extended expiry
    ],
)
def test_tampered_fields_are_rejected(file_id: str, link_id: str, expires: str) -> None:
    result = signer.verify(SECRET, file_id, link_id, expires, _sig(), NOW)
    assert result is VerifyResult.INVALID_SIGNATURE


def test_tampered_signature_is_rejected() -> None:
    sig = _sig()
    tampered = ("A" if sig[0] != "A" else "B") + sig[1:]
    result = signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), tampered, NOW)
    assert result is VerifyResult.INVALID_SIGNATURE


def test_wrong_secret_is_rejected() -> None:
    other = signer.sign(b"another-secret-0123456789abcdefghijkl", FILE_ID, LINK_ID, EXPIRES)
    result = signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), other, NOW)
    assert result is VerifyResult.INVALID_SIGNATURE


def test_expired_link_is_reported_as_expired() -> None:
    result = signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), _sig(), EXPIRES + 1)
    assert result is VerifyResult.EXPIRED


def test_expiry_boundary_is_exclusive() -> None:
    # A link is expired at exactly expires_at.
    assert signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), _sig(), EXPIRES - 0.001) is (
        VerifyResult.VALID
    )
    assert signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), _sig(), EXPIRES) is (
        VerifyResult.EXPIRED
    )


def test_expired_but_forged_link_is_invalid_not_expired() -> None:
    # Only authentic links may learn that they are expired.
    result = signer.verify(SECRET, FILE_ID, LINK_ID, str(EXPIRES), "forged", EXPIRES + 1)
    assert result is VerifyResult.INVALID_SIGNATURE


@pytest.mark.parametrize(
    ("file_id", "link_id", "expires", "sig"),
    [
        ("", LINK_ID, str(EXPIRES), "x"),
        ("../etc/passwd", LINK_ID, str(EXPIRES), "x"),
        (FILE_ID.upper(), LINK_ID, str(EXPIRES), "x"),
        (FILE_ID, "short", str(EXPIRES), "x"),
        (FILE_ID, LINK_ID, "", "x"),
        (FILE_ID, LINK_ID, "-1", "x"),
        (FILE_ID, LINK_ID, "12.5", "x"),
        (FILE_ID, LINK_ID, "9" * 40, "x"),
        (FILE_ID, LINK_ID, str(EXPIRES), ""),
        (FILE_ID, LINK_ID, str(EXPIRES), "é" * 10),  # non-ASCII must not raise TypeError
        (FILE_ID, LINK_ID, str(EXPIRES), "x" * 500),
    ],
)
def test_malformed_input_is_invalid_and_never_raises(
    file_id: str, link_id: str, expires: str, sig: str
) -> None:
    assert signer.verify(SECRET, file_id, link_id, expires, sig, NOW) is (
        VerifyResult.INVALID_SIGNATURE
    )
