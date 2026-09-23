from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, parse_api_keys

GOOD_SECRET = "x" * 32
GOOD_KEYS = "alice-test-api-key-000000:alice"


def _settings(**overrides) -> Settings:
    values = {"signing_secret": GOOD_SECRET, "api_keys": GOOD_KEYS, "data_dir": Path("/tmp/x")}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_settings_load() -> None:
    settings = _settings(public_base_url="https://files.example.com/")
    assert settings.api_key_map == {"alice-test-api-key-000000": "alice"}
    assert settings.public_base_url == "https://files.example.com"


def test_missing_signing_secret_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGNING_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_keys=GOOD_KEYS)


def test_short_signing_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(signing_secret="too-short")


def test_secret_is_not_exposed_in_repr() -> None:
    assert GOOD_SECRET not in repr(_settings())


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-separator-here-000000",
        ":alice",
        "short:alice",
        "k" * 20 + ":",
        "a" * 20 + ":x," + "a" * 20 + ":y",
    ],
)
def test_malformed_api_keys_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_api_keys(raw)


def test_invalid_ttl_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(min_ttl_seconds=100, max_ttl_seconds=10)


def test_base_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        _settings(public_base_url="ftp://example.com")
