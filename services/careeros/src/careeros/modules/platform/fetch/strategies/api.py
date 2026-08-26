"""Native API strategy: delegates to ``connector.fetch_job_api`` (declared ⇒ implemented)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from careeros.modules.platform.base import ConnectorContext
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.sources import CanonicalSource

if TYPE_CHECKING:
    from careeros.modules.platform.base import BaseConnector


class ApiStrategy:
    name = FetchStrategy.api

    def __init__(self, connector: BaseConnector) -> None:
        self.connector = connector

    async def run(
        self, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
    ) -> FetchArtifact:
        return await self.connector.fetch_job_api(ctx, source)
