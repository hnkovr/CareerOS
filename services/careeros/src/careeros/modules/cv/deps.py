from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings, get_settings
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.cv.service import CVService
from careeros.modules.vault.deps import get_vault


def build_cv_service(
    settings: Settings | None = None,
    *,
    session: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
) -> CVService:
    settings = settings or get_settings()
    return CVService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user_id),
        session=session,
        user_id=user_id,
    )
