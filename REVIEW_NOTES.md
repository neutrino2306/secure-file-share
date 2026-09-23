# Review Notes

Design decisions, trade-offs and deliberate omissions for the technical review.
Context: a 3-hour, single-engineer prototype. Priorities were correctness, a working
end-to-end flow, deployability and explainability, in that order.

## Scope

**Implemented**: API-key authentication, private uploads with size limit and
hashing, signed links with TTL, public download with validation, file listing and
metadata with sharing status, audit events (upload, link creation, download,
delete) with a read endpoint, file deletion, uniform errors, pytest suite, CI with
Docker build and end-to-end smoke test, Dockerfile and compose.

**Deliberately out of scope** (discussed below): real identity provider, key
rotation, link revocation, encryption at rest, malware scanning, rate limiting,
object storage, Postgres, multi-instance deployment, resumable uploads, HTTP Range,
metrics/tracing, CD, HTTPS.

## Key decisions

### 1. Signed links: custom HMAC-SHA256, stateless verification

- Canonical string `v1\n{file_id}\n{link_id}\n{exp}`, HMAC-SHA256 with
  `SIGNING_SECRET`, base64url without padding.
- The signature covers every field that matters, so pointing a link at another
  file or extending its expiry breaks it. Expiry is computed server-side.
- `v1` prefix leaves room to change the format; fixed field count and strict
  format checks (32-hex ids, digits-only expiry) remove canonicalization ambiguity.
- `hmac.compare_digest` on bytes (it raises `TypeError` on non-ASCII `str`, which
  would otherwise turn a bad link into a 500).
- Signature is checked before expiry, so only authentic links learn they are
  expired (410); anything else is 403.
- **Why not JWT**: the format is ~30 lines I can explain byte by byte, with no
  algorithm negotiation (`alg` confusion) and no dependency. Trade-off: not a
  standard format.
- **Stateless vs stored tokens**: verification needs no DB lookup, which scales
  horizontally. Trade-off: an individual link cannot be revoked. Mitigation path:
  every link already has a `link_id` recorded in the audit table; revocation is a
  `revoked_links` table checked after signature verification. Deleting the file
  already invalidates all of its links.

### 2. "Valid after restart"

The secret comes from configuration (no default, min 32 chars, fail fast), and all
state is on disk (SQLite + blob directory, a named volume in Docker). Proven by
`tests/test_restart.py` (new app instance, same config → 200; different secret →
403) and by CI restarting the real container and re-downloading the same link.

### 3. Authentication: static API keys from configuration

`API_KEYS="key:user,..."`, sent as `X-API-Key`. Compared in constant time. Simple,
honest (clients cannot claim an arbitrary user id), and isolated in one dependency
(`auth.py`), so JWT/OIDC can replace it without touching routes.
Trade-off: no key management, hashing or rotation; keys live in env.

### 4. Storage: SQLite + local filesystem

- Blobs are named by server-generated UUIDs; user filenames are sanitized and used
  only for display and `Content-Disposition`. This removes path traversal by design.
- Directory mode 0700, files 0600, container runs as non-root, data directory is
  outside the code tree and never served statically.
- Upload streams to `tmp/`, counts bytes and hashes, `fsync`, then `os.replace`
  (atomic). The DB row is written after the blob; if the insert fails the blob is
  removed. Invariant: a DB row implies the blob exists. The reverse (orphan blob
  after a crash) is a cleanup-job concern.
- `storage.py` is the seam for object storage (DigitalOcean Spaces / S3).
- Trade-off: SQLite is single-node and single-writer; fine here, Postgres for scale.

### 5. Audit

- Append-only table; the code only INSERTs and SELECTs.
- Link creation is **fail closed**: if the audit insert fails, no URL is returned.
- Download audit is **best effort**: logged on failure, the download proceeds.
  Rationale: the requirement is auditing link generation; blocking downloads on an
  audit hiccup would hurt availability for recipients.
- Client IP is `request.client.host`; `X-Forwarded-For` is not trusted without a
  known proxy setup.

### 6. HTTP semantics

