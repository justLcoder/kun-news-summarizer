# News summarization backend

This repository contains the backend for an AI news platform for Uzbekistan.
Kun.uz is the first publisher integration, not the final scope of the product.
The backend currently retrieves metadata from the Kun.uz Uzbek RSS feed:
https://kun.uz/news/rss?lang=uz

RSS fetching returns one snapshot of titles, source URLs, and timezone-aware
publication times. A separate article integration retrieves full article text.
Neither integration operation tracks new items across runs. PostgreSQL persistence
is available through an explicit ingestion workflow, and a callable-based
summarization workflow generates and stores summaries. A read-only FastAPI API
exposes successfully summarized news. Scheduling can be supplied by the deployment
platform; no scheduler or frontend code is included in the application.

## Setup and run

Python 3.11 or newer is required. From the repository root:

```bash
python3 -m venv .venv  # Skip if the virtual environment already exists.
source .venv/bin/activate
python -m pip install -e ./backend
python -m news_backend.integrations.kun_uz
```

## Production deployment

The current private MVP uses a Render FastAPI Web Service, Neon PostgreSQL, a
Render Cron Job, and Gemini. Both Render resources use the same repository and
installed backend package. The API reads summarized news from PostgreSQL; the
separate Cron Job performs publisher ingestion followed by Gemini summarization.

The repository has been validated with Python 3.14.4. Configure both Render
resources with:

```text
PYTHON_VERSION=3.14.4
```

### Render Web Service

Configure the API resource as follows:

| Setting | Value |
| --- | --- |
| Resource type | Web Service |
| Repository | `justLcoder/kun-news-summarizer` |
| Branch | `main` |
| Root directory | `backend` |
| Runtime | Python |
| Build command | `python -m pip install .` |
| Health check path | `/health` |

Use this start command:

```bash
python -m uvicorn news_backend.api.app:create_app --factory \
    --host 0.0.0.0 --port "$PORT"
```

Render supplies `PORT`; do not configure it manually. One Uvicorn worker is
enough for this MVP.

### Neon PostgreSQL and migrations

The application accepts only SQLAlchemy URLs using the Psycopg driver:

```text
postgresql+psycopg://
```

When copying a Neon connection string, change only its URL scheme from
`postgresql://` to `postgresql+psycopg://`. Preserve the encoded credentials,
hostname, database, and query parameters exactly as Neon supplied them.

Use the pooled Neon endpoint, whose hostname includes `-pooler`, for the FastAPI
service. Use the direct Neon endpoint for the refresh job because its PostgreSQL
advisory lock requires a session-persistent connection. Also use the direct
endpoint for Alembic so migrations remain independent of PgBouncer behavior.

Apply migrations explicitly before starting or using the deployed application:

```bash
cd backend
export DATABASE_URL='<DIRECT NEON PSYCOPG URL>'
alembic upgrade head
alembic current
```

Do not add migration execution to application startup, the Render build command,
or refresh startup.

### Render Cron Job

Configure the refresh resource as follows:

| Setting | Value |
| --- | --- |
| Resource type | Cron Job |
| Repository | `justLcoder/kun-news-summarizer` |
| Branch | `main` |
| Root directory | `backend` |
| Runtime | Python |
| Build command | `python -m pip install .` |
| Command | `python -m news_backend.jobs.refresh --summary-limit 10` |
| Schedule | `*/15 * * * *` |

Render evaluates cron schedules in UTC.

### Production environment variables

| Variable | FastAPI Web Service | Refresh Cron Job |
| --- | --- | --- |
| `DATABASE_URL` | Required: pooled Neon Psycopg URL | Required: direct Neon Psycopg URL |
| `GEMINI_API_KEY` | Not required | Required |
| `GEMINI_SUMMARY_MODELS` | Not required | Required |
| `GEMINI_REFERENCE_ARTICLES` | Not required | Required: `/etc/secrets/reference_articles_15.json` |
| `GEMINI_REFERENCE_ANNOTATIONS` | Not required | Required: `/etc/secrets/reference_annotations_15.json` |
| `PYTHON_VERSION` | `3.14.4` | `3.14.4` |
| `PORT` | Supplied by Render | Not required |

