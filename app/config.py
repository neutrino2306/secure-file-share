"""Application settings loaded from environment variables (and an optional .env file).

Settings are validated at startup. A missing or weak signing secret, or malformed
API keys, make the service refuse to start (fail fast) instead of running insecurely.
"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32
MIN_API_KEY_LENGTH = 16


def parse_api_keys(raw: str) -> dict[str, str]:
    """Parse "key1:user1,key2:user2" into {"key1": "user1", "key2": "user2"}."""
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        key, sep, user_id = entry.partition(":")
        key, user_id = key.strip(), user_id.strip()
        if not sep or not key or not user_id:
            raise ValueError("API_KEYS entries must look like 'api_key:user_id'")
        if len(key) < MIN_API_KEY_LENGTH:
            raise ValueError(f"each API key must be at least {MIN_API_KEY_LENGTH} characters")
        if key in mapping:
            raise ValueError("duplicate API key in API_KEYS")
        mapping[key] = user_id
    if not mapping:
        raise ValueError("API_KEYS must contain at least one 'api_key:user_id' entry")
    return mapping


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Required: no defaults on purpose, so the service never runs with a known secret.
    signing_secret: SecretStr
    api_keys: SecretStr

    public_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("./data")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    min_ttl_seconds: int = Field(default=60, gt=0)
    max_ttl_seconds: int = Field(default=7 * 24 * 3600, gt=0)

    @field_validator("signing_secret")
    @classmethod
    def _check_secret_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_SECRET_LENGTH:
            raise ValueError(f"SIGNING_SECRET must be at least {MIN_SECRET_LENGTH} characters")
        return value

    @field_validator("api_keys")
    @classmethod
    def _check_api_keys(cls, value: SecretStr) -> SecretStr:
        parse_api_keys(value.get_secret_value())
        return value

    @field_validator("public_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _check_ttl_range(self) -> "Settings":
        if self.min_ttl_seconds > self.max_ttl_seconds:
            raise ValueError("MIN_TTL_SECONDS must not exceed MAX_TTL_SECONDS")
        return self

    @property
    def signing_key(self) -> bytes:
        return self.signing_secret.get_secret_value().encode("utf-8")

    @property
    def api_key_map(self) -> dict[str, str]:
        return parse_api_keys(self.api_keys.get_secret_value())

    @property
    def db_path(self) -> Path:
        return self.data_dir / "metadata.db"
