"""P0 auth: single user. If ``CAREEROS_API_TOKEN`` is set, every request must carry it as a Bearer
token; otherwise the API is open (local single-user default). ``CurrentUser`` is the only thing the
rest of the app depends on, so swapping in OAuth/OIDC later touches this file alone.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from careeros.core.config import Settings, get_settings

# Stable UUID for the single P0 user; seeded into the ``user`` table by ``careeros seed``.
SINGLE_USER_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str


def _settings_dep() -> Settings:
    return get_settings()


async def get_current_user(
    request: Request, settings: Annotated[Settings, Depends(_settings_dep)]
) -> CurrentUser:
    token = settings.api_token.get_secret_value() if settings.api_token else None
    if token:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")
    return CurrentUser(id=SINGLE_USER_ID, email=settings.user_email)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
