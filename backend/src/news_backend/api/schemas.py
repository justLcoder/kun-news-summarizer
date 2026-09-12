"""Stable public response schemas."""

from datetime import datetime, timezone

from pydantic import BaseModel, field_validator


class HealthResponse(BaseModel):
    status: str


class NewsItem(BaseModel):
    id: int
    title: str
    summary: str
    source: str
    source_url: str
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def normalize_publication_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class NewsCollection(BaseModel):
    items: list[NewsItem]