`OPENAI_API_KEY`, `OPENAI_SUMMARY_MODEL`, and `TEST_DATABASE_URL` are not
required for this production Gemini/API deployment.

### Private editorial reference files

The evaluated reference corpus remains in these private, gitignored local files:

```text
backend/.local/reference_articles_15.json
backend/.local/reference_annotations_15.json
```

Never commit these raw reference files. For the private MVP, upload them only to
the Render Cron Job as Render Secret Files named:

```text
reference_articles_15.json
reference_annotations_15.json
```

Then configure:

```text
GEMINI_REFERENCE_ARTICLES=/etc/secrets/reference_articles_15.json
GEMINI_REFERENCE_ANNOTATIONS=/etc/secrets/reference_annotations_15.json
```

The FastAPI service does not need either file. This secret-file arrangement is
the private MVP solution; revisit the reference-corpus and legal strategy before
a serious public or commercial launch.

## Read API

The FastAPI application serves public, summarized news without exposing stored raw
article content or internal AI provenance. Run database migrations separately, then
start the application from `backend/`:

```bash
alembic upgrade head
uvicorn news_backend.api.app:create_app --factory
```

Only `DATABASE_URL` is required for API startup. Gemini/OpenAI keys, models, and
reference files are unnecessary because the API never triggers ingestion or
summarization. Importing the application creates no engine or connection. The
default application lifespan creates a lazy SQLAlchemy engine, stores its synchronous
session factory on application state, and disposes the engine at shutdown. A valid
URL whose database is temporarily unreachable can still serve the liveness endpoint;
database routes surface connection failures as server errors. The application does
not probe the database or run migrations during startup.

The initial contract is:

```text
GET /health
GET /api/v1/news?limit=20
GET /api/v1/news/{article_id}
```

`/health` returns `{"status":"ok"}` without opening a database session. The feed
accepts `limit` from 1 through 100 and returns `{"items":[...]}` ordered by
`published_at DESC, id DESC`. Detail and feed both use an inner join and therefore
show only articles with summaries; missing and unsummarized detail IDs return the
same `404 {"detail":"News not found"}` response. Each item contains exactly:

```json
{
  "id": 42,
  "title": "Yangilik sarlavhasi",
  "summary": "Qisqa yangilik mazmuni.",
  "source": "kun_uz",
  "source_url": "https://kun.uz/news/2026/09/12/example",
  "published_at": "2026-09-12T10:55:00Z"
}
```

Publication timestamps are normalized to UTC and serialized as timezone-aware RFC 3339
values. The raw `Article.content` value and summary provider, model, prompt version, and
generation metadata are never selected for public responses. Invalid limits and IDs
return FastAPI's standard 422 response. Pagination, authentication, CORS, and write
endpoints are outside this milestone.

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
- `backend/src/news_backend/db/`: PostgreSQL models, configuration, and session factories.
- `backend/src/news_backend/services/`: explicit ingestion and summarization workflows.
- `backend/src/news_backend/api/`: FastAPI construction, public schemas, routes, and read queries.
- `backend/migrations/`: Alembic schema migrations.
- `backend/tests/`: parser, persistence, service, provider, and API tests.

Ingestion workflows import
`news_backend.integrations.kun_uz.rss.fetch_recent_articles` directly. The
integration returns metadata without coupling to database models or HTTP schemas.
The persistence, service, and API layers build on that integration without changing
its source-specific contract. Background scheduling and the future web frontend
remain intentionally deferred.

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
from functools import partial
from news_backend.integrations.openai.config import make_client, summary_model
from news_backend.integrations.openai.summarization import generate_summary
from news_backend.services.summarization import summarize_articles

