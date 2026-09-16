"""Daryo.uz article retrieval and structured content extraction."""

import json
import re
from datetime import datetime
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString, Tag

from .models import FetchedArticle

DETAIL_URL = "https://data.daryo.uz/api/v1/site/news/{slug}"
MAX_JSON_BYTES = 5 * 1024 * 1024
_PUBLISHED_FORMAT = "%Y-%m-%d %H:%M:%S"
_PUBLISHED = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_TASHKENT = ZoneInfo("Asia/Tashkent")
_ARTICLE_PATH = re.compile(
    r"/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"(?P<slug>[A-Za-z0-9][A-Za-z0-9._~-]*)/"
)
_BLOCKS = {"p", "div", "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
_MEDIA = {
    "script", "style", "template", "noscript", "iframe", "figure", "figcaption",
    "img", "picture", "video", "audio", "canvas", "svg", "object", "embed",
}


class ArticleParseError(ValueError):
    """The Daryo.uz response does not contain a usable published article."""


def _public_url_parts(source_url: str) -> tuple[str, str]:
    parsed = urlsplit(source_url)
    match = _ARTICLE_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "daryo.uz"
        or parsed.query
        or parsed.fragment
        or match is None
        or any(char.isspace() for char in source_url)
    ):
        raise ValueError("Expected a canonical Daryo.uz HTTPS article URL")
    date = f"{match['year']}-{match['month']}-{match['day']}"
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Expected a valid date in the Daryo.uz article URL") from exc
    return match["slug"], date


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _is_caption(tag: Tag) -> bool:
    return any(value.startswith("figcaption") for value in tag.get("class", ()))


def _extract_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    parts = []

    def visit(node):
        if isinstance(node, NavigableString):
            if type(node) is NavigableString:
                parts.append(str(node))
            return
        if not isinstance(node, Tag) or node.name in _MEDIA or _is_caption(node):
            return
        boundary = node.name in _BLOCKS or node.name == "br"
        if boundary:
            parts.append("\n")
        for child in node.children:
            visit(child)
        if boundary:
            parts.append("\n")

    for child in soup.children:
        visit(child)
    lines = [_normalize(part) for part in "".join(parts).split("\n")]
    return "\n\n".join(line for line in lines if line)


def _parse_published_at(value: object) -> datetime:
    if not isinstance(value, str) or _PUBLISHED.fullmatch(value) is None:
        raise ArticleParseError("Article date is malformed")
    try:
        return datetime.strptime(value, _PUBLISHED_FORMAT).replace(tzinfo=_TASHKENT)
    except ValueError as exc:
        raise ArticleParseError("Article date is malformed") from exc


def parse_article(payload: bytes, *, source_url: str) -> FetchedArticle:
    """Parse one detail response without making a network request."""
    expected_slug, expected_date = _public_url_parts(source_url)
    try:
        article = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArticleParseError("Daryo.uz returned malformed JSON") from exc
    if not isinstance(article, dict):
        raise ArticleParseError("Expected a Daryo.uz article object")

    if article.get("status") != "publish":
        raise ArticleParseError("Article is not published")
    category_slug = article.get("category_slug")
    if not isinstance(category_slug, str) or not category_slug:
        raise ArticleParseError("Article category is malformed")
    if category_slug == "reklama":
        raise ArticleParseError("Advertising articles are not supported")
    if article.get("expired") is not False:
        raise ArticleParseError("Expired or incomplete articles are not supported")
    if article.get("type") != "news":
        raise ArticleParseError("Unsupported Daryo.uz article type")

    slug = article.get("slug")
    if not isinstance(slug, str) or slug != expected_slug:
        raise ArticleParseError("Article slug does not match its public URL")
    published_at = _parse_published_at(article.get("date"))
    if f"{published_at:%Y-%m-%d}" != expected_date:
        raise ArticleParseError("Article date does not match its public URL")

    title_value = article.get("title")
    if not isinstance(title_value, str) or not (title := _normalize(title_value)):
        raise ArticleParseError("Article title is missing")
    fragment = article.get("content")
    if not isinstance(fragment, str):
        raise ArticleParseError("Article content is malformed")
    content = _extract_text(fragment)
    if not content:
        raise ArticleParseError("Article body contains no text")
    return FetchedArticle(source_url, title, content)


def fetch_article(source_url: str, *, timeout: float = 15.0) -> FetchedArticle:
    """Retrieve one bounded JSON detail response; network errors propagate."""
    slug, _date = _public_url_parts(source_url)
    request = Request(
        DETAIL_URL.format(slug=quote(slug, safe="-._~")),
        headers={
            "User-Agent": "kun-news-backend/0.1",
            "Accept": "application/json",
            "Accept-Language": "oz",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("Daryo.uz article endpoint returned a non-JSON response")
        payload = response.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("Daryo.uz article response exceeds size limit")
    return parse_article(payload, source_url=source_url)
