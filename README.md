# Secure File Sharing Service

A REST API for uploading private files and sharing them through temporary,
cryptographically signed download links.

- Files are stored in a private directory, named by server-generated ids, and
  associated with the uploading user.
- Owners generate links with a TTL. Links are HMAC-SHA256 signed and stay valid
  across service restarts (the secret comes from configuration, state is on disk).
- Anyone holding a valid link can download the file until it expires; tampered,
  expired or deleted-file links are rejected.
- Every link generation is recorded as an audit event; owners can list their files
  (name, size, upload date, sharing status) and read each file's audit trail.

See [docs/architecture.md](docs/architecture.md) for the architecture and request
lifecycle diagrams, and [REVIEW_NOTES.md](REVIEW_NOTES.md) for design decisions and
trade-offs.

## Tech stack

Python 3.14 · FastAPI · SQLite (stdlib `sqlite3`, plain SQL) · local filesystem ·
pytest · ruff · Docker · GitHub Actions.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# Edit .env: set SIGNING_SECRET and API_KEYS (generation commands are in the file).

uvicorn app.main:create_app --factory --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API (click **Authorize** and
enter one of your API keys). Health check: http://localhost:8000/healthz.

The service refuses to start if `SIGNING_SECRET` is missing or shorter than 32
characters, or if `API_KEYS` is malformed.

## Run with Docker

```bash
cp .env.example .env   # then edit the values
docker compose up -d --build
```

Uploaded files and the database live in the `app-data` named volume, so they
survive container restarts and rebuilds. The container runs as a non-root user.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `SIGNING_SECRET` | yes | — | HMAC key for links, at least 32 characters. Changing it invalidates all issued links. |
| `API_KEYS` | yes | — | Comma-separated `api_key:user_id` pairs; each key at least 16 characters. |
| `PUBLIC_BASE_URL` | no | `http://localhost:8000` | Base URL used to build links (never taken from the request Host header). |
| `DATA_DIR` | no | `./data` (`/data` in Docker) | Private directory for blobs and `metadata.db`. |
| `MAX_UPLOAD_BYTES` | no | `52428800` (50 MiB) | Maximum upload size. |
| `MIN_TTL_SECONDS` | no | `60` | Minimum link lifetime. |
| `MAX_TTL_SECONDS` | no | `604800` (7 days) | Maximum link lifetime. |

## API

All `/v1` endpoints require the `X-API-Key` header. Errors always use the shape
`{"error": {"code": "...", "message": "..."}}`.

| Method | Path | Description | Success | Errors |
|---|---|---|---|---|
| `GET` | `/healthz` | Liveness probe | 200 | — |
| `POST` | `/v1/files` | Upload (multipart field `file`) | 201 | 400 empty, 401, 413 too large, 422 |
| `GET` | `/v1/files?limit=&offset=` | List your files, newest first | 200 | 401, 422 |
| `GET` | `/v1/files/{file_id}` | File metadata and sharing status | 200 | 401, 404 |
| `DELETE` | `/v1/files/{file_id}` | Delete a file; its links stop working | 204 | 401, 404 |
| `POST` | `/v1/files/{file_id}/links` | Create a signed link, body `{"ttl_seconds": 3600}` | 201 | 401, 404, 422, 500 |
| `GET` | `/v1/files/{file_id}/audit-events` | Audit trail for a file, newest first | 200 | 401, 404 |
| `GET` | `/d/{file_id}?lid=&exp=&sig=` | Public download via signed link | 200 | 403 invalid, 404 deleted, 410 expired |

Notes:

- A file owned by another user returns **404**, not 403, so ids cannot be probed.
- `ttl_seconds` must be a JSON integer (strings, floats and booleans are rejected)
  within the configured range. The expiry is always computed by the server.
- Downloads are always served as `application/octet-stream` with
  `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` and
  `Cache-Control: private, no-store`.

