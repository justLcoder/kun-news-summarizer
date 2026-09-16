import json
import unittest
from datetime import timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from news_backend.integrations.daryo_uz.discovery import (
    DISCOVERY_URL,
    MAX_JSON_BYTES,
    fetch_recent_articles,
    parse_recent_articles,
)

FIXTURES = Path(__file__).parent / "fixtures"
DISCOVERY = (FIXTURES / "discovery.json").read_bytes()


class DiscoveryParsingTests(unittest.TestCase):
    def test_multiple_entries_timezone_case_and_canonical_urls(self):
        articles = parse_recent_articles(DISCOVERY)
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].title, "Birinchi sintetik yangilik")
        self.assertEqual(
            articles[0].source_url,
            "https://daryo.uz/2026/09/16/birinchi-sintetik-yangilik/",
        )
        self.assertEqual(articles[0].published_at.utcoffset(), timedelta(hours=5))
        self.assertEqual(getattr(articles[0].published_at.tzinfo, "key", None), "Asia/Tashkent")
        self.assertEqual(
            articles[1].source_url,
            "https://daryo.uz/2026/09/16/SyntheticCASE/",
        )

    def test_duplicate_slug_is_preserved_for_ingestion_accounting(self):
        document = json.loads(DISCOVERY)
        document["data"].append(dict(document["data"][0]))
        articles = parse_recent_articles(json.dumps(document).encode())
        self.assertEqual(len(articles), 3)
        self.assertEqual(articles[0].source_url, articles[2].source_url)

    def test_advertising_and_nonpublished_entries_are_skipped(self):
        document = json.loads(DISCOVERY)
        advertisement = dict(document["data"][0], category_slug="reklama")
        draft = dict(document["data"][0], slug="draft", status="draft")
        document["data"] = [advertisement, draft, document["data"][0]]
        self.assertEqual(len(parse_recent_articles(json.dumps(document).encode())), 1)

    def test_invalid_entries_are_skipped_without_losing_valid_entries(self):
        document = json.loads(DISCOVERY)
        blank = dict(document["data"][0], title="  ", slug="blank")
        malformed_date = dict(document["data"][0], date="16 September", slug="bad-date")
        loose_date = dict(document["data"][0], date="2026-9-16 1:02:03", slug="loose-date")
        malformed_slug = dict(document["data"][0], slug="wrong/path")
        missing_category = dict(document["data"][0], category_slug="", slug="no-category")
        document["data"] = [
            blank,
            malformed_date,
            loose_date,
            malformed_slug,
            missing_category,
            document["data"][0],
        ]
        with self.assertLogs(
            "news_backend.integrations.daryo_uz.discovery", level="WARNING"
        ):
            articles = parse_recent_articles(json.dumps(document).encode())
        self.assertEqual([item.title for item in articles], ["Birinchi sintetik yangilik"])

    def test_malformed_document_shapes_raise(self):
        for payload in (
            b"not-json",
            b"[]",
            b'{}',
            b'{"data": {}}',
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_recent_articles(payload)

    def test_unknown_top_level_metadata_is_forward_compatible(self):
        document = json.loads(DISCOVERY)
        document["total"] = 2
        self.assertEqual(len(parse_recent_articles(json.dumps(document).encode())), 2)


class DiscoveryFetchTests(unittest.TestCase):
    @patch("news_backend.integrations.daryo_uz.discovery.urlopen")
    def test_request_contract_timeout_and_parsing(self, open_url):
        response = open_url.return_value.__enter__.return_value
        response.headers = Message()
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.read.return_value = DISCOVERY

        self.assertEqual(len(fetch_recent_articles(timeout=3)), 2)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, DISCOVERY_URL)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 3)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Accept-language"), "oz")
        response.read.assert_called_once_with(MAX_JSON_BYTES + 1)

    @patch("news_backend.integrations.daryo_uz.discovery.urlopen")
    def test_non_json_and_oversized_responses_are_rejected(self, open_url):
        response = open_url.return_value.__enter__.return_value
        response.headers = Message()
        response.headers["Content-Type"] = "text/html"
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            fetch_recent_articles()

        response.headers.replace_header("Content-Type", "application/json")
        response.read.return_value = b"x" * (MAX_JSON_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "exceeds size limit"):
            fetch_recent_articles()

    @patch(
        "news_backend.integrations.daryo_uz.discovery.urlopen",
        side_effect=URLError("offline"),
    )
    def test_network_failure_propagates(self, _open_url):
        with self.assertRaises(URLError):
            fetch_recent_articles()
