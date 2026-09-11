"""Fetch and parse the Kun.uz Uzbek RSS feed."""

import logging
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .models import RssArticle

FEED_URL = "https://kun.uz/news/rss?lang=uz"
logger = logging.getLogger(__name__)


def parse_rss(xml: bytes) -> list[RssArticle]:
    """Parse RSS 2.0 in feed order, warning and skipping invalid entries.

    Invalid XML or an unexpected document structure raises ValueError.
    Publication times retain the source's explicit timezone offset.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Kun.uz returned malformed RSS XML") from exc

    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Expected an RSS document with a channel")

    articles = []
    for position, item in enumerate(channel.findall("item"), start=1):
        try:
            title = (item.findtext("title") or "").strip()
            source_url = (item.findtext("link") or "").strip()
            date_text = (item.findtext("pubDate") or "").strip()
            if not title or not source_url or not date_text:
                raise ValueError("missing title, link, or pubDate")
            url = urlsplit(source_url)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("link must be an absolute HTTP(S) URL")
            published_at = parsedate_to_datetime(date_text)
            if published_at.utcoffset() is None:
                raise ValueError("pubDate must include a timezone")
        except (ValueError, TypeError, OverflowError) as exc:
            logger.warning("Skipping Kun.uz RSS item %d: %s", position, exc)
            continue
        articles.append(RssArticle(title, source_url, published_at))
    return articles


def fetch_recent_articles(*, timeout: float = 15.0) -> list[RssArticle]:
    """Fetch one feed snapshot; propagate network and parsing failures.

    No article pages are fetched and no state is retained between calls.
    """
    request = Request(
        FEED_URL,
        headers={"User-Agent": "kun-news-backend/0.1", "Accept": "application/rss+xml, application/xml"},
    )
    with urlopen(request, timeout=timeout) as response:
        return parse_rss(response.read())
