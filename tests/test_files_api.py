import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from tests.helpers import BOB_KEY, MAX_UPLOAD_BYTES, auth, create_link, upload


def test_upload_returns_metadata(client: TestClient) -> None:
    content = b"hello world"

    response = upload(client, content=content, filename="hello.txt")

    assert response.status_code == 201
    body = response.json()
    assert len(body["id"]) == 32
    assert body["filename"] == "hello.txt"
    assert body["size_bytes"] == len(content)
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert body["content_type"] == "text/plain"
    assert body["created_at"].endswith("+00:00")
    assert body["link_count"] == 0
    assert body["last_shared_at"] is None


def test_upload_is_stored_by_id_in_private_dir(client: TestClient, data_dir: Path) -> None:
    body = upload(client, filename="../../etc/passwd").json()

    assert body["filename"] == "passwd"
    blob = data_dir / "blobs" / body["id"]
    assert blob.read_bytes() == b"hello world"
    assert not (data_dir / "passwd").exists()


def test_upload_requires_api_key(client: TestClient) -> None:
    response = client.post("/v1/files", files={"file": ("a.txt", b"x", "text/plain")})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_upload_rejects_invalid_api_key(client: TestClient) -> None:
    response = upload(client, key="not-a-real-key-0000000000")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_upload_without_file_field_is_422(client: TestClient) -> None:
    response = client.post("/v1/files", headers=auth(), data={"other": "x"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_empty_upload_is_rejected(client: TestClient) -> None:
    response = upload(client, content=b"")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"


def test_oversized_upload_is_rejected_while_streaming(client: TestClient, data_dir: Path) -> None:
    # Slightly over the limit: passes the Content-Length fast path, caught while streaming.
    response = upload(client, content=b"x" * (MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert list((data_dir / "blobs").iterdir()) == []
    assert list((data_dir / "tmp").iterdir()) == []


def test_oversized_upload_is_rejected_by_content_length(client: TestClient) -> None:
    # Far over the limit: rejected by the middleware before the body is parsed.
    response = upload(client, content=b"x" * (MAX_UPLOAD_BYTES + 100 * 1024))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_list_only_returns_own_files_newest_first(client: TestClient) -> None:
    first = upload(client, filename="first.txt").json()
    second = upload(client, filename="second.txt").json()
    upload(client, filename="bobs.txt", key=BOB_KEY)

    body = client.get("/v1/files", headers=auth()).json()

    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]


def test_list_pagination(client: TestClient) -> None:
    for i in range(3):
        upload(client, filename=f"{i}.txt")

    body = client.get("/v1/files?limit=2&offset=2", headers=auth()).json()

    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert client.get("/v1/files?limit=0", headers=auth()).status_code == 422


def test_get_own_file(client: TestClient) -> None:
    file_id = upload(client).json()["id"]

    response = client.get(f"/v1/files/{file_id}", headers=auth())

    assert response.status_code == 200
    assert response.json()["id"] == file_id


def test_other_users_file_is_not_found(client: TestClient) -> None:
    file_id = upload(client).json()["id"]

    assert client.get(f"/v1/files/{file_id}", headers=auth(BOB_KEY)).status_code == 404
    assert client.delete(f"/v1/files/{file_id}", headers=auth(BOB_KEY)).status_code == 404
    audit = client.get(f"/v1/files/{file_id}/audit-events", headers=auth(BOB_KEY))
    assert audit.status_code == 404


def test_unknown_file_is_not_found(client: TestClient) -> None:
    response = client.get(f"/v1/files/{'0' * 32}", headers=auth())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


def test_status_reflects_generated_links(client: TestClient) -> None:
    file_id = upload(client).json()["id"]
    create_link(client, file_id)
    create_link(client, file_id)

    body = client.get(f"/v1/files/{file_id}", headers=auth()).json()

    assert body["link_count"] == 2
    assert body["last_shared_at"] is not None


def test_delete_removes_file_and_blob(client: TestClient, data_dir: Path) -> None:
    file_id = upload(client).json()["id"]

    response = client.delete(f"/v1/files/{file_id}", headers=auth())

    assert response.status_code == 204
    assert client.get(f"/v1/files/{file_id}", headers=auth()).status_code == 404
    assert client.get("/v1/files", headers=auth()).json()["total"] == 0
    assert not (data_dir / "blobs" / file_id).exists()
