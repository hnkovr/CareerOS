"""Application settings. Every value comes from the environment (prefix ``CAREEROS_``).

Templates for the variables live in ``config/.env.*.template`` at the repo root; never commit
real values. Secrets are read only here and never logged (see ``redacted_dump``).
"""

from __future__ import annotations

import types
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]


def _accepts_none(annotation: Any) -> bool:
    return get_origin(annotation) in (Union, types.UnionType) and type(None) in get_args(annotation)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAREEROS_", extra="ignore", env_file=None)

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: Any) -> Any:
        """``VAR=`` in a rendered .env means "not set", for every optional field.

        Every value the owner has not filled in yet renders blank out of
        ``config/.env.*.template`` (a non-empty literal there would be a leaked secret). An
        empty string must therefore read as ``None`` — otherwise ``int | None`` fields refuse
        to parse at all (the app could not start with an unclaimed
        ``CAREEROS_TG_OWNER_CHAT_ID``) and ``SecretStr | None`` fields become ``SecretStr("")``,
        a credential that tests truthy but authenticates nothing.
        """
        if not isinstance(data, dict):
            return data

        def optional(name: str) -> bool:
            field = cls.model_fields.get(name)
            return field is not None and _accepts_none(field.annotation)

        blanks = [name for name, value in data.items() if value == "" and optional(name)]
        return {**data, **dict.fromkeys(blanks)} if blanks else data

    # --- runtime ---
    env: Environment = "dev"
    log_level: str = "INFO"
    log_json: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- storage ---
    database_url: str = "postgresql+asyncpg://careeros:careeros@localhost:5432/careeros"
    redis_url: str = "redis://localhost:6379/0"
    generated_dir: Path = Path("generated")

    # --- vault (canonical career data) ---
    vault_path: Path = Path("career/private")
    career_dir: Path = Path("career")  # repo-shipped schemas, templates, prompt library, demo
    vault_git_user_name: str = "CareerOS"
    vault_git_user_email: str = "careeros@localhost"
    vault_auto_push: bool = False

    # --- auth (P0: single user) ---
    api_token: SecretStr | None = None
    user_email: str = "demo@careeros.local"

    # --- AI ---
    ai_default_provider: str = "anthropic"
    ai_fallback_providers: list[str] = Field(default_factory=list)
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5"
    ai_structured_max_retries: int = 2
    ai_store_inputs: bool = True
    ai_inputs_retention_days: int = 90
    # model → (usd per 1M input tokens, usd per 1M output tokens); unknown models record cost=null
    ai_pricing: dict[str, tuple[float, float]] = Field(default_factory=dict)
    # embeddings for semantic search; None → FTS-only
    ai_embeddings_provider: str | None = None
    ai_embeddings_model: str = "text-embedding-3-small"

    # --- platform connectors (ADR-013): OAuth client credentials + optional env-pinned tokens ---
    platform_token_file: Path = Path("generated/platform/tokens.json")
    platform_oauth_redirect_base: str = "http://localhost:8000/api/platform/oauth"
    platform_http_timeout_s: float = 20.0
    platform_user_agent: str = "CareerOS/0.1 (careeros@localhost)"
    hh_client_id: str | None = None
    hh_client_secret: SecretStr | None = None
    hh_access_token: SecretStr | None = None
    hh_refresh_token: SecretStr | None = None
    upwork_client_id: str | None = None
    upwork_client_secret: SecretStr | None = None
    upwork_access_token: SecretStr | None = None
    upwork_refresh_token: SecretStr | None = None

    # --- telegram bot (ADR-012) ---
    tg_enabled: bool = False
    tg_bot_token: SecretStr | None = None
    #: Echoed by Telegram in X-Telegram-Bot-Api-Secret-Token; an unsigned request is 403.
    tg_webhook_secret: SecretStr | None = None
    #: The only chat served. Any other chat gets 200 and no side effect.
    tg_owner_chat_id: int | None = None
    tg_webhook_path: str = "/tg/webhook"
    #: Eligibility key. Unset → this process never contacts Telegram, so a local run
    #: cannot claim the webhook away from the deployed bot.
    tg_public_url: str | None = None
    #: Deliberate takeover of a webhook held by a different live URL.
    tg_webhook_force_claim: bool = False
    tg_notify_min_score: int = 75
    tg_api_base: str = "https://api.telegram.org"
    tg_http_timeout_s: float = 20.0

    # --- tasks ---
    task_runner: Literal["inline", "arq"] = "arq"

    def redacted_dump(self) -> dict[str, object]:
        """Settings for logs/debug endpoints — secrets masked, URLs password-stripped."""
        data = self.model_dump()
        for key, value in list(data.items()):
            if isinstance(value, SecretStr):
                data[key] = "***" if value.get_secret_value() else None
            elif key.endswith("_url") and isinstance(value, str) and "@" in value:
                scheme, _, rest = value.partition("://")
                data[key] = f"{scheme}://***@{rest.rsplit('@', 1)[-1]}"
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
