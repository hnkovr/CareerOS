"""Reading and writing the saved platform set (GH #25).

A singleton row per user, upserted rather than inserted: two concurrent
`/services set` commands must not leave two rows behind, and the unique index on
user_id is what makes that a database guarantee rather than a hope.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.bot.models import BotPreference


class PreferenceStore:
    def __init__(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        self._session = session
        self._user_id = user_id

    async def get_platforms(self) -> list[str]:
        """The saved set, or an empty list when the user has never set one.

        Empty is meaningful and distinct from "all": callers decide what an unset
        preference means for them, rather than having a default silently baked in
        at the storage layer where it cannot be seen.
        """
        row = await self._row()
        return list(row.platforms) if row else []

    async def set_platforms(self, platforms: list[str]) -> list[str]:
        """Replace the saved set, creating the row on first use."""
        row = await self._row()
        if row is None:
            row = BotPreference(user_id=self._user_id, platforms=list(platforms))
            self._session.add(row)
        else:
            row.platforms = list(platforms)
        await self._session.flush()
        return list(row.platforms)

    async def _row(self) -> BotPreference | None:
        return await self._session.scalar(
            select(BotPreference).where(BotPreference.user_id == self._user_id)
        )
