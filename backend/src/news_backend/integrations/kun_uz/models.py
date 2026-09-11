"""Source data supplied by Kun.uz."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RssArticle:
    title: str
    source_url: str
    published_at: datetime


@dataclass(frozen=True)
class FetchedArticle:
    source_url: str
    title: str
    content: str
