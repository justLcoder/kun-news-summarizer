# News summarization backend

The backend retrieves metadata from the Kun.uz Uzbek RSS feed:
https://kun.uz/news/rss?lang=uz

RSS fetching returns one snapshot of titles, source URLs, and timezone-aware
publication times. A separate article integration retrieves full article text.
Neither operation tracks new items across runs. No API, database, AI integration,
scheduler, or frontend is included yet.

## Setup and run

Python 3.11 or newer is required. From the repository root:

```bash
python3 -m venv .venv  # Skip if the virtual environment already exists.
source .venv/bin/activate
python -m pip install -e ./backend
python -m news_backend.integrations.kun_uz
```

Article parsing uses BeautifulSoup with Python's built-in `html.parser`;
installation uses setuptools as its build dependency. The RSS command prints JSON with Unicode titles and ISO 8601
timestamps, preserving source timezone offsets and feed order. Network or feed
errors produce a message on stderr and a nonzero exit status. Invalid individual
entries are skipped with warnings; an empty feed returns an empty list.

## Tests

After installation, run from the repository root:

```bash
python -m unittest discover -s backend/tests/integrations/kun_uz -v
```

Tests use a synthetic RSS fixture and mocked network calls; they need no internet.

## Article retrieval

After installing the backend, call the integration directly:

```python
from news_backend.integrations.kun_uz.article import fetch_article

# rss_article is an RssArticle discovered through the RSS integration.
article = fetch_article(rss_article.source_url, timeout=15)
print(article.title)
print(article.content)
```

`FetchedArticle` is a frozen dataclass with `source_url`, `title`, and `content`.
The original input URL is retained. Only HTTPS article URLs on `kun.uz` or
`www.kun.uz`, with `/news/YYYY/MM/DD/slug` paths, are accepted; redirects follow
the same restriction. Responses must be HTML and at most 5 MiB. Network failures
propagate unchanged. Invalid URLs, response types, and oversized responses raise
`ValueError`; unusable article structures raise `ArticleParseError(ValueError)`.

`parse_article(html, source_url=...)` performs no network access. It expects one
`.article-body` inside an `article` element, a headline in the article/header,
and an optional direct header paragraph as the lead. JSON-LD `NewsArticle`
headline/description and Open Graph title/description are limited fallbacks.
Metadata descriptions never replace an empty body.

Some current Kun.uz responses stream body sections into hidden `div` fragments,
linked to `template` placeholders by `$RS("source-id","target-id")` calls.
The parser reads those mappings without executing JavaScript, moves referenced
fragments into placeholder order, and rejects unresolved or repeated fragments.
This is a site-specific assumption that may need updating if Kun.uz changes its
rendering. It does not implement a browser or a general React stream decoder.

Content consists of lead plus body with plain-text block boundaries. Only an
exact whitespace-normalized match to the opening body block removes a duplicate
lead. Inline text is preserved; images, photo captions, scripts, and embeds are
excluded. Navigation and related stories outside the body are not collected.
Video transcription and image text extraction are outside this milestone.

`article_basic.html` and `article_streamed.html` are small, entirely synthetic
fixtures modeled on observed page structures; they contain no copied article
prose. Article tests use these fixtures and mocked transport and run with the
same test command above, together with all RSS tests. The RSS CLI is unchanged.

## Structure and reuse

- `backend/pyproject.toml`: backend package metadata and dependencies.
- `backend/src/news_backend/integrations/kun_uz/models.py`: source metadata dataclass.
- `backend/src/news_backend/integrations/kun_uz/rss.py`: independent fetching and parsing functions.
- `backend/src/news_backend/integrations/kun_uz/article.py`: article fetching, fragment reconstruction, and text extraction.
- `backend/src/news_backend/integrations/kun_uz/__main__.py`: manual diagnostic command.
- `backend/tests/`: integration parsing and transport tests.

Future ingestion workflows can import
`news_backend.integrations.kun_uz.rss.fetch_recent_articles` directly. The
integration returns metadata without coupling to database models or HTTP schemas.
FastAPI routes, persistence, migrations, services, and background entry points
will be added to the backend when implemented. A future `frontend/` directory
will own the web client. Those components are intentionally not scaffolded yet.
