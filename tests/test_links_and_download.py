import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import db
from tests.helpers import BOB_KEY, FakeClock, auth, create_link, upload


def _upload_and_link(client: TestClient, content: bytes = b"secret data", ttl: int = 3600):
    file_id = upload(client, content=content, filename="report.txt").json()["id"]
    link = create_link(client, file_id, ttl=ttl).json()
    return file_id, link


def _with_param(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    params[name] = value
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{parts.path}?{query}"


def test_full_flow_upload_link_download(client: TestClient) -> None:
    file_id, link = _upload_and_link(client, content=b"secret data")

    assert link["file_id"] == file_id
    assert link["url"].startswith(f"http://testserver/d/{file_id}?")

    response = client.get(link["url"])

    assert response.status_code == 200
    assert response.content == b"secret data"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="report.txt"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


def test_link_expiry_is_computed_server_side(client: TestClient, clock: FakeClock) -> None:
    _, link = _upload_and_link(client, ttl=120)

    exp = int(parse_qs(urlsplit(link["url"]).query)["exp"][0])

    assert exp == int(clock.now) + 120


def test_non_ascii_filename_download_header(client: TestClient) -> None:
    file_id = upload(client, filename="报告.txt").json()["id"]
    url = create_link(client, file_id).json()["url"]

    disposition = client.get(url).headers["content-disposition"]

    assert disposition.startswith("attachment; filename*=utf-8''")
    assert "%E6%8A%A5%E5%91%8A.txt" in disposition


def test_html_upload_is_never_served_as_html(client: TestClient) -> None:
    html = b"<script>alert(1)</script>"
    file_id = upload(client, content=html, filename="x.html", content_type="text/html").json()["id"]
    url = create_link(client, file_id).json()["url"]

    response = client.get(url)

    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")


def test_expired_link_returns_410(client: TestClient, clock: FakeClock) -> None:
    _, link = _upload_and_link(client, ttl=60)

    clock.advance(59)
    assert client.get(link["url"]).status_code == 200

    clock.advance(1)
    response = client.get(link["url"])
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "link_expired"


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("sig", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        ("exp", "9999999999"),
        ("lid", "0" * 32),
        ("sig", "%C3%A9%C3%A9"),  # non-ASCII signature must be 403, not 500
        ("exp", "not-a-number"),
    ],
)
def test_tampered_link_returns_403(client: TestClient, param: str, value: str) -> None:
    _, link = _upload_and_link(client)

    response = client.get(_with_param(link["url"], param, value))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_signature"


def test_link_cannot_be_moved_to_another_file(client: TestClient) -> None:
    _, link = _upload_and_link(client)
    other_id = upload(client, content=b"other").json()["id"]
    query = urlsplit(link["url"]).query

    assert client.get(f"/d/{other_id}?{query}").status_code == 403


@pytest.mark.parametrize("query", ["", "lid=x", "sig=x&exp=1"])
def test_missing_parameters_return_403(client: TestClient, query: str) -> None:
    file_id, _ = _upload_and_link(client)

    assert client.get(f"/d/{file_id}?{query}").status_code == 403


def test_link_for_deleted_file_returns_404(client: TestClient) -> None:
    file_id, link = _upload_and_link(client)
    client.delete(f"/v1/files/{file_id}", headers=auth())

    response = client.get(link["url"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


@pytest.mark.parametrize("ttl", [0, -5, 59, 604801])
def test_ttl_out_of_range_is_rejected(client: TestClient, ttl: int) -> None:
    file_id = upload(client).json()["id"]

    response = create_link(client, file_id, ttl=ttl)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_ttl"


@pytest.mark.parametrize("ttl", ["3600", 1.5, True, None])
def test_ttl_must_be_a_strict_integer(client: TestClient, ttl: object) -> None:
    file_id = upload(client).json()["id"]

    response = create_link(client, file_id, ttl=ttl)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_client_cannot_choose_expiry(client: TestClient) -> None:
    file_id = upload(client).json()["id"]

    response = client.post(
        f"/v1/files/{file_id}/links",
        headers=auth(),
        json={"ttl_seconds": 3600, "expires_at": 9999999999},
    )

    assert response.status_code == 422


def test_cannot_sign_other_users_file(client: TestClient) -> None:
    file_id = upload(client).json()["id"]

    response = create_link(client, file_id, key=BOB_KEY)

    assert response.status_code == 404


def test_link_requires_api_key(client: TestClient) -> None:
    file_id = upload(client).json()["id"]

    response = client.post(f"/v1/files/{file_id}/links", json={"ttl_seconds": 3600})

    assert response.status_code == 401


def test_link_generation_records_audit_event(client: TestClient) -> None:
    file_id, link = _upload_and_link(client)

    events = client.get(f"/v1/files/{file_id}/audit-events", headers=auth()).json()["items"]

    created = [e for e in events if e["event_type"] == "link.created"]
    assert len(created) == 1
    assert created[0]["link_id"] == link["link_id"]
    assert created[0]["actor_id"] == "alice"
    assert created[0]["expires_at"] == link["expires_at"]


def test_download_records_audit_event(client: TestClient) -> None:
    file_id, link = _upload_and_link(client)
    client.get(link["url"])

    events = client.get(f"/v1/files/{file_id}/audit-events", headers=auth()).json()["items"]

    assert [e["event_type"] for e in events] == ["file.downloaded", "link.created", "file.uploaded"]
    assert events[0]["link_id"] == link["link_id"]
    assert events[0]["actor_id"] is None


def test_link_is_not_issued_when_audit_write_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_id = upload(client).json()["id"]

    def failing_insert(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "insert_audit_event", failing_insert)
    response = create_link(client, file_id)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "audit_write_failed"
    assert "url" not in response.json()
