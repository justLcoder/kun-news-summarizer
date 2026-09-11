# News summarization backend

The backend retrieves metadata from the Kun.uz Uzbek RSS feed:
https://kun.uz/news/rss?lang=uz

RSS fetching returns one snapshot of titles, source URLs, and timezone-aware
publication times. A separate article integration retrieves full article text.
Neither integration operation tracks new items across runs. PostgreSQL persistence
is available through an explicit ingestion workflow, and a separate OpenAI workflow
generates and stores summaries. No API, scheduler, or frontend is included yet.

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
The explicit ingestion workflow maps source data to an Article with
`source="kun_uz"`. Scheduled ingestion is not implemented.

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

## One ingestion run (Milestone 4)

After starting development PostgreSQL, setting DATABASE_URL, and applying migrations,
call the workflow manually from Python:

```python
from news_backend.db.session import make_engine, make_session_factory
from news_backend.services.ingestion import ingest_kun_uz

engine = make_engine()
try:
    result = ingest_kun_uz(make_session_factory(engine))
    print(result)
finally:
    engine.dispose()
```

The workflow fetches RSS first, performs one batched URL lookup in a short read
session, closes it, then processes entries in feed order. Stored URLs and repeated
snapshot URLs are skipped. Each new distinct URL is fetched at most once, with no
session or transaction open during HTTP requests. Fetched URLs must match RSS URLs.
Each successful fetch is inserted in its own fresh transaction; `stored` increases
only after commit. Earlier commits survive later failures.

Frozen `IngestionResult` contains `discovered`, `skipped`, `stored`, and a tuple of
frozen `IngestionFailure(source_url, reason)` records. `failed` is the number of
failure records. Every returned result satisfies
`discovered == skipped + stored + failed`. Discovered counts all valid RSS entries,
including repeats; invalid entries already discarded by RSS parsing are excluded.
Repeated URLs count as skipped even when their first fetch failed.

Expected article fetch exceptions are URLError, OSError (including timeouts), and
HTTPException, reported as `network_error`; ValueError (including ArticleParseError
and UnicodeError) is reported as `invalid_article`. These catches surround only
article fetching. RSS failures, initial lookup failures, database failures, URL
contract mismatches, and unexpected programming errors propagate. Fatal errors do
not return a completed result, and a connection failure during commit may leave
that commit's outcome uncertain; a later run rechecks persisted URLs.

An IntegrityError is skipped only for SQLSTATE `23505` together with constraint
`uq_articles_source_url`. It is caught after transaction rollback; the competing
row is not overwritten. Other integrity errors propagate. There are no upserts,
retries, scheduling, parallel fetching, or engine lifecycle changes in the workflow.

Logging uses DEBUG for successful storage, WARNING for expected article failures,
and INFO for completed counts. The workflow itself never prints.

The complete test command above includes ingestion tests with mocked network
functions and real PostgreSQL persistence. Shared guarded database setup lives in
`tests/db/support.py`. A race test commits a competing article through a separate
session during the mocked fetch, verifying real uniqueness handling and continued
processing without threads or live news requests.

## Explicit AI summarization (Milestone 5)

Install updated dependencies and apply `alembic upgrade head` from `backend/`.
Revision 0002 adds `summaries`, preserving articles: article_id is the named primary
key and a named foreign key with ON DELETE CASCADE; content has a nonblank check.
Provider, resolved model, prompt_version, and timezone-aware generated_at are
required. There is no status or history; a row represents successful generation.
Downgrading 0002 removes summaries, not articles.

Set OPENAI_API_KEY and OPENAI_SUMMARY_MODEL explicitly (see `.env.example`, whose
example model is gpt-5-mini). No dotenv loader or import-time client is used.
The official synchronous OpenAI SDK uses Responses with store=False and no tools.
The configured client has a 60-second timeout and max_retries=0. The caller owns
closing both client and engine. Creating a client does not generate a summary.

Manual invocation below makes paid API requests; automated tests never run it:

```python
from news_backend.db.session import make_engine, make_session_factory
from news_backend.integrations.openai.config import make_client, summary_model
from news_backend.services.summarization import summarize_articles

engine = make_engine()
try:
    with make_client() as client:
        result = summarize_articles(make_session_factory(engine), client=client,
                                    model=summary_model(), limit=10)
        print(result)
finally:
    engine.dispose()
```

Prompt version `uz-news-v1` requests one concise paragraph in Uzbek Latin script,
normally 2–3 sentences, preserving main facts, names, numbers, attribution, and
uncertainty. It prohibits invented context, opinions, headings, and filler, and
instructs the model to treat article text as data. Instructions and source content
are separate API fields. Prompt changes require a new version string.

Safeguards: reject blank input or more than 40,000 Unicode characters before the
API call; never truncate. The output budget is 2,048 tokens (including any model
reasoning), and normalized summary text is capped at 2,000 characters. Reject
non-completed, malformed, refused, blank, or oversized output. These checks do not
guarantee factual accuracy or Uzbek fluency. No live quality check has been run.
Model selection is replaceable through the environment; returned model identity
is saved for provenance. Model compatibility and account access remain prerequisites.

The service selects at most limit unsummarized article IDs/content, ordered by ID,
then closes the read session. Calls run sequentially outside database transactions.
Each valid result is committed independently and counted only after commit. Already
summarized articles are excluded even if the model or prompt configuration changes.
Frozen results contain selected, stored, skipped, failures; failed=len(failures).
Completed runs satisfy selected == stored + skipped + failed.

APIConnectionError (including SDK timeouts) and InternalServerError are recorded
as provider_error; SummaryValidationError becomes invalid_generation. Processing
continues after these individual errors. Authentication, permission, bad request,
invalid configuration/model, rate limit/quota, database errors, and programming
errors propagate; there are no workflow retries. Failed articles remain eligible
for later manual runs. Repeated permanent failures can occupy the beginning of a
bounded selection; persistent retry state is intentionally deferred.

Only SQLSTATE 23505 with constraint pk_summaries is treated as a skipped competing
insert, after rollback. The competing row is not overwritten. Overlapping runs can
still spend credits on duplicate API calls; run serially. A failed DB commit after
generation may require another paid generation later. Earlier commits survive a
fatal failure. Stored source content is assumed unchanged; no automatic stale-summary
invalidation is provided.

Logging uses DEBUG for stored IDs, WARNING for failure categories, INFO for completed
counts. Keys, full article text, raw provider responses, and provider exception bodies
are not logged by this workflow. Tests mock provider calls and use the isolated
PostgreSQL service, including upgrade-from-0001 preservation, constraints, cascade,
selection limits, failure handling, and a real competing insert. Cleanup truncates
summaries and articles together. The existing complete-suite command still applies.
