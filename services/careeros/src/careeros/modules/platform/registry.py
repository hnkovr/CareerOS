"""Connector discovery and the capabilities matrix (ADR-004)."""

from __future__ import annotations

import importlib
from collections.abc import Iterable

from careeros.modules.platform.base import METHOD_IMPL, BaseConnector, PlatformError
from careeros.modules.platform.enums import PLATFORMS, SyncKind
from careeros.modules.platform.schemas import Capabilities
from careeros.modules.vault.enums import Platform


class UnknownPlatform(PlatformError):
    pass


class PlatformRegistry:
    def __init__(self, connectors: Iterable[BaseConnector]) -> None:
        self._by_platform: dict[Platform, BaseConnector] = {}
        for c in connectors:
            if c.platform in self._by_platform:
                raise ValueError(f"duplicate connector for {c.platform}")
            self._by_platform[c.platform] = c

    @classmethod
    def default(cls) -> PlatformRegistry:
        """Import ``connectors/<name>/connector.py:Connector`` for every listed module."""
        from careeros.modules.platform.connectors import CONNECTOR_MODULES

        connectors: list[BaseConnector] = []
        for name in CONNECTOR_MODULES:
            module = importlib.import_module(
                f"careeros.modules.platform.connectors.{name}.connector"
            )
            connectors.append(module.Connector())
        return cls(connectors)

    def get(self, platform: Platform | str) -> BaseConnector:
        try:
            key = Platform(platform)
        except ValueError as exc:
            raise UnknownPlatform(str(platform)) from exc
        try:
            return self._by_platform[key]
        except KeyError as exc:
            raise UnknownPlatform(str(platform)) from exc

    def all(self) -> list[BaseConnector]:
        ordered = [self._by_platform[p] for p in PLATFORMS if p in self._by_platform]
        extra = [c for p, c in self._by_platform.items() if p not in PLATFORMS]
        return ordered + extra

    def platforms(self) -> list[Platform]:
        return [c.platform for c in self.all()]

    def capabilities(self) -> list[Capabilities]:
        return [c.capabilities for c in self.all()]

    def verify(self) -> list[str]:
        """Declared capability ⇒ overridden method; platform fields consistent. Empty = OK."""
        problems: list[str] = []
        for c in self.all():
            if c.capabilities.platform != c.platform:
                problems.append(f"{c.platform}: capabilities.platform mismatch")
            for kind in SyncKind:
                for method in c.capabilities.methods(kind):
                    attr = METHOD_IMPL[(kind, method)]
                    if getattr(type(c), attr) is getattr(BaseConnector, attr):
                        problems.append(f"{c.platform}: declares {kind}/{method} but no {attr}()")
            if c.capabilities.auth != "none" and c.capabilities.official_api is False:
                problems.append(f"{c.platform}: auth={c.capabilities.auth} but official_api=False")
        return problems


_default: PlatformRegistry | None = None


def get_registry() -> PlatformRegistry:
    global _default
    if _default is None:
        _default = PlatformRegistry.default()
    return _default


def reset_registry() -> None:
    global _default
    _default = None
