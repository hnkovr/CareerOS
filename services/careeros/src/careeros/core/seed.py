"""``careeros seed`` — idempotent bootstrap of the single P0 user (demo data lives in the vault)."""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy import select

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import get_settings
from careeros.core.db import dispose_engine, get_sessionmaker
from careeros.core.models import User

app = typer.Typer(help="Seed the database")


async def seed_user(email: str) -> bool:
    """Create the single user if missing. Returns True when created."""
    async with get_sessionmaker()() as session:
        existing = await session.scalar(select(User).where(User.id == SINGLE_USER_ID))
        if existing is not None:
            if existing.email != email:
                existing.email = email
                await session.commit()
            return False
        session.add(User(id=SINGLE_USER_ID, email=email, display_name="CareerOS user"))
        await session.commit()
        return True


@app.command("run")
def run() -> None:
    """Create the single user (idempotent)."""
    settings = get_settings()

    async def _main() -> None:
        try:
            created = await seed_user(settings.user_email)
        finally:
            await dispose_engine()
        print(f"user {settings.user_email}: {'created' if created else 'exists'}")
        print(f"vault: {settings.vault_path} (demo vault: career/examples/demo)")

    asyncio.run(_main())
