"""Builds providers from settings and resolves the default / fallback chain."""

from __future__ import annotations

from careeros.core.config import Settings
from careeros.modules.ai.provider import AIProvider, AIUnavailable
from careeros.modules.ai.schemas import ProviderInfo


class ProviderRegistry:
    def __init__(
        self, providers: dict[str, AIProvider], default: str, fallbacks: list[str]
    ) -> None:
        self._providers = providers
        self.default = default
        self.fallbacks = fallbacks

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderRegistry:
        from careeros.modules.ai.providers.anthropic_provider import AnthropicProvider
        from careeros.modules.ai.providers.openai_compat import OpenAICompatibleProvider

        providers: dict[str, AIProvider] = {
            "anthropic": AnthropicProvider(
                settings.anthropic_api_key.get_secret_value()
                if settings.anthropic_api_key
                else None,
                settings.anthropic_model,
            ),
            "openai": OpenAICompatibleProvider(
                "openai",
                settings.openai_api_key.get_secret_value() if settings.openai_api_key else None,
                settings.openai_base_url,
                settings.openai_model,
                requires_key=True,
            ),
        }
        if settings.env == "test":
            from careeros.modules.ai.providers.fake import FakeProvider

            providers["fake"] = FakeProvider()
        return cls(providers, settings.ai_default_provider, list(settings.ai_fallback_providers))

    def names(self) -> list[str]:
        return list(self._providers)

    def infos(self) -> list[ProviderInfo]:
        return [p.info() for p in self._providers.values()]

    def get(self, name: str | None = None) -> AIProvider:
        key = name or self.default
        try:
            return self._providers[key]
        except KeyError as exc:
            raise AIUnavailable(
                f"unknown AI provider '{key}' (known: {', '.join(self._providers)})"
            ) from exc

    def register(self, provider: AIProvider, *, make_default: bool = False) -> None:
        self._providers[provider.name] = provider
        if make_default:
            self.default = provider.name

    def chain(self, name: str | None = None) -> list[AIProvider]:
        """Requested (or default) provider first, then configured, usable fallbacks."""
        first = self.get(name)
        out = [first]
        for fb in self.fallbacks:
            if fb in self._providers and fb != first.name and self._providers[fb].info().configured:
                out.append(self._providers[fb])
        return out
