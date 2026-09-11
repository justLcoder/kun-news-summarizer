"""Manual diagnostic entry point: python -m news_backend.integrations.kun_uz."""

import json
import logging
import sys
from urllib.error import URLError

from .rss import fetch_recent_articles


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        articles = fetch_recent_articles()
    except (URLError, OSError, ValueError) as exc:
        print(f"Unable to retrieve Kun.uz RSS: {exc}", file=sys.stderr)
        return 1
    print(json.dumps([
        {
            "title": article.title,
            "source_url": article.source_url,
            "published_at": article.published_at.isoformat(),
        }
        for article in articles
    ], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
