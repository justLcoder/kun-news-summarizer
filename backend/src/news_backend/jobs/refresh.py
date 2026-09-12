"""Run one production refresh: Kun.uz ingestion followed by summarization."""

import argparse
from dataclasses import dataclass
import json
import logging
import time

from news_backend.db.session import make_engine, make_session_factory
from news_backend.integrations.gemini.config import make_client, make_router
from news_backend.services.ingestion import IngestionResult, ingest_kun_uz
from news_backend.services.summarization import (
    SummarizationAlreadyRunning,
    SummarizationResult,
    summarize_articles,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshResult:
    ingestion: IngestionResult
    summarization_status: str
    summarization: SummarizationResult | None

    def __post_init__(self) -> None:
        valid = (
            self.summarization_status == "completed" and self.summarization is not None
        ) or (
            self.summarization_status == "already_running" and self.summarization is None
        )
        if not valid:
            raise ValueError("Invalid refresh result")


def _validate_summary_limit(summary_limit: int) -> None:
    if type(summary_limit) is not int or not 1 <= summary_limit <= 100:
        raise ValueError("summary_limit must be an integer from 1 through 100")


def run_refresh(
    session_factory,
    *,
    generate,
    summary_limit: int = 10,
) -> RefreshResult:
    """Run both stages while preserving each service's failure semantics."""
    _validate_summary_limit(summary_limit)
    started = time.monotonic()
    logger.info("Refresh started")

    ingestion = ingest_kun_uz(session_factory)
    try:
        summarization = summarize_articles(
            session_factory, generate=generate, limit=summary_limit
        )
    except SummarizationAlreadyRunning:
        elapsed = time.monotonic() - started
        logger.warning(
            "Refresh completed: ingestion discovered=%d stored=%d skipped=%d failed=%d; "
            "summarization=already_running elapsed_seconds=%.3f",
            ingestion.discovered,
            ingestion.stored,
            ingestion.skipped,
            ingestion.failed,
            elapsed,
        )
        return RefreshResult(ingestion, "already_running", None)

    elapsed = time.monotonic() - started
    logger.info(
        "Refresh completed: ingestion discovered=%d stored=%d skipped=%d failed=%d; "
        "summarization selected=%d stored=%d skipped=%d failed=%d; "
        "elapsed_seconds=%.3f",
        ingestion.discovered,
        ingestion.stored,
        ingestion.skipped,
        ingestion.failed,
        summarization.selected,
        summarization.stored,
        summarization.skipped,
        summarization.failed,
        elapsed,
    )
    return RefreshResult(ingestion, "completed", summarization)


def run_configured_refresh(*, summary_limit: int = 10) -> RefreshResult:
    """Own one engine, Gemini client, and router for one configured job run."""
    _validate_summary_limit(summary_limit)
    engine = make_engine()
    try:
        session_factory = make_session_factory(engine)
        client = make_client()
        try:
            router = make_router(client=client)
            return run_refresh(
                session_factory,
                generate=router.generate_summary,
                summary_limit=summary_limit,
            )
        finally:
            client.close()
    finally:
        engine.dispose()


def _result_payload(result: RefreshResult) -> dict:
    ingestion = result.ingestion
    payload = {
        "status": "completed",
        "ingestion": {
            "discovered": ingestion.discovered,
            "stored": ingestion.stored,
            "skipped": ingestion.skipped,
            "failed": ingestion.failed,
        },
    }
    if result.summarization_status == "already_running":
        payload["summarization"] = {"status": "already_running"}
    else:
        summarization = result.summarization
        assert summarization is not None
        payload["summarization"] = {
            "status": "completed",
            "selected": summarization.selected,
            "stored": summarization.stored,
            "skipped": summarization.skipped,
            "failed": summarization.failed,
        }
    return payload


def _summary_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer from 1 through 100") from None
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be an integer from 1 through 100")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-limit", type=_summary_limit, default=10)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        result = run_configured_refresh(summary_limit=args.summary_limit)
    except Exception:
        logger.exception("Refresh failed")
        return 1
    print(json.dumps(_result_payload(result), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