- Other users' files → 404 (not 403) to prevent id enumeration.
- Invalid/malformed/missing link parameters → 403; authentic but expired → 410;
  deleted file → 404; blob missing for an active row → 500 (server integrity issue).
- `ttl_seconds` is `StrictInt` (no `"3600"` or `true` coercion), extra fields are
  forbidden (clients cannot send `expires_at`).
- Downloads: `application/octet-stream`, `attachment`, `nosniff`, `no-store`,
  `no-referrer`. The client-declared content type is stored but never served, so an
  uploaded HTML file cannot execute in the browser (stored XSS).
- `PUBLIC_BASE_URL` comes from configuration, never from the Host header.

### 7. Upload size

Two layers: a middleware rejects requests whose `Content-Length` is clearly over the
limit before the body is parsed, and the authoritative check happens while
streaming to disk. Note: Starlette spools the multipart body before the handler
runs, so in production the limit should also be enforced at the reverse proxy.

### 8. Sync endpoints

Endpoints are plain `def` because `sqlite3` and file I/O block; FastAPI runs them in
a threadpool. Using `async def` with blocking calls would stall the event loop.
One SQLite connection per request (`check_same_thread=False` because dependencies
and endpoints may run on different threadpool threads), WAL mode.

### 9. App factory and injectable clock

`create_app(settings, clock)` builds independent instances, which makes the restart
test possible and lets expiry be tested without sleeping. Import has no side
effects; `uvicorn --factory` calls it at startup.

### 10. Logging

uvicorn's access log includes query strings, i.e. link signatures. A logging filter
redacts the query string of `/d/...` requests. Secrets are `SecretStr` and never
logged.

## Environment notes

- The provided container is Ubuntu 26.04 with Python 3.14 (task text says Ubuntu
  24). Python 3.14 is used consistently locally, in CI and in the Docker image.
  Fallback plan if a dependency lacked 3.14 wheels: pin all three to 3.13.
- Plain `uvicorn` (not `uvicorn[standard]`) avoids optional C extensions.
- Docker is not available inside the dev container, so the Dockerfile is verified
  in CI (build, run, smoke test, restart).
- Dependencies use lower bounds; production should use a lock file.

## Deployment

- Target: DigitalOcean Droplet + Docker Compose with a named volume.
- App Platform rejected: its filesystem is ephemeral, so uploads and the SQLite
  database would be lost on redeploy, contradicting the local-storage requirement.
- Deployment is an optional extension; deployability is demonstrated by the image
  build and end-to-end smoke test in CI. CD (push image to DO Container Registry,
  deploy on merge) is a next step.

## Production concerns not implemented (and what I would do)

| Concern | Approach |
|---|---|
| HTTPS | TLS at a reverse proxy (Caddy/Nginx + Let's Encrypt) or a load balancer. Signed URLs are bearer tokens and must not travel in plain text. |
| Rate limiting / brute force | At the proxy or gateway, per IP and per API key. |
| Identity | OIDC/JWT; replace `auth.py`. |
| API key management | Store hashes, scopes, rotation, per-key audit. |
| Signing key rotation | Add `kid` to the URL; sign with the active key, verify with all configured keys. |
| Revocation / one-time links | `links` table keyed by `link_id`, `revoked_at`, atomic download counters. |
| Encryption at rest | Envelope encryption (per-file DEK wrapped by a KMS key) or volume encryption. |
| Malware scanning | Async scan (e.g. ClamAV); block link creation until clean. |
| Horizontal scaling | Postgres + object storage; stateless verification already scales. |
| Large files | Multipart/resumable upload, HTTP Range, CDN, presigned object-store URLs. |
| Lifecycle | Retention policies, quotas, orphan/deleted blob cleanup job. |
| Observability | Structured JSON logs, request ids, Prometheus metrics, tracing, alerts. |
| Audit integrity | Ship events to a separate store/SIEM; hash chaining. |
| Backups | Scheduled snapshots of the volume (SQLite online backup + blobs). |
| Migrations | Alembic once the schema starts evolving. |
