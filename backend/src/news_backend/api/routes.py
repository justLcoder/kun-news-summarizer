"""Public read-only routes and request-scoped database sessions."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from .queries import latest_news, summarized_news
from .schemas import HealthResponse, NewsCollection, NewsItem

router = APIRouter()


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/api/v1/news", response_model=NewsCollection)
def news_feed(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NewsCollection:
    return NewsCollection(items=[NewsItem(**row._mapping) for row in latest_news(session, limit=limit)])


@router.get("/api/v1/news/{article_id}", response_model=NewsItem)
def news_detail(
    session: SessionDependency,
    article_id: Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)],
) -> NewsItem:
    row = summarized_news(session, article_id=article_id)
    if row is None:
        raise HTTPException(status_code=404, detail="News not found")
    return NewsItem(**row._mapping)
