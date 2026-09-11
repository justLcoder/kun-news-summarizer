# News summarization backend

The backend retrieves metadata from the Kun.uz Uzbek RSS feed:
https://kun.uz/news/rss?lang=uz

RSS fetching returns one snapshot of titles, source URLs, and timezone-aware
publication times. A separate article integration retrieves full article text.
Neither integration operation tracks new items across runs. PostgreSQL persistence
is available separately; no automatic ingestion, API, AI integration, scheduler,
or frontend is included yet.

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

## PostgreSQL persistence (Milestone 3)

The source-independent `news_backend.db` package contains the `Article` ORM
model, URL configuration, and explicit engine/session factories. No connections
are opened on import. Kun.uz integration objects remain independent of the ORM.
Callers map source data to an Article with `source="kun_uz"`; automatic ingestion
is not implemented.

Install dependencies with `python -m pip install -e ./backend`. Persistence uses
SQLAlchemy 2.x, Psycopg 3 (binary distribution), and Alembic with synchronous
sessions. Docker Engine and Docker Compose are required for the supplied local
PostgreSQL environment. Only PostgreSQL is containerized.

From the repository root, start development PostgreSQL:

```bash
docker compose up -d --wait postgres
export DATABASE_URL='postgresql+psycopg://news:local_news_password@localhost:5432/news'
source .venv/bin/activate
cd backend
alembic upgrade head
```

`backend/.env.example` documents local credentials. It is not loaded automatically.
These credentials are for local development only; deployment must provide its own
DATABASE_URL. Missing/invalid configuration raises a credential-free ValueError.
The development database persists in the Compose named volume. The test service
has separate credentials, port 5433, database `news_test`, and ephemeral storage.
Do not point development tools at the test service.

The initial migration creates `articles`: generated BIGINT `id`, required TEXT
`source`, unique required TEXT `source_url`, nonblank required TEXT `title` and
`content`, required TIMESTAMPTZ `published_at`, and database-defaulted required
TIMESTAMPTZ `created_at`. Primary key, unique, and check constraints have explicit
names. PostgreSQL preserves timestamp instants rather than original offsets.
The ORM rejects naive publication datetimes; direct SQL callers must also supply
aware timestamps. NOT NULL is enforced by PostgreSQL; title/content additionally
reject empty or whitespace-only strings.

An article row records successfully retrieved content, not completion of future
AI processing. Exact URL equality defines duplicates. There is no canonicalization,
upsert, or source table. Database uniqueness protects concurrent inserts.

```python
from news_backend.db.models import Article
from news_backend.db.session import make_engine, make_session_factory

engine = make_engine()  # Or make_engine(explicit_url).
sessions = make_session_factory(engine)
try:
    with sessions.begin() as session:
        session.add(Article(
            source="kun_uz",
            source_url=rss_article.source_url,
            title=fetched_article.title,
            content=fetched_article.content,
            published_at=rss_article.published_at,
        ))
finally:
    engine.dispose()
```

The caller owns transaction boundaries. Short-lived sessions are not shared.
Duplicate inserts surface as IntegrityError; explicitly roll back a manually
managed session before reusing it after failure. Context-managed transactions
roll back on exceptions. No repository or service layer is introduced.

Alembic is the schema-management mechanism; application startup does not create
schemas. Review generated migration code before use. The initial downgrade drops
`articles` and its constraints, deleting its rows. Use downgrades only deliberately.

### Complete test suite

From the repository root:

```bash
docker compose --profile test up -d --wait postgres-test
export TEST_DATABASE_URL='postgresql+psycopg://news_test:local_test_password@localhost:5433/news_test'
.venv/bin/python -m unittest discover -s backend/tests -t backend -v
```

Persistence setup downgrades the dedicated test schema to base, upgrades to head,
checks model/migration agreement, then clears articles before each test. Tests
reject URLs outside the documented local test service. Do not run persistence
suites concurrently against that single database. Missing prerequisites are errors,
not silently skipped tests. The test suite never uses DATABASE_URL for setup.

The existing 16 Kun.uz tests still run independently, without a database:

```bash
.venv/bin/python -m unittest discover -s backend/tests/integrations/kun_uz -v
```

Stop the disposable test database with
`docker compose --profile test stop postgres-test`. Development data remains in
its separate named volume. No custom PostgreSQL process manager is included.
