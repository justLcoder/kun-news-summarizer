"""One bounded, sequential summarization run."""
import logging
from dataclasses import dataclass
from openai import OpenAI, APIConnectionError, InternalServerError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from news_backend.db.models import Article, Summary
from news_backend.integrations.openai.summarization import generate_summary, SummaryValidationError

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


def summarize_articles(session_factory: sessionmaker[Session], *, client: OpenAI,
                       model: str, limit: int = 10) -> SummarizationResult:
    if type(limit) is not int or limit <= 0:
        raise ValueError('limit must be a positive integer')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('A model is required')
    with session_factory() as session:
        articles = session.execute(select(Article.id, Article.content).where(
            ~select(Summary.article_id).where(Summary.article_id == Article.id).exists()
        ).order_by(Article.id).limit(limit)).all()
    stored = skipped = 0
    failures = []
    for article_id, content in articles:
        try:
            generated = generate_summary(content, client=client, model=model)
        except (APIConnectionError, InternalServerError):
            failures.append(SummarizationFailure(article_id, 'provider_error'))
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
