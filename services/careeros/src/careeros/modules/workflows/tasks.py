"""Worker task: the daily follow-up sweep (ADR-017). Registered by name; the worker's cron and
the API/CLI all go through the same handler."""

from __future__ import annotations

from typing import Any

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import get_settings
from careeros.core.db import get_sessionmaker
from careeros.core.tasks import registry
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.vault.deps import get_vault
from careeros.modules.workflows.service import WorkflowService


@registry.task("workflows.sweep_follow_ups")
async def sweep_follow_ups(limit: int = 20) -> list[dict[str, Any]]:
    settings = get_settings()
    async with get_sessionmaker(settings)() as session:
        svc = WorkflowService(
            settings,
            get_vault(settings),
            build_ai_service(settings, session=session, user_id=SINGLE_USER_ID),
            session=session,
            user_id=SINGLE_USER_ID,
        )
        runs = await svc.sweep_follow_ups(limit=limit)
        return [{"id": str(r.id), "state": str(r.state), "target": r.target_ref} for r in runs]
