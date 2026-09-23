"""API key authentication.

Clients send "X-API-Key: <key>". Keys are mapped to user ids via the API_KEYS
setting. This dependency is the only place that knows how identity is established,
so it can later be replaced by JWT/OIDC without touching the route handlers.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.deps import get_settings
from app.errors import ApiError

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> str:
    if not api_key:
        raise ApiError(401, "missing_api_key", "Missing X-API-Key header")
    provided = api_key.encode("utf-8")
    user_id: str | None = None
    # Compare against every key in constant time; do not short-circuit on a match.
    for known_key, known_user in settings.api_key_map.items():
        if hmac.compare_digest(known_key.encode("utf-8"), provided):
            user_id = known_user
    if user_id is None:
        raise ApiError(401, "invalid_api_key", "Invalid API key")
    return user_id


CurrentUser = Annotated[str, Depends(get_current_user)]
