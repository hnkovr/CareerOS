"""Registry of module routers. Add a module's router here when its slice lands."""

from __future__ import annotations

from fastapi import APIRouter

ROUTERS: list[APIRouter] = []
