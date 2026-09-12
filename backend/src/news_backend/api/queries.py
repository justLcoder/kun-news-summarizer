"""Small, explicit projections for the public news API."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from news_backend.db.models import Article, Summary


def _public_news_statement():
    return select(
        Article.id,
        Article.title,
        Summary.content.label("summary"),
        Article.source,
        Article.source_url,
        Article.published_at,
    ).join(Summary, Summary.article_id == Article.id)


def latest_news(session: Session, *, limit: int):
    return session.execute(
        _public_news_statement()
        .order_by(Article.published_at.desc(), Article.id.desc())
        .limit(limit)
    ).all()


def summarized_news(session: Session, *, article_id: int):
    return session.execute(
        _public_news_statement().where(Article.id == article_id)
    ).one_or_none()
