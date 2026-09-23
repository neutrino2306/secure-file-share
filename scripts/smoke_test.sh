#!/usr/bin/env bash
# End-to-end smoke test against a running instance (local, CI container or a server).
#
# Usage:
#   BASE_URL=http://localhost:8000 API_KEY=<key> bash scripts/smoke_test.sh
# Optional:
#   LINK_OUT=<path>  write the generated signed URL to this file (used by CI to
#                    re-download the same link after a container restart).
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:?API_KEY is required}"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

json_field() {
  python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

echo "==> Waiting for $BASE_URL/healthz"
for i in $(seq 1 30); do
  if curl -fsS "$BASE_URL/healthz" > /dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Service did not become healthy" >&2
    exit 1
  fi
  sleep 1
done

echo "smoke test content $(date +%s)" > "$workdir/upload.txt"

echo "==> Uploading a file"
file_id="$(curl -fsS -H "X-API-Key: $API_KEY" -F "file=@$workdir/upload.txt" \
  "$BASE_URL/v1/files" | json_field id)"
echo "    file_id=$file_id"

echo "==> Creating a signed link"
url="$(curl -fsS -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"ttl_seconds": 600}' "$BASE_URL/v1/files/$file_id/links" | json_field url)"

echo "==> Downloading through the signed link"
curl -fsS -o "$workdir/download.txt" "$url"
cmp "$workdir/upload.txt" "$workdir/download.txt"

echo "==> Checking that a tampered link is rejected"
status="$(curl -s -o /dev/null -w '%{http_code}' "${url}x")"
if [ "$status" != "403" ]; then
  echo "Expected 403 for a tampered link, got $status" >&2
  exit 1
fi

echo "==> Checking that requests without an API key are rejected"
status="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/v1/files")"
if [ "$status" != "401" ]; then
  echo "Expected 401 without API key, got $status" >&2
  exit 1
fi

if [ -n "${LINK_OUT:-}" ]; then
  echo "$url" > "$LINK_OUT"
fi

echo "Smoke test passed"
