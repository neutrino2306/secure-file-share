"""The core persistence guarantee: signed links survive a service restart.

A "restart" is simulated by discarding the app and building a new one from the same
configuration (same DATA_DIR and SIGNING_SECRET), exactly as a new process would.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import FakeClock, auth, create_link, make_settings, upload


def test_link_survives_restart(data_dir: Path) -> None:
    clock = FakeClock()
    first = TestClient(create_app(make_settings(data_dir), clock=clock))
    file_id = upload(first, content=b"persistent").json()["id"]
    url = create_link(first, file_id).json()["url"]
    del first

    restarted = TestClient(create_app(make_settings(data_dir), clock=clock))

    response = restarted.get(url)
    assert response.status_code == 200
    assert response.content == b"persistent"
    # Metadata and audit history survived too.
    assert restarted.get(f"/v1/files/{file_id}", headers=auth()).json()["link_count"] == 1


def test_link_is_rejected_after_secret_change(data_dir: Path) -> None:
    clock = FakeClock()
    first = TestClient(create_app(make_settings(data_dir), clock=clock))
    file_id = upload(first).json()["id"]
    url = create_link(first, file_id).json()["url"]

    rotated = make_settings(data_dir, signing_secret="a-completely-different-secret-0123456789")
    restarted = TestClient(create_app(rotated, clock=clock))

    # Proves link validity comes from the persisted secret, not from process memory.
    assert restarted.get(url).status_code == 403
