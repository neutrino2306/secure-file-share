"""Shared test helpers and constants."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings

SIGNING_SECRET = "test-signing-secret-0123456789-abcdefghijklmnop"
ALICE_KEY = "alice-test-api-key-000000"
BOB_KEY = "bob-test-api-key-11111111"
MAX_UPLOAD_BYTES = 1024
START_TIME = 1_800_000_000.0


class FakeClock:
    """Controllable clock so expiry can be tested without sleeping."""

    def __init__(self, start: float = START_TIME) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_settings(data_dir: Path, signing_secret: str = SIGNING_SECRET) -> Settings:
    return Settings(
        _env_file=None,
        signing_secret=signing_secret,
        api_keys=f"{ALICE_KEY}:alice,{BOB_KEY}:bob",
        public_base_url="http://testserver",
        data_dir=data_dir,
        max_upload_bytes=MAX_UPLOAD_BYTES,
    )


def auth(key: str = ALICE_KEY) -> dict[str, str]:
    return {"X-API-Key": key}


def upload(
    client: TestClient,
    content: bytes = b"hello world",
    filename: str = "hello.txt",
    key: str = ALICE_KEY,
    content_type: str = "text/plain",
):
    return client.post(
        "/v1/files",
        headers=auth(key),
        files={"file": (filename, content, content_type)},
    )


def create_link(client: TestClient, file_id: str, ttl: object = 3600, key: str = ALICE_KEY):
    return client.post(
        f"/v1/files/{file_id}/links",
        headers=auth(key),
        json={"ttl_seconds": ttl},
    )