engine = make_engine()
try:
    with make_client() as client:
        result = summarize_articles(make_session_factory(engine),
                                    generate=partial(generate_summary, client=client, model=summary_model()), limit=10)
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
through ModelsUnavailable (reported as models_unavailable); SummaryValidationError becomes invalid_generation. Processing
continues after these individual errors. Authentication, permission, bad request,
invalid configuration/model, rate limit/quota, database errors, and programming
errors propagate; there are no workflow retries. Failed articles remain eligible
for later manual runs. Repeated permanent failures can occupy the beginning of a
bounded selection; persistent retry state is intentionally deferred.

Only SQLSTATE 23505 with constraint pk_summaries is treated as a skipped competing
insert, after rollback. The competing row is not overwritten. Production runs are serialized with a PostgreSQL advisory lock; experimental runs
remain explicitly user-controlled. A failed DB commit after
generation may require another paid generation later. Earlier commits survive a
fatal failure. Stored source content is assumed unchanged; no automatic stale-summary
invalidation is provided.

Logging uses DEBUG for stored IDs, WARNING for failure categories, INFO for completed
counts. Keys, full article text, raw provider responses, and provider exception bodies
are not logged by this workflow. Tests mock provider calls and use the isolated
PostgreSQL service, including upgrade-from-0001 preservation, constraints, cascade,
selection limits, failure handling, and a real competing insert. Cleanup truncates
summaries and articles together. The existing complete-suite command still applies.

## Local editorial few-shot experiment

This opt-in runner leaves production `uz-news-v6` and stored summaries unchanged.
Private `.local/reference_articles_15.json` and `.local/reference_annotations_15.json`
are required only when explicitly running it, never by production imports or tests.
Both files must have exactly 15 unique integer IDs with matching sets. Examples
are joined and sorted numerically by ID. Fixed labels and JSON-encoded field values
preserve the entire source text and annotation list order in a stable prefix.
No reference prose belongs in committed files. `.local/` is gitignored.

The runner uses GPT-5-mini, the existing explicit client factory (60-second timeout,
zero retries), Responses plain text, store=False, and cache key
`uz-news-editorial-examples-v1`. Instructions and all demonstrations precede the
changing target title/content. Source blocks are untrusted data. Only final summaries
are requested. No explicit cache breakpoints or pre-counting calls are used.
`truncation="disabled"` lets the API reject oversized input; examples and targets
are never silently truncated. There is no combined character limit. The existing
2,048-output-token budget is reused; incomplete responses are rejected.

With DATABASE_URL and OPENAI_API_KEY exported, run from `backend/`:

```bash
python -m news_backend.experiments.editorial_examples --limit 5
```

This command makes paid API calls. Do not run it as part of automated validation.
`--references-dir` can override the default `.local` directory. The runner selects
newest Kun.uz articles by publication time then ID, excluding reference source URLs
(authoritative even when IDs change) only; reference JSON IDs do not exclude unrelated database rows. It does
not require an absent production summary. A short read session closes before API
calls; nothing is written to the database. Re-running may select the same articles.

Every request prints article ID/title, summary (or validation failure), returned
model, input_tokens, cached_tokens, and output_tokens. Missing usage is reported
as unavailable, never assumed zero. API failures report unavailable usage and abort;
invalid generation reports available usage and continues. Cache reuse is observed
through actual cached_tokens, not guaranteed by the key. Updating references changes
the prefix. Automated tests use synthetic examples, mocked Responses calls, and the
isolated PostgreSQL test service; the complete test command above includes them.

## Correctness and cost safeguards

Production `generate_summary` now requires keyword-only `title` and `content`, both
nonblank. Input is `TITLE:\n<title>\n\nARTICLE:\n<body>`; instructions remain separate
and source material remains untrusted. The input contract is versioned `uz-news-v6`;
the editorial prompt text is otherwise unchanged. Existing summaries are untouched.

