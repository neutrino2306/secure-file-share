"""Unit tests for small pure helpers: filenames, storage and log redaction."""

import hashlib
import io
import logging
from pathlib import Path

import pytest

from app.filenames import sanitize_filename
from app.log_redaction import RedactSignedUrlQuery
from app.storage import BlobStorage, EmptyFileError, FileTooLargeError

FILE_ID = "f" * 32


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\secret.txt", "secret.txt"),
        ("bad\x00name\n.txt", "badname.txt"),
        ("invoice\u202egpj.exe", "invoicegpj.exe"),
        ("", "unnamed"),
        (None, "unnamed"),
        ("..", "unnamed"),
        ("dir/", "unnamed"),
        ("报告.pdf", "报告.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_long_names() -> None:
    assert len(sanitize_filename("a" * 1000)) == 255


def test_storage_saves_content_with_hash_and_private_permissions(tmp_path: Path) -> None:
    storage = BlobStorage(tmp_path)
    content = b"some content"

    blob = storage.save(FILE_ID, io.BytesIO(content), max_bytes=100)

    assert blob.size_bytes == len(content)
    assert blob.sha256 == hashlib.sha256(content).hexdigest()
    path = storage.path_for(FILE_ID)
    assert path.read_bytes() == content
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "blobs").stat().st_mode & 0o777 == 0o700


def test_storage_rejects_oversized_upload_and_cleans_up(tmp_path: Path) -> None:
    storage = BlobStorage(tmp_path)

    with pytest.raises(FileTooLargeError):
        storage.save(FILE_ID, io.BytesIO(b"x" * 101), max_bytes=100)

    assert not storage.exists(FILE_ID)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_storage_rejects_empty_upload(tmp_path: Path) -> None:
    storage = BlobStorage(tmp_path)

    with pytest.raises(EmptyFileError):
        storage.save(FILE_ID, io.BytesIO(b""), max_bytes=100)

    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize("bad_id", ["../escape", "", "A" * 32, "a" * 31])
def test_storage_refuses_non_id_paths(tmp_path: Path, bad_id: str) -> None:
    with pytest.raises(ValueError):
        BlobStorage(tmp_path).path_for(bad_id)


def test_access_log_redacts_download_signature() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/d/abc?lid=x&exp=1&sig=SECRET", "1.1", 200),
        None,
    )
    RedactSignedUrlQuery().filter(record)
    message = record.getMessage()
    assert "SECRET" not in message
    assert "/d/abc?<redacted>" in message


def test_access_log_keeps_other_paths() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/v1/files?limit=5", "1.1", 200),
        None,
    )
    RedactSignedUrlQuery().filter(record)
    assert "/v1/files?limit=5" in record.getMessage()
