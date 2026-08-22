"""Wiring: one provider registry + prompt registry per process; AIService per request/session."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings, get_settings
from careeros.modules.ai.prompts import PromptRegistry
from careeros.modules.ai.registry import ProviderRegistry
from careeros.modules.ai.service import AIService

_providers: dict[int, ProviderRegistry] = {}
_prompts: dict[str, PromptRegistry] = {}


def get_provider_registry(settings: Settings | None = None) -> ProviderRegistry:
    settings = settings or get_settings()
    key = id(settings)
    if key not in _providers:
        _providers[key] = ProviderRegistry.from_settings(settings)
    return _providers[key]


def get_prompt_registry(settings: Settings | None = None) -> PromptRegistry:
    settings = settings or get_settings()
    library = Path(settings.career_dir) / "prompts"
    vault_prompts = Path(settings.vault_path) / "prompts"
    key = f"{library}|{vault_prompts}"
    if key not in _prompts:
        _prompts[key] = PromptRegistry(library, vault_prompts)
    return _prompts[key]


def build_ai_service(
    settings: Settings | None = None,
    *,
    session: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
) -> AIService:
    settings = settings or get_settings()
    return AIService(
        settings,
        get_provider_registry(settings),
        get_prompt_registry(settings),
        session=session,
        user_id=user_id,
    )


def reset_ai_caches() -> None:
    _providers.clear()
    _prompts.clear()
