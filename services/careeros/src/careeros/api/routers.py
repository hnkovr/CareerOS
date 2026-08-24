"""Registry of module routers. Add a module's router here when its slice lands."""

from __future__ import annotations

from fastapi import APIRouter

from careeros.modules.ai.router import router as ai_router
from careeros.modules.cv.router import router as cv_router
from careeros.modules.inbox.router import router as inbox_router
from careeros.modules.opportunities.contacts import router as contacts_router
from careeros.modules.opportunities.router import router as opportunities_router
from careeros.modules.pipeline.router import router as pipeline_router
from careeros.modules.profiles.router import router as profiles_router
from careeros.modules.vault.router import router as vault_router

ROUTERS: list[APIRouter] = [
    vault_router,
    ai_router,
    cv_router,
    opportunities_router,
    profiles_router,
    pipeline_router,
    contacts_router,
    inbox_router,
]
