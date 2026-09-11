"""Kun.uz article retrieval and HTML extraction."""

import json
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup, NavigableString, Tag

from .models import FetchedArticle

MAX_HTML_BYTES = 5 * 1024 * 1024
_INSERTION = re.compile(r'\$RS\(\s*"([^"\\]+)"\s*,\s*"([^"\\]+)"\s*\)')
_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div", "ul", "ol"}


class ArticleParseError(ValueError):
    """The page does not contain usable, complete article text."""


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"kun.uz", "www.kun.uz"}
        or not re.fullmatch(r"/news/\d{4}/\d{2}/\d{2}/[^/]+/?", parsed.path)
        or any(char.isspace() for char in url)
    ):
        raise ValueError("Expected a Kun.uz HTTPS article URL")


class _KunRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _blocks(element: Tag) -> list[str]:
    """Keep block boundaries while leaving inline punctuation and spacing intact."""
    parts = []

    def visit(node):
        if isinstance(node, NavigableString):
            # Comments are NavigableString subclasses, but are not article text.
            if type(node) is NavigableString:
                parts.append(str(node))
        elif isinstance(node, Tag):
            if node.name in {"script", "style", "template", "noscript", "iframe", "figure", "figcaption"}:
                return
            boundary = node.name in _BLOCKS or node.name == "br"
            if boundary:
                parts.append("\n")
            for child in node.children:
                visit(child)
            if boundary:
                parts.append("\n")

    visit(element)
    return [line for part in "".join(parts).split("\n") if (line := _normalize(part))]


def _restore_fragments(soup: BeautifulSoup, body: Tag) -> None:
    mappings = {}
    for script in soup.find_all("script"):
        for source, target in _INSERTION.findall(script.get_text()):
            if target in mappings and mappings[target] != source:
                raise ArticleParseError("Conflicting article fragment mappings")
            mappings[target] = source
    used = set()
    while placeholder := body.find("template"):
        target = placeholder.get("id")
        source = mappings.get(target)
        fragments = soup.find_all(id=source) if source else []
        if not source or source in used or len(fragments) != 1:
            raise ArticleParseError("Unresolved or repeated article fragment")
        fragment = fragments[0]
        if fragment.name != "div" or not fragment.has_attr("hidden") or body in fragment.parents:
            raise ArticleParseError("Malformed article fragment")
        used.add(source)
        children = list(fragment.contents)
        if not children:
            raise ArticleParseError("Empty article fragment")
        for child in children:
            placeholder.insert_before(child.extract())
        placeholder.decompose()
        fragment.decompose()


def _metadata(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "NewsArticle":
            return data
    return {}


def parse_article(html: str, *, source_url: str) -> FetchedArticle:
    """Extract one article without network access or executing page scripts."""
    soup = BeautifulSoup(html, "html.parser")
    bodies = soup.select(".article-body")
    if len(bodies) != 1:
        raise ArticleParseError("Expected one article body")
    body = bodies[0]
    article = body.find_parent("article")
    if article is None:
        raise ArticleParseError("Article body has no enclosing article")
    _restore_fragments(soup, body)
    paragraphs = _blocks(body)
    if not paragraphs:
        raise ArticleParseError("Article body contains no text")

    metadata = _metadata(soup)

    def fallback(key, property_name):
        value = metadata.get(key)
        if isinstance(value, str) and _normalize(value):
            return _normalize(value)
        meta = soup.find("meta", property=property_name)
        return _normalize(meta.get("content", "")) if meta else ""

    header = article.find("header")
    heading = header.find("h1") if header else article.find("h1")
    title = _normalize(heading.get_text()) if heading else ""
    title = title or fallback("headline", "og:title")
    if not title:
        raise ArticleParseError("Article title is missing")
    lead_tag = header.find("p", recursive=False) if header else None
    lead = _normalize(lead_tag.get_text()) if lead_tag else fallback("description", "og:description")
    if lead and lead != paragraphs[0]:
        paragraphs.insert(0, lead)
    return FetchedArticle(source_url=source_url, title=title, content="\n\n".join(paragraphs))


def fetch_article(source_url: str, *, timeout: float = 15.0) -> FetchedArticle:
    """Retrieve a bounded HTML response; network errors propagate to the caller."""
    _validate_url(source_url)
    request = Request(source_url, headers={"User-Agent": "kun-news-backend/0.1", "Accept": "text/html"})
    with build_opener(_KunRedirectHandler()).open(request, timeout=timeout) as response:
        if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Kun.uz returned a non-HTML response")
        raw = response.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError("Kun.uz article response exceeds size limit")
        html = raw.decode(response.headers.get_content_charset() or "utf-8")
    return parse_article(html, source_url=source_url)
