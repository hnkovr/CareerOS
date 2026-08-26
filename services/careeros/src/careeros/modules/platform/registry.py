"""Connector discovery and the capabilities matrix (ADR-004)."""

from __future__ import annotations

import importlib
from collections.abc import Iterable

from careeros.modules.platform.base import (
    METHOD_IMPL,
    READ_IMPL,
    READ_REQUIRED,
    BaseConnector,
    PlatformError,
)
from careeros.modules.platform.enums import (
    PLATFORMS,
    PUBLIC_READ_STRATEGIES,
    AccessMode,
    SyncKind,
)
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
        """Declared capability ⇒ overridden method; platform fields consistent. Empty = OK.

        Job reads (ADR-015): ``read_job`` declared ⇒ ``detect`` (or ``detect_hosts``) and
        ``extract_job`` overridden; ``api`` ⇒ ``fetch_job_api``; a public-page strategy ⇒
        ``access == public``.
        """
        problems: list[str] = []
        for c in self.all():
            if c.capabilities.platform != c.platform:
                problems.append(f"{c.platform}: capabilities.platform mismatch")
            for kind in SyncKind:
                for method in c.capabilities.methods(kind):
                    attr = METHOD_IMPL[(kind, method)]
                    if not _overridden(c, attr):
                        problems.append(f"{c.platform}: declares {kind}/{method} but no {attr}()")
            if c.capabilities.auth != "none" and c.capabilities.official_api is False:
                problems.append(f"{c.platform}: auth={c.capabilities.auth} but official_api=False")
            read_job = c.capabilities.read_job
            if read_job:
                for attr in READ_REQUIRED:
                    if attr == "detect" and c.detect_hosts:
                        continue
                    if not _overridden(c, attr):
                        problems.append(f"{c.platform}: declares read_job but no {attr}()")
                for strategy, attr in READ_IMPL.items():
                    if strategy in read_job and not _overridden(c, attr):
                        problems.append(
                            f"{c.platform}: declares read_job/{strategy} but no {attr}()"
                        )
                public = [s for s in read_job if s in PUBLIC_READ_STRATEGIES]
                if public and c.capabilities.access != AccessMode.public:
                    problems.append(
                        f"{c.platform}: read_job has {', '.join(str(s) for s in public)} "
                        f"but access={c.capabilities.access} (must be public)"
                    )
        return problems


def _overridden(connector: BaseConnector, attr: str) -> bool:
    return getattr(type(connector), attr) is not getattr(BaseConnector, attr)


_default: PlatformRegistry | None = None


def get_registry() -> PlatformRegistry:
    global _default
    if _default is None:
        _default = PlatformRegistry.default()
    return _default


def reset_registry() -> None:
    global _default
    _default = None
