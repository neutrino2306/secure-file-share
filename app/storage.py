"""Private on-disk blob storage.

Layout under DATA_DIR (mode 0700, never served statically):

    blobs/<file_id>   final file content (mode 0600)
    tmp/              in-progress uploads, same filesystem so os.replace is atomic

Blobs are named by server-generated ids only. User-supplied filenames never touch
the filesystem, which rules out path traversal by construction.

This module is the seam for swapping in object storage (e.g. DigitalOcean Spaces).
"""

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.signer import is_valid_id

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024


class FileTooLargeError(Exception):
    """The upload exceeded the configured maximum size."""


class EmptyFileError(Exception):
    """The upload contained zero bytes."""


@dataclass(frozen=True)
class StoredBlob:
    size_bytes: int
    sha256: str


class BlobStorage:
    def __init__(self, data_dir: Path) -> None:
        self._blobs_dir = data_dir / "blobs"
        self._tmp_dir = data_dir / "tmp"
        for directory in (data_dir, self._blobs_dir, self._tmp_dir):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path_for(self, file_id: str) -> Path:
        # Defense in depth: ids are always server-generated hex, but never build a
        # path from anything that does not look like one.
        if not is_valid_id(file_id):
            raise ValueError("invalid file id")
        return self._blobs_dir / file_id

    def save(self, file_id: str, source: BinaryIO, max_bytes: int) -> StoredBlob:
        """Stream source to disk, enforcing max_bytes and computing SHA-256.

        The content is written to a temp file first and atomically renamed into
        place only when complete, so a blob is never observed half-written.
        """
        final_path = self.path_for(file_id)
        hasher = hashlib.sha256()
        size = 0
        # NamedTemporaryFile creates the file with mode 0600.
        tmp = tempfile.NamedTemporaryFile(dir=self._tmp_dir, delete=False)
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                while chunk := source.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLargeError
                    hasher.update(chunk)
                    tmp.write(chunk)
                if size == 0:
                    raise EmptyFileError
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return StoredBlob(size_bytes=size, sha256=hasher.hexdigest())

    def exists(self, file_id: str) -> bool:
        return self.path_for(file_id).is_file()

    def delete(self, file_id: str) -> None:
        try:
            self.path_for(file_id).unlink(missing_ok=True)
        except OSError:
            # The metadata is already marked deleted; an orphaned blob is a
            # cleanup concern, not a correctness one.
            logger.exception("Failed to delete blob %s", file_id)
