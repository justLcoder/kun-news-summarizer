import json
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from news_backend.integrations.daryo_uz.article import (
    MAX_JSON_BYTES,
    ArticleParseError,
    fetch_article,
    parse_article,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = (FIXTURES / "article_basic.json").read_bytes()
MEDIA = (FIXTURES / "article_media.json").read_bytes()
URL = "https://daryo.uz/2026/09/16/birinchi-sintetik-yangilik/"
MEDIA_URL = "https://daryo.uz/2026/09/15/media-sintetik-yangilik/"


class ArticleParsingTests(unittest.TestCase):
    def test_prose_boundaries_and_short_content_is_not_prepended(self):
        article = parse_article(BASIC, source_url=URL)
        self.assertEqual(article.source_url, URL)
        self.assertEqual(article.title, "Sintetik Daryo yangiligi")
        self.assertEqual(
            article.content,
            "Birinchi muhim xabar.\n\nIkkinchi tafsilot.\n\n"
            "“Aniq iqtibos.”\n\nBirinchi band\n\nIkkinchi band\n\n"
            "Kichik sarlavha\n\nYakuniy jumla.",
        )
        self.assertNotIn("qisqa tavsif", article.content)

    def test_embeds_images_figures_and_captions_are_removed(self):
        article = parse_article(MEDIA, source_url=MEDIA_URL)
        self.assertEqual(
            article.content,
            "Media oldidagi xabar.\n\nMedia ortidagi xabar.",
        )
        for excluded in ("Rasm tagidagi", "Tashqi rasm", "qisqa tavsif"):
            self.assertNotIn(excluded, article.content)

    def test_source_specific_rejections(self):
        base = json.loads(BASIC)
        cases = {
            "advertisement": dict(base, category_slug="reklama"),
            "expired": dict(base, expired=True),
            "non-published": dict(base, status="draft"),
            "blank title": dict(base, title="  "),
            "malformed date": dict(base, date="bad"),
            "non-strict date": dict(base, date="2026-9-16 1:02:03"),
            "wrong type": dict(base, type="video"),
            "missing category": dict(base, category_slug=""),
            "missing content": {key: value for key, value in base.items() if key != "content"},
            "blank body": dict(base, content=" <p> </p> "),
            "media-only": dict(base, content='<figure><img src="x"><figcaption>Caption</figcaption></figure>'),
        }
        for name, value in cases.items():
            with self.subTest(name=name), self.assertRaises(ArticleParseError):
                parse_article(json.dumps(value).encode(), source_url=URL)

    def test_malformed_document_and_mismatched_identity_are_rejected(self):
        base = json.loads(BASIC)
        cases = [
            b"not-json",
            b"[]",
            json.dumps(dict(base, slug="different")).encode(),
            json.dumps(dict(base, date="2026-09-15 14:05:06")).encode(),
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises((ArticleParseError, ValueError)):
                parse_article(payload, source_url=URL)


class ArticleFetchTests(unittest.TestCase):
    @patch("news_backend.integrations.daryo_uz.article.urlopen")
    def test_request_contract_and_canonical_result(self, open_url):
        response = open_url.return_value.__enter__.return_value
        response.headers = Message()
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.read.return_value = BASIC

        article = fetch_article(URL, timeout=4)
        self.assertEqual(article.source_url, URL)
        request = open_url.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://data.daryo.uz/api/v1/site/news/birinchi-sintetik-yangilik",
        )
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Accept-language"), "oz")
        self.assertEqual(open_url.call_args.kwargs["timeout"], 4)
        response.read.assert_called_once_with(MAX_JSON_BYTES + 1)

    @patch("news_backend.integrations.daryo_uz.article.urlopen")
    def test_invalid_public_urls_never_fetch(self, open_url):
        invalid = (
            "http://daryo.uz/2026/09/16/example/",
            "https://www.daryo.uz/2026/09/16/example/",
            "https://daryo.uz.evil.test/2026/09/16/example/",
            "https://user@daryo.uz/2026/09/16/example/",
            "https://daryo.uz/2026/02/30/example/",
            "https://daryo.uz/2026/09/16/example",
            "https://daryo.uz/2026/09/16/example/?query=1",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_article(url)
        open_url.assert_not_called()

    @patch("news_backend.integrations.daryo_uz.article.urlopen")
    def test_non_json_and_oversized_responses_are_rejected(self, open_url):
        response = open_url.return_value.__enter__.return_value
        response.headers = Message()
        response.headers["Content-Type"] = "text/html"
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            fetch_article(URL)

        response.headers.replace_header("Content-Type", "application/json")
        response.read.return_value = b"x" * (MAX_JSON_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "exceeds size limit"):
            fetch_article(URL)

    @patch("news_backend.integrations.daryo_uz.article.urlopen")
    def test_network_errors_propagate(self, open_url):
        for error in (URLError("offline"), TimeoutError("timed out")):
            open_url.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)) as raised:
                fetch_article(URL)
            self.assertIs(raised.exception, error)
