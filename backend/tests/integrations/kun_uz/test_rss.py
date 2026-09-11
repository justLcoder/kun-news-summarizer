import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from news_backend.integrations.kun_uz.rss import fetch_recent_articles, parse_rss

FIXTURE = Path(__file__).parent / "fixtures" / "rss.xml"


class RssTests(unittest.TestCase):
    def test_metadata_unicode_entities_order_and_timezone(self):
        articles = parse_rss(FIXTURE.read_bytes())
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].title, "O‘zbekiston: ta’lim & yangiliklar")
        self.assertEqual(articles[0].source_url, "https://kun.uz/news/2026/09/10/example")
        self.assertEqual(articles[0].published_at, datetime(2026, 9, 10, 13, 20, tzinfo=timezone.utc))
        self.assertEqual(articles[0].published_at.utcoffset(), timedelta(hours=5))
        self.assertEqual(articles[1].title, "Ikkinchi yangilik")

    def test_invalid_items_are_skipped_with_warning(self):
        valid = "<item><title>Valid</title><link>https://kun.uz/a</link><pubDate>Thu, 10 Sep 2026 18:20:00 +0500</pubDate></item>"
        for invalid in (
            valid.replace("<title>Valid</title>", ""),
            valid.replace("https://kun.uz/a", ""),
            valid.replace("https://kun.uz/a", "/relative"),
            valid.replace("Thu, 10 Sep 2026 18:20:00 +0500", "bad date"),
            valid.replace(" +0500", ""),
        ):
            with self.subTest(invalid=invalid):
                with self.assertLogs("news_backend.integrations.kun_uz.rss", level="WARNING"):
                    articles = parse_rss(f"<rss><channel>{invalid}{valid}</channel></rss>".encode())
                self.assertEqual(len(articles), 1)
                self.assertEqual(articles[0].title, "Valid")

    def test_invalid_documents_raise(self):
        for xml in (b"<rss>", b"<html/>", b"<rss/>"):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                parse_rss(xml)

    def test_empty_feed(self):
        self.assertEqual(parse_rss(b"<rss><channel/></rss>"), [])

    @patch("news_backend.integrations.kun_uz.rss.urlopen")
    def test_fetch_parses_response_and_sets_timeout(self, open_url):
        open_url.return_value.__enter__.return_value.read.return_value = FIXTURE.read_bytes()
        self.assertEqual(len(fetch_recent_articles(timeout=3)), 2)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 3)
        self.assertEqual(open_url.call_args.args[0].full_url, "https://kun.uz/news/rss?lang=uz")

    @patch("news_backend.integrations.kun_uz.rss.urlopen", side_effect=URLError("offline"))
    def test_network_failure_propagates(self, _open_url):
        with self.assertRaises(URLError):
            fetch_recent_articles()