File metadata example:

```json
{
  "id": "3f2c...e91a",
  "filename": "report.pdf",
  "size_bytes": 48213,
  "sha256": "9b1d...",
  "content_type": "application/pdf",
  "created_at": "2026-09-23T10:15:02.123+00:00",
  "link_count": 2,
  "last_shared_at": "2026-09-23T10:20:44.910+00:00"
}
```

## Demo with curl

```bash
export BASE=http://localhost:8000
export KEY=<one of your API keys>

# Upload
curl -s -H "X-API-Key: $KEY" -F "file=@README.md" $BASE/v1/files
# → note the "id" field
export FILE_ID=<id>

# Create a link valid for 10 minutes
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"ttl_seconds": 600}' $BASE/v1/files/$FILE_ID/links
# → copy "url" and open it in a browser, or:
curl -OJ "<url>"

# Status and audit trail
curl -s -H "X-API-Key: $KEY" $BASE/v1/files
curl -s -H "X-API-Key: $KEY" $BASE/v1/files/$FILE_ID/audit-events
```

Restart the service (or `docker compose restart`) and open the same URL again:
it still works.

An automated version of this flow (plus tamper and auth checks) is in
`scripts/smoke_test.sh`:

```bash
BASE_URL=http://localhost:8000 API_KEY=$KEY bash scripts/smoke_test.sh
```

## Tests

```bash
pytest -v        # unit + API integration tests
ruff check .     # lint
```

What is covered:

- `test_signer.py`: signing round trip, tampering of every field, wrong secret,
  expiry boundary, and malformed input (including non-ASCII) never raising.
- `test_files_api.py`: upload metadata and hashing, private storage by id,
  authentication, empty and oversized uploads, ownership isolation, pagination,
  sharing status, deletion.
- `test_links_and_download.py`: full upload → link → download flow and response
  headers, expiry (410) using an injected clock, tampered/missing parameters (403),
  TTL validation, audit events, and fail-closed behaviour when the audit write fails.
- `test_restart.py`: a link issued by one app instance works in a fresh instance
  with the same configuration, and stops working if the secret changes.
- `test_config.py`, `test_units.py`: fail-fast configuration, filename
  sanitization, storage cleanup and permissions, access-log redaction.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request:

1. **Lint and test**: `ruff check` and `pytest` on Python 3.14.
2. **Docker build and end-to-end smoke test**: builds the image, runs it with a
   named volume, executes `scripts/smoke_test.sh` against the container, restarts
   the container and verifies that the same signed link still downloads.

## Deployment (DigitalOcean Droplet)

App Platform containers have an ephemeral filesystem, which conflicts with the
local-storage requirement, so the target is a Droplet running Docker Compose:

1. Create a Droplet from the Docker 1-Click image and add your SSH key.
2. `git clone` this repository on the Droplet.
3. Create `.env` with a fresh `SIGNING_SECRET`, `API_KEYS`, and
   `PUBLIC_BASE_URL=http://<droplet-ip>`; set `HOST_PORT=80`.
4. `docker compose up -d --build`, then run `scripts/smoke_test.sh` against it.
5. Updates: `git pull && docker compose up -d --build`.

Known limitation: without a domain this serves plain HTTP, so signed URLs are
visible on the network. Production would terminate TLS in front of the service.

## Project layout

```
app/
  main.py           app factory, middleware, router wiring
  config.py         settings with fail-fast validation
  auth.py           X-API-Key → user id
  signer.py         HMAC signing/verification (pure)
  storage.py        private blob storage (streaming, hashing, atomic writes)
  db.py             SQLite schema and queries
  schemas.py        request/response models
  errors.py         uniform error responses
  filenames.py      filename sanitization
  log_redaction.py  keeps link signatures out of access logs
  routes/           files, links, download endpoints
tests/              pytest suite
scripts/            smoke_test.sh
docs/               architecture diagrams
```
