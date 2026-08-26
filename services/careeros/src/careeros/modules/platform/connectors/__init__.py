"""One package per platform. Each ``<name>/connector.py`` exposes ``Connector(BaseConnector)``."""

CONNECTOR_MODULES: tuple[str, ...] = (
    "hh",
    "upwork",
    "linkedin",
    "wellfound",
    "indeed",
    "getmatch",
    "toptal",
    "rockethunt",
    "justjoin",
    "generic",  # Platform.website — the fallback provider, always last
)
