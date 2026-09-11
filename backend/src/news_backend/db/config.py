"""Minimal PostgreSQL configuration without import-time environment access."""

import os

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def database_url(value: str | URL | None = None) -> URL:
    value = os.environ.get("DATABASE_URL") if value is None else value
    if not value:
        raise ValueError("DATABASE_URL is required")
    try:
        url = make_url(value)
        valid = (
            url.drivername == "postgresql+psycopg"
            and bool(url.host)
            and bool(url.database)
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except (ValueError, TypeError, ArgumentError):
        # Do not include the original URL or parsing exception in diagnostics.
        raise ValueError("Invalid PostgreSQL database URL") from None
    if not valid:
        raise ValueError("Expected a postgresql+psycopg URL with host and database")
    return url
