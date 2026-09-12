"""One bounded, sequential summarization run."""
import logging
from dataclasses import dataclass
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from news_backend.db.models import Article, Summary
from news_backend.summarization import SummaryValidationError, ModelsUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummarizationFailure:
    article_id: int
    reason: str


@dataclass(frozen=True)
class SummarizationResult:
    selected: int
    stored: int
    skipped: int
    failures: tuple[SummarizationFailure, ...]

    @property
    def failed(self) -> int:
        return len(self.failures)


def _summarize_articles(session_factory: sessionmaker[Session], *, generate, limit: int = 10) -> SummarizationResult:
    if type(limit) is not int or limit <= 0:
        raise ValueError('limit must be a positive integer')
    with session_factory() as session:
        articles = session.execute(select(Article.id, Article.title, Article.content).where(
            ~select(Summary.article_id).where(Summary.article_id == Article.id).exists()
        ).order_by(Article.id).limit(limit)).all()
    stored = skipped = 0
    failures = []
    for article_id, title, content in articles:
        try:
            generated = generate(title=title, content=content)
        except ModelsUnavailable:
            failures.append(SummarizationFailure(article_id, 'models_unavailable'))
            logger.warning('Transient summary provider failure for article %d', article_id)
            continue
        except SummaryValidationError:
            failures.append(SummarizationFailure(article_id, 'invalid_generation'))
            logger.warning('Invalid summary generation for article %d', article_id)
            continue
        try:
            with session_factory.begin() as session:
                session.add(Summary(article_id=article_id, content=generated.content,
                                    provider=generated.provider, model=generated.model,
                                    prompt_version=generated.prompt_version, generated_at=generated.generated_at))
        except IntegrityError as exc:
            if (getattr(exc.orig, 'sqlstate', None) == '23505'
                    and getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None) == 'pk_summaries'):
                skipped += 1
                continue
            raise
        stored += 1
        logger.debug('Stored summary for article %d', article_id)
    result = SummarizationResult(len(articles), stored, skipped, tuple(failures))
    logger.info('Summarization completed: selected=%d stored=%d skipped=%d failed=%d',
                result.selected, result.stored, result.skipped, result.failed)
    return result


# Fixed signed BIGINT key, scoped to this application's production summarization.
SUMMARIZATION_LOCK_KEY = 5428339482444254513


class SummarizationAlreadyRunning(RuntimeError):
    """Another production summarization run owns the database advisory lock."""


def summarize_articles(session_factory: sessionmaker[Session], *, generate, limit: int = 10) -> SummarizationResult:
    engine = session_factory.kw['bind']
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as guard:
        acquired = guard.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': SUMMARIZATION_LOCK_KEY})
        if not acquired:
            raise SummarizationAlreadyRunning('Production summarization is already running')
        try:
            return _summarize_articles(session_factory, generate=generate, limit=limit)
        finally:
            try:
                guard.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': SUMMARIZATION_LOCK_KEY})
            except BaseException:
                # Never return a potentially locked physical connection to the pool.
                guard.invalidate()
                raise
