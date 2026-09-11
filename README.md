# News summarization backend

The first milestone retrieves metadata from the Kun.uz Uzbek RSS feed:
https://kun.uz/news/rss?lang=uz

It fetches one snapshot and returns titles, source URLs, and timezone-aware
publication times. It does not retrieve article pages or track new items across
runs. No API, database, AI integration, scheduler, or frontend is included yet.

## Setup and run

Python 3.11 or newer is required. From the repository root:

```bash
python3 -m venv .venv  # Skip if the virtual environment already exists.
source .venv/bin/activate
python -m pip install -e ./backend
python -m news_backend.integrations.kun_uz
```

The backend has no third-party runtime dependencies. Installation uses setuptools
as its build dependency. The command prints JSON with Unicode titles and ISO 8601
timestamps, preserving source timezone offsets and feed order. Network or feed
errors produce a message on stderr and a nonzero exit status. Invalid individual
entries are skipped with warnings; an empty feed returns an empty list.

## Tests

After installation, run from the repository root:

```bash
python -m unittest discover -s backend/tests/integrations/kun_uz -v
```

Tests use a synthetic RSS fixture and mocked network calls; they need no internet.

## Structure and reuse

- `backend/pyproject.toml`: backend package metadata and dependencies.
- `backend/src/news_backend/integrations/kun_uz/models.py`: source metadata dataclass.
- `backend/src/news_backend/integrations/kun_uz/rss.py`: independent fetching and parsing functions.
- `backend/src/news_backend/integrations/kun_uz/__main__.py`: manual diagnostic command.
- `backend/tests/`: integration parsing and transport tests.

Future ingestion workflows can import
`news_backend.integrations.kun_uz.rss.fetch_recent_articles` directly. The
integration returns metadata without coupling to database models or HTTP schemas.
FastAPI routes, persistence, migrations, services, and background entry points
will be added to the backend when implemented. A future `frontend/` directory
will own the web client. Those components are intentionally not scaffolded yet.
