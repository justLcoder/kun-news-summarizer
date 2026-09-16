"""Sequential source ingestion runs, independent of their triggers."""

import logging
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import URLError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from news_backend.db.models import Article
from news_backend.integrations.daryo_uz.article import fetch_article as fetch_daryo_article
from news_backend.integrations.daryo_uz.discovery import (
    fetch_recent_articles as fetch_recent_daryo_articles,
)
from news_backend.integrations.kun_uz.article import fetch_article
from news_backend.integrations.kun_uz.rss import fetch_recent_articles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionFailure:
    source_url: str
    reason: str


@dataclass(frozen=True)
class IngestionResult:
    discovered: int
    skipped: int
    stored: int
    failures: tuple[IngestionFailure, ...]

    @property
    def failed(self) -> int:
        return len(self.failures)


def _ingest_source(
    session_factory: sessionmaker[Session],
    *,
    source: str,
    discover,
    fetch,
    label: str,
) -> IngestionResult:
    """Persist one source snapshot using short, independent transactions."""
    snapshot = discover()
    urls = {item.source_url for item in snapshot}
    existing = set()
    if urls:
        with session_factory() as session:
            query = select(Article.source_url).where(Article.source_url.in_(urls))
            existing = set(session.scalars(query))

    seen = set()
    skipped = stored = 0
    failures = []
    for item in snapshot:
        url = item.source_url
        if url in existing or url in seen:
            skipped += 1
            continue
        seen.add(url)
        try:
            fetched = fetch(url)
        except (URLError, OSError, HTTPException) as exc:
            failures.append(IngestionFailure(url, "network_error"))
            logger.warning("Article fetch failed for %s: %s", url, exc)
            continue
        except ValueError as exc:
            # Includes source-specific parsing and decoding validation errors.
            failures.append(IngestionFailure(url, "invalid_article"))
            logger.warning("Invalid article at %s: %s", url, exc)
            continue
        if fetched.source_url != url:
            raise RuntimeError("Fetched article URL does not match the discovered URL")

        article = Article(source=source, source_url=url, title=fetched.title,
                          content=fetched.content, published_at=item.published_at)
        try:
            with session_factory.begin() as session:
                session.add(article)
        except IntegrityError as exc:
            if (getattr(exc.orig, "sqlstate", None) == "23505"
                    and getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                    == "uq_articles_source_url"):
                skipped += 1
                continue
            raise
        stored += 1
        logger.debug("Stored article %s", url)

    result = IngestionResult(len(snapshot), skipped, stored, tuple(failures))
    logger.info("%s ingestion completed: discovered=%d skipped=%d stored=%d failed=%d",
                label, result.discovered, result.skipped, result.stored, result.failed)
    return result


def ingest_kun_uz(session_factory: sessionmaker[Session]) -> IngestionResult:
    """Commit each new Kun.uz article independently."""
    return _ingest_source(
        session_factory,
        source="kun_uz",
        discover=fetch_recent_articles,
        fetch=fetch_article,
        label="Kun.uz",
    )


def ingest_daryo_uz(session_factory: sessionmaker[Session]) -> IngestionResult:
    """Commit each new Daryo.uz article independently."""
    return _ingest_source(
        session_factory,
        source="daryo_uz",
        discover=fetch_recent_daryo_articles,
        fetch=fetch_daryo_article,
        label="Daryo.uz",
    )
