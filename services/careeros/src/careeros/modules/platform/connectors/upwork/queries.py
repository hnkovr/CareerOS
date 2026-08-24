"""GraphQL documents sent to ``https://api.upwork.com/graphql``.

Field names were checked against the official reference
(https://www.upwork.com/developer/documentation/graphql/api/docs/index.html). Whatever could
not be confirmed offline is listed in the ``# VERIFY LIVE`` comment above its document;
``careeros platform doctor upwork`` reports which *root* fields exist on the live schema.
"""

from __future__ import annotations

GRAPHQL_URL = "https://api.upwork.com/graphql"

# Root fields the documents below depend on → which document uses them (doctor output order).
ROOT_FIELDS: dict[str, str] = {
    "user": "USER_INFO / FREELANCER_PROFILE",
    "marketplaceJobPostingsSearch": "JOB_SEARCH",
    "vendorProposals": "PROPOSALS",
}

# VERIFY LIVE: nothing — ``user { id nid rid name email }`` is the reference's own example.
USER_INFO = """query CareerOSUserInfo {
  user { id nid rid name email }
}
"""

# VERIFY LIVE: skills.edges.node field names (prettyName / preferredLabel), whether ``project``
# is one object or a list, personalData.profileUrl, user.ciphertext as the public profile key.
FREELANCER_PROFILE = """query CareerOSFreelancerProfile {
  user {
    id
    nid
    name
    ciphertext
    freelancerProfile {
      fullName
      personalData {
        title
        description
        profileUrl
        chargeRate { rawValue currency displayValue }
      }
      skills { edges { node { prettyName preferredLabel } } }
      availability { name capacity availabilityDateTime }
      project { id title description projectUrl }
    }
  }
}
"""

# VERIFY LIVE: client.location.country (MarketPlaceJobSearchLocation), publishedDateTime and
# durationLabel on search nodes, ``type`` enum spelling (HOURLY / FIXED), max ``first``.
JOB_SEARCH = """query CareerOSJobSearch($filter: MarketplaceJobPostingsSearchFilter) {
  marketplaceJobPostingsSearch(
    marketPlaceJobFilter: $filter
    searchType: USER_JOBS_SEARCH
    sortAttributes: [{ field: RECENCY }]
  ) {
    totalCount
    edges {
      node {
        id
        title
        description
        ciphertext
        createdDateTime
        publishedDateTime
        type
        hourlyBudgetType
        hourlyBudgetMin
        hourlyBudgetMax
        amount { rawValue currency displayValue }
        skills { name prettyName }
        client { totalHires totalFeedback location { country } }
        totalApplicants
        durationLabel
      }
    }
  }
}
"""

# VERIFY LIVE: marketplaceJobPosting.clientCompanyPublic.name, sortOrder DESC, pageInfo field
# names, and that ``status_eq`` takes exactly one status per call (the reference types it as a
# single required enum — hence one query per status in client.py).
PROPOSALS = """query CareerOSProposals(
  $filter: VendorProposalFilter!
  $sortAttribute: VendorProposalSortAttribute!
  $pagination: Pagination!
) {
  vendorProposals(filter: $filter, sortAttribute: $sortAttribute, pagination: $pagination) {
    totalCount
    edges {
      node {
        id
        status { status }
        viewedByClient
        auditDetails { createdDateTime modifiedDateTime }
        marketplaceJobPosting {
          id
          content { title }
          clientCompanyPublic { id name }
        }
        terms { chargeRate { displayValue } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# VERIFY LIVE: whether schema introspection is enabled for third-party API keys at all.
INTROSPECT_QUERY_FIELDS = """{ __type(name: "Query") { fields { name } } }"""