Production summarization acquires PostgreSQL session advisory lock
`5428339482444254513` before selection. A dedicated AUTOCOMMIT connection owns it
through the run; there is no long-running database transaction during API calls.
A contending run raises `SummarizationAlreadyRunning` without generation. Finally
releases the lock; an unlock failure invalidates the physical connection rather
than returning it to the pool. The factory must be engine-bound as created by
`make_session_factory`, with capacity for the guard plus working sessions. Use a
direct/session-persistent PostgreSQL connection, not transaction-mode pooling.
The primary-key race handling remains defense in depth. If the lock connection
is lost, PostgreSQL releases its lock; this is not an exactly-once guarantee across
network failures or noncooperating callers. No new schema is required.

For a repeatable experiment, from backend/ with environment configured:

```bash
python -m news_backend.experiments.editorial_examples --article-ids 1 2 3 4 5
```

This preserves supplied order and fails for duplicate/nonpositive/missing IDs,
reference URLs, or non-Kun.uz targets. `--limit` and `--article-ids` are mutually
exclusive. Default selection still chooses the newest eligible five articles.
Reference URLs alone determine reference identity, even after database rebuilds.
The private references, demonstrations, and prompt cache key are unchanged.

The Kun.uz parser additionally supports a whole `.article-body` streamed into a
hidden div with a unique `$RS` mapping to a template inside an article with a header
headline. Missing/ambiguous mappings or non-article destinations still fail visibly.
This reconstructs the observed dayjest layout rather than accepting orphan bodies.

Manually check that page without OpenAI calls, from backend/:

```bash
python - <<'PY'
from news_backend.integrations.kun_uz.article import fetch_article
article = fetch_article('https://kun.uz/news/2026/09/11/navoiydagi-konda-baxtsiz-hodisa-va-fuqarolar-osoyishtaligi-himoyasi-mahalliy-dayjest')
print(article.title)
print(article.content)
PY
```

Deferred: summary history remains out of scope until the production model/prompt is
selected. TODO: choose deployment dependency locking when deployment work starts;
no dependency-management tool is introduced here.

Gemini editorial benchmark (optional experiment)

From `backend/`, install `python -m pip install -e '.[gemini-experiment]'`
(the complete test suite also requires this extra). Set `GEMINI_API_KEY` in your
shell, alongside the existing `DATABASE_URL`. Then explicitly run:

```bash
python -m news_backend.experiments.editorial_examples_gemini \
  --article-ids 30 31 32 33 34 \
  --model gemini-3.8-flash
```

This paid, read-only experiment reuses the OpenAI benchmark's exact instructions,
references, formatting, and selection checks. It uses two user content messages,
implicit caching only, a 2048-token output ceiling, a 60-second timeout, and no
SDK retries or tools. Thinking settings remain at the model default; token
budgets and tokenization are not necessarily identical across providers.
It reports actual response usage, including cache and thinking tokens, with
`unavailable` for absent metrics. Incomplete or blank responses are reported as
validation errors; API errors abort the run. No summaries are persisted.
The Google SDK is also a production dependency used by Gemini summarization and
model routing; the optional extra remains only as an experiment-installation alias.


Production Gemini routing

`google-genai` is now a production dependency; the `gemini-experiment` extra is
retained for installation compatibility. Production uses an externally supplied
15-example editorial bundle, never the old OpenAI prompt or an import from
`experiments`. No reference article text is included in source control. The prompt
version is `uz-editorial-v1-` plus the SHA-256 of the exact instructions and prefix;
changed reference content therefore has different persisted provenance.

Set `GEMINI_API_KEY`, `GEMINI_SUMMARY_MODELS` (comma-separated, explicit priority),
`GEMINI_REFERENCE_ARTICLES`, and `GEMINI_REFERENCE_ANNOTATIONS`. No numeric model
ranking or model availability probes are used. The following explicit invocation
can spend API credits:

