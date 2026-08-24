"""Thin GraphQL client for the Upwork API: one POST per document, typed errors, no persistence.

Tokens never originate here — the connector passes ``auth_headers(ctx)`` in (ADR-005/011).
"""

from __future__ import annotations

from typing import Any

from careeros.core.logging import get_logger
from careeros.modules.platform.base import ConnectorContext, UpstreamError
from careeros.modules.platform.connectors.upwork import queries
from careeros.modules.platform.connectors.upwork.queries import GRAPHQL_URL
from careeros.modules.platform.http import request_json
from careeros.modules.platform.schemas import JobQuery
from careeros.modules.vault.enums import Platform

__all__ = [
    "GRAPHQL_URL",
    "PROPOSAL_STATUS_FILTERS",
    "UpworkClient",
    "graphql",
    "job_filter",
]

log = get_logger(__name__)

PLATFORM = Platform.upwork

# ``VendorProposalFilter.status_eq`` is a single required enum → one call per status.
# ``Pending`` (creation temporarily failed) is skipped on purpose.  # VERIFY LIVE
PROPOSAL_STATUS_FILTERS: tuple[str, ...] = (
    "Accepted",
    "Activated",
    "Offered",
    "Hired",
    "Declined",
    "Withdrawn",
    "Archived",
)
PROPOSAL_PAGE_SIZE = 50
PROPOSAL_MAX_PAGES = 3

_JOB_TYPES = {"hourly": "HOURLY", "fixed": "FIXED", "fixed-price": "FIXED", "fixed_price": "FIXED"}


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nodes(connection: dict[str, Any]) -> list[dict[str, Any]]:
    edges = connection.get("edges")
    if not isinstance(edges, list):
        return []
    out: list[dict[str, Any]] = []
    for edge in edges:
        node = _obj(edge).get("node")
        if isinstance(node, dict):
            out.append(node)
    return out


async def graphql(
    ctx: ConnectorContext,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST one document and return its ``data``; a non-empty ``errors`` array → ``UpstreamError``.

    GraphQL servers answer validation/permission problems with HTTP 200 + ``errors``; HTTP-level
    failures (401 → ``NotConnected``, 5xx/429 retries) are handled by ``request_json``.
    """
    body = await request_json(
        ctx.http,
        "POST",
        GRAPHQL_URL,
        platform=PLATFORM,
        json={"query": query, "variables": variables or {}},
        headers=headers or {},
    )
    if not isinstance(body, dict):
        raise UpstreamError(PLATFORM, 200, "GraphQL response is not a JSON object")
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        message = first.get("message") if isinstance(first, dict) else None
        raise UpstreamError(PLATFORM, 200, str(message or first)[:300])
    data = body.get("data")
    if not isinstance(data, dict):
        raise UpstreamError(PLATFORM, 200, "GraphQL response without data")
    return data


def job_filter(query: JobQuery) -> dict[str, Any]:
    """``JobQuery`` → ``MarketplaceJobPostingsSearchFilter`` using documented keys only.

    ``text`` → ``searchExpression_eq``; ``extra`` knobs: ``title``, ``skills``, ``category_ids``,
    ``job_type`` (hourly|fixed), ``verified_payment_only``. ``posted_since`` has no filter
    counterpart and is applied client-side by the connector; ``salary_min`` is not mapped
    (``IntRange`` shape unconfirmed).
    """
    flt: dict[str, Any] = {}
    if query.text:
        flt["searchExpression_eq"] = query.text
    extra = query.extra
    if extra.get("title"):
        flt["titleExpression_eq"] = str(extra["title"])
    if extra.get("skills"):
        flt["skillExpression_eq"] = str(extra["skills"])
    ids = extra.get("category_ids")
    if isinstance(ids, list | tuple) and ids:
        flt["categoryIds_any"] = [str(i) for i in ids]
    job_type = _JOB_TYPES.get(str(extra.get("job_type") or "").lower())
    if job_type:
        flt["jobType_eq"] = job_type
    if extra.get("verified_payment_only") is True:
        flt["verifiedPaymentOnly_eq"] = True
    if query.location:
        flt["locations_any"] = [query.location]
    flt["pagination_eq"] = {"after": "0", "first": query.limit}
    return flt


class UpworkClient:
    """Bound to one ``ConnectorContext``; ``headers`` carry the bearer token from the connector."""

    def __init__(self, ctx: ConnectorContext, headers: dict[str, str] | None = None) -> None:
        self.ctx = ctx
        self.headers = dict(headers or {})

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return await graphql(self.ctx, query, variables, headers=self.headers)

    async def user_info(self) -> dict[str, Any]:
        data = await self.graphql(queries.USER_INFO)
        return _obj(data.get("user"))

    async def freelancer_profile(self) -> dict[str, Any]:
        """The current user with its nested ``freelancerProfile`` (may be ``None`` for clients)."""
        data = await self.graphql(queries.FREELANCER_PROFILE)
        return _obj(data.get("user"))

    async def search_jobs(self, query: JobQuery) -> list[dict[str, Any]]:
        data = await self.graphql(queries.JOB_SEARCH, {"filter": job_filter(query)})
        return _nodes(_obj(data.get("marketplaceJobPostingsSearch")))

    async def proposals(
        self, statuses: tuple[str, ...] = PROPOSAL_STATUS_FILTERS
    ) -> list[dict[str, Any]]:
        """Proposal nodes across all status filters, de-duplicated by id.

        A status whose query fails is logged and skipped so one unaccepted enum value does not
        blank the whole sync; ``UpstreamError`` is raised only when every status fails.
        """
        nodes: dict[str, dict[str, Any]] = {}
        failures: list[UpstreamError] = []
        for status in statuses:
            try:
                for node in await self._proposals_for(status):
                    nodes.setdefault(str(node.get("id") or id(node)), node)
            except UpstreamError as exc:
                failures.append(exc)
                log.warning(
                    "platform.upwork_proposals_status_failed", status=status, detail=exc.detail
                )
        if failures and len(failures) == len(statuses):
            raise failures[0]
        return list(nodes.values())

    async def _proposals_for(self, status: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(PROPOSAL_MAX_PAGES):
            pagination: dict[str, Any] = {"first": PROPOSAL_PAGE_SIZE}
            if after:
                pagination["after"] = after
            data = await self.graphql(
                queries.PROPOSALS,
                {
                    "filter": {"status_eq": status},
                    "sortAttribute": {"field": "CREATEDDATETIME", "sortOrder": "DESC"},
                    "pagination": pagination,
                },
            )
            connection = _obj(data.get("vendorProposals"))
            out.extend(_nodes(connection))
            page = _obj(connection.get("pageInfo"))
            cursor = page.get("endCursor")
            after = str(cursor) if page.get("hasNextPage") and cursor else None
            if not after:
                break
        return out

    async def introspect_query_fields(self) -> list[str]:
        data = await self.graphql(queries.INTROSPECT_QUERY_FIELDS)
        fields = _obj(data.get("__type")).get("fields")
        if not isinstance(fields, list):
            return []
        return [str(f["name"]) for f in fields if isinstance(f, dict) and f.get("name")]
