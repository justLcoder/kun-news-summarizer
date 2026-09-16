"""Fetch and parse one bounded Daryo.uz Latin-Uzbek news snapshot."""

import json
import logging
import re
from datetime import datetime
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import DiscoveredArticle

DISCOVERY_URL = (
    "https://data.daryo.uz/api/v1/site/news-latest/list"
    "?limit=50&offset=0&order=date%2Bdesc"
)
MAX_JSON_BYTES = 5 * 1024 * 1024
_PUBLISHED_FORMAT = "%Y-%m-%d %H:%M:%S"
_PUBLISHED = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_TASHKENT = ZoneInfo("Asia/Tashkent")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]*")
logger = logging.getLogger(__name__)


def _parse_published_at(value: object) -> datetime:
    if not isinstance(value, str) or _PUBLISHED.fullmatch(value) is None:
        raise ValueError("date must use YYYY-MM-DD HH:MM:SS")
    try:
        return datetime.strptime(value, _PUBLISHED_FORMAT).replace(tzinfo=_TASHKENT)
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD HH:MM:SS") from exc


def _canonical_url(slug: str, published_at: datetime) -> str:
    if not _SLUG.fullmatch(slug):
        raise ValueError("slug must be one URL-safe path segment")
    return f"https://daryo.uz/{published_at:%Y/%m/%d}/{slug}/"


def parse_recent_articles(payload: bytes) -> list[DiscoveredArticle]:
    """Parse one JSON snapshot, warning and skipping unusable entries."""
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Daryo.uz returned malformed JSON") from exc
    if not isinstance(document, dict) or "data" not in document:
        raise ValueError("Expected a Daryo.uz discovery object with data")
    entries = document["data"]
    if not isinstance(entries, list):
        raise ValueError("Expected Daryo.uz discovery data to be a list")

    articles = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            logger.warning("Skipping Daryo.uz item %d: expected an object", position)
            continue
        if entry.get("status") != "publish":
            continue
        try:
            title_value = entry.get("title")
            slug = entry.get("slug")
            category_slug = entry.get("category_slug")
            if not isinstance(category_slug, str) or not category_slug:
                raise ValueError("category_slug is missing")
            if category_slug == "reklama":
                continue
            if not isinstance(title_value, str) or not (title := " ".join(title_value.split())):
                raise ValueError("title is blank")
            if not isinstance(slug, str):
                raise ValueError("slug is missing")
            published_at = _parse_published_at(entry.get("date"))
            source_url = _canonical_url(slug, published_at)
        except ValueError as exc:
            logger.warning("Skipping Daryo.uz item %d: %s", position, exc)
            continue
        articles.append(DiscoveredArticle(title, source_url, published_at))
    return articles


def fetch_recent_articles(*, timeout: float = 15.0) -> list[DiscoveredArticle]:
    """Fetch one recent snapshot; propagate network and document failures."""
    request = Request(
        DISCOVERY_URL,
        headers={
            "User-Agent": "kun-news-backend/0.1",
            "Accept": "application/json",
            "Accept-Language": "oz",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("Daryo.uz discovery returned a non-JSON response")
        payload = response.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("Daryo.uz discovery response exceeds size limit")
    return parse_recent_articles(payload)
