"""Metadata supplied by the Kun.uz RSS feed."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RssArticle:
    title: str
    source_url: str
    published_at: datetime
