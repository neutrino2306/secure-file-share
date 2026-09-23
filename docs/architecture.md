# Architecture

A single FastAPI process with two pieces of local state: a SQLite database for
metadata and audit events, and a private directory for file content. There is no
other infrastructure.

## Components

```mermaid
flowchart LR
    Owner["Owner client<br/>(X-API-Key)"]
    Recipient["Link recipient<br/>(no account)"]

    subgraph Service["FastAPI service (single process)"]
        MW["Middleware<br/>Content-Length guard"]
        Auth["auth.py<br/>API key → user id"]
        Files["routes/files.py<br/>upload · list · get · delete · audit"]
        Links["routes/links.py<br/>create signed link"]
        Download["routes/download.py<br/>public download"]
        Signer["signer.py<br/>HMAC-SHA256 (pure)"]
        Storage["storage.py<br/>temp file + atomic rename"]
        DB["db.py<br/>parameterized SQL"]
    end

    Config[("Environment / .env<br/>SIGNING_SECRET · API_KEYS")]
    SQLite[("DATA_DIR/metadata.db<br/>files · audit_events")]
    Blobs[("DATA_DIR/blobs/{file_id}<br/>mode 0600, not public")]

    Owner --> MW --> Auth
    Auth --> Files
    Auth --> Links
    Recipient --> Download
    Links --> Signer
    Download --> Signer
    Files --> Storage
    Download --> Storage
    Files --> DB
    Links --> DB
    Download --> DB
    DB --> SQLite
    Storage --> Blobs
    Config -.-> Signer
    Config -.-> Auth
```

Everything that must survive a restart lives outside the process: the signing
secret comes from configuration, metadata is in SQLite, and content is on disk.
That is why a link signed before a restart still verifies after it.

## Request lifecycle: upload

```mermaid
sequenceDiagram
    autonumber
    participant C as Owner
    participant API as FastAPI
    participant S as storage.py
    participant D as SQLite

    C->>API: POST /v1/files (multipart, X-API-Key)
    API->>API: Content-Length > limit? → 413
    API->>API: Resolve API key → owner_id (401 if missing/invalid)
    API->>S: save(new uuid, stream, max_bytes)
    S->>S: write chunks to tmp/, count bytes, SHA-256
    alt over limit / empty
        S-->>API: FileTooLarge / EmptyFile (tmp file removed)
        API-->>C: 413 / 400
    end
    S->>S: fsync + os.replace → blobs/{file_id}
    API->>D: One transaction: INSERT files + INSERT audit(file.uploaded)
    alt DB error
        API->>S: delete blob (keep "row ⇒ blob exists")
        API-->>C: 500
    end
    API-->>C: 201 FileMetadata
```

## Request lifecycle: create signed link

```mermaid
sequenceDiagram
    autonumber
    participant C as Owner
    participant API as FastAPI
    participant G as signer.py
    participant D as SQLite

    C->>API: POST /v1/files/{id}/links {"ttl_seconds": N}
    API->>API: Authenticate → owner_id
    API->>D: SELECT file WHERE id=? AND owner_id=? AND not deleted
    alt not found or not owner
        API-->>C: 404 (no distinction, prevents enumeration)
    end
    API->>API: Validate N in [MIN_TTL, MAX_TTL] (422)
    API->>API: exp = now + N, lid = random 128-bit
    API->>G: sign(secret, v1 | file_id | lid | exp)
    API->>D: INSERT audit(link.created, lid, exp)
    alt audit write fails
        API-->>C: 500, no URL issued (fail closed)
    end
    API-->>C: 201 {url, link_id, expires_at}
```

## Request lifecycle: public download

```mermaid
sequenceDiagram
    autonumber
    participant R as Recipient
    participant API as FastAPI
    participant G as signer.py
    participant D as SQLite
    participant S as storage.py

    R->>API: GET /d/{file_id}?lid&exp&sig (no auth)
    API->>G: verify(secret, file_id, lid, exp, sig, now)
    G->>G: format checks → recompute HMAC → compare_digest
    alt malformed or signature mismatch
        API-->>R: 403 invalid_signature
    else authentic but now >= exp
        API-->>R: 410 link_expired
    end
    API->>D: SELECT file WHERE id=? AND not deleted
    alt deleted
        API-->>R: 404
    end
    API->>D: INSERT audit(file.downloaded) (best effort)
    API->>S: open blobs/{file_id}
    API-->>R: 200 application/octet-stream,<br/>Content-Disposition: attachment, nosniff, no-store
```

Signature verification needs no database lookup (stateless). The database is only
consulted afterwards to make sure the file has not been deleted.
