"""``careeros-api`` console entrypoint."""

from __future__ import annotations

import uvicorn

from careeros.api.app import create_app
from careeros.core.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "careeros.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "dev",
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