```python
from news_backend.db.session import make_engine, make_session_factory
from news_backend.integrations.gemini.config import make_client, make_router
from news_backend.services.summarization import summarize_articles

engine = make_engine()
try:
    with make_client() as client:
        router = make_router(client=client)
        result = summarize_articles(make_session_factory(engine),
                                    generate=router.generate_summary, limit=10)
        print(result)
finally:
    engine.dispose()
```

Reuse this router across runs in a long-lived single worker. Restarts and separate
manual invocations forget cooldowns. The PostgreSQL advisory lock serializes runs
but does not share cooldown state; benchmarks can independently consume quota.
The service now accepts a generation callable and catches only application-level
validation/unavailability failures. The OpenAI generation boundary translates
APIConnectionError/InternalServerError to ModelsUnavailable so later articles
continue. Authentication, permission, bad request, quota and programming errors
remain fatal. Both experimental runners are unchanged. make_router(client=client)
reads configured models and both reference paths once; the caller explicitly
creates and closes the client and reuses the router across runs.

Each eligible model is attempted once per article, with no sleep, probe, or retry.
SDK attempts=1 and timeout=60 seconds prevent stacked retries. Absolute UTC
deadlines use an injectable UTC clock. Minute/unknown quota defaults to 60 seconds;
408/503 and known transient 500/502/504 or transport errors default to 30 seconds.
Valid RetryInfo/Retry-After hints override defaults. Daily quota IDs containing
`PerDay` use the next America/Los_Angeles midnight, including DST; multiple
violations use the daily deadline and any later valid retry hint. A zero allowance
is a configuration error. Unsupported/malformed details do not trigger daily or
project-wide guesses. Explicit structured scope=project/provider without a model
dimension is required for provider-wide cooldown; merely naming a project is not
sufficient. Unrecognized quota IDs remain temporary model-level unknown quotas.
These scope markers are conservative evidence handling, not guaranteed fields in
every Google response; extend recognized structured cases as evidence warrants.

400/401/403/404 and unexpected errors abort. Blocked, malformed, incomplete, or
blank generations fail only that article without model rotation. Unavailability
leaves the article unsummarized (`models_unavailable`); subsequent selected
articles make no calls if every model is still cooling down. Existing result
counts, individual commits, duplicate-race handling, and schema are unchanged.
Only the successful returned model_version is stored with provider=gemini.
Missing model provenance is rejected. Production Gemini rejects article bodies
over 40,000 characters and final stripped summaries over 2,000 characters, with
a 2048-token output cap. These bounds match OpenAI; reference-prefix size is not
part of the article-body bound. Nothing is silently truncated.
Cooldowns describe eligibility, not proven availability. Clock corrections can
shift effective cooldown duration; ambiguous transport failures may still have
incurred generation charges. No factual-accuracy guarantee is implied.

Router/classifier tests use synthetic errors and fake time without PostgreSQL.
Service tests retain the isolated PostgreSQL fixture. No live API calls are used.

## Production refresh job

Run one explicit ingestion-then-summarization refresh from `backend/`:

```bash
python -m news_backend.jobs.refresh
python -m news_backend.jobs.refresh --summary-limit 20
```

Configure `DATABASE_URL`, `GEMINI_API_KEY`, `GEMINI_SUMMARY_MODELS`,
`GEMINI_REFERENCE_ARTICLES`, and `GEMINI_REFERENCE_ANNOTATIONS` first. Apply
database migrations separately with `alembic upgrade head`; the refresh command
does not run migrations.

Ingestion always runs before summarization. When ingestion stores no new articles,
summarization still processes up to 10 unsummarized articles by default; use
`--summary-limit` to select a value from 1 through 100. Expected per-article
failures remain visible in the command's compact JSON result without failing the
whole job. Fatal configuration, provider, database, RSS, and programming failures
produce a nonzero exit status.

If another cooperating process owns the summarization advisory lock, ingestion may
complete and summarization returns an `already_running` successful no-op outcome.
The command is non-interactive and suitable for later cron or container-job use,
but no scheduler is included yet.
