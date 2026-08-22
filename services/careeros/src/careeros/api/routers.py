"""Registry of module routers. Add a module's router here when its slice lands."""

from __future__ import annotations

from fastapi import APIRouter

from careeros.modules.vault.router import router as vault_router

ROUTERS: list[APIRouter] = [vault_router]
