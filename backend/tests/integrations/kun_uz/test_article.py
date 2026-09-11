import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from news_backend.integrations.kun_uz.article import MAX_HTML_BYTES, ArticleParseError, fetch_article, parse_article

URL = "https://kun.uz/news/2026/09/11/example"
FIXTURES = Path(__file__).parent / "fixtures"
BASIC = (FIXTURES / "article_basic.html").read_text(encoding="utf-8")
STREAMED = (FIXTURES / "article_streamed.html").read_text(encoding="utf-8")


class ArticleTests(unittest.TestCase):
    def test_normal_article_and_boundaries(self):
        article = parse_article(BASIC, source_url=URL)
        self.assertEqual(article.source_url, URL)
        self.assertEqual(article.title, "O‘zbekiston & yangiliklar")
        self.assertEqual(article.content, "Qisqa kirish.\n\nBirinchi xabar.\n\nTafsilotlar\n\nBirinchi band\n\nIkkinchi band\n\n“Muhim fikr.”\n\nYakuniy jumla.")

    def test_streamed_order_and_exact_deduplication(self):
        article = parse_article(STREAMED, source_url=URL)
        self.assertEqual(article.content, "Opening.\n\nSecond paragraph.\n\nMiddle.\n\nFinal paragraph.")

    def test_similar_lead_is_preserved(self):
        html = STREAMED.replace("<p>Opening.</p></header>", "<p>Opening!</p></header>")
        self.assertTrue(parse_article(html, source_url=URL).content.startswith("Opening!\n\nOpening."))

    def test_unusable_structures(self):
        cases = [
            BASIC.replace("<h1>O‘zbekiston &amp; yangiliklar</h1>", ""),
            BASIC.replace("article-body", "other"),
            '<article><h1>Title</h1><div class="article-body">  <img src="x"></div></article>',
            STREAMED.replace('$RS("S:5","P:5")', ""),
            STREAMED.replace('id="S:5"', 'id="missing"'),
            STREAMED.replace('<div hidden id="S:5">', '<div id="S:5">'),
            STREAMED.replace('<p>Second paragraph.</p>', '<template id="P:5"></template>'),
        ]
        for html in cases:
            with self.subTest(html=html), self.assertRaises(ArticleParseError):
                parse_article(html, source_url=URL)

    def test_metadata_fallbacks(self):
        html = '<article><div class="article-body"><p>Body.</p></div></article>'
        for metadata in (
            '<script type="application/ld+json">{"@type":"NewsArticle","headline":"Title","description":"Lead."}</script>',
            '<script type="application/ld+json">bad json</script><meta property="og:title" content="Title"><meta property="og:description" content="Lead.">',
        ):
            with self.subTest(metadata=metadata):
                article = parse_article(metadata + html, source_url=URL)
                self.assertEqual(article.title, "Title")
                self.assertEqual(article.content, "Lead.\n\nBody.")


class FetchTests(unittest.TestCase):
    @patch("news_backend.integrations.kun_uz.article.build_opener")
    def test_invalid_urls(self, opener):
        for url in ("http://kun.uz/news/2026/09/11/example", "https://example.com/news/2026/09/11/example", "https://kun.uz.evil.com/news/2026/09/11/example", "file:///tmp/a", "https://kun.uz/news/category/jahon", "https://user@kun.uz/news/2026/09/11/example"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_article(url)
        opener.assert_not_called()

    @patch("news_backend.integrations.kun_uz.article.build_opener")
    def test_fetch_timeout_and_non_html(self, opener):
        response = opener.return_value.open.return_value.__enter__.return_value
        response.headers = Message()
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.read.return_value = BASIC.encode()
        self.assertEqual(fetch_article(URL, timeout=3).title, "O‘zbekiston & yangiliklar")
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 3)
        response.headers.replace_header("Content-Type", "application/json")
        with self.assertRaisesRegex(ValueError, "non-HTML"):
            fetch_article(URL)

    @patch("news_backend.integrations.kun_uz.article.parse_article")
    @patch("news_backend.integrations.kun_uz.article.build_opener")
    def test_oversized_response_is_rejected(self, opener, parser):
        response = opener.return_value.open.return_value.__enter__.return_value
        response.headers = Message()
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.read.return_value = b"x" * (MAX_HTML_BYTES + 1)

        with self.assertRaisesRegex(ValueError, "exceeds size limit"):
            fetch_article(URL)

        response.read.assert_called_once_with(MAX_HTML_BYTES + 1)
        parser.assert_not_called()

    @patch("news_backend.integrations.kun_uz.article.build_opener")
    def test_network_errors_propagate(self, opener):
        for error in (URLError("offline"), TimeoutError("timed out")):
            opener.return_value.open.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)) as raised:
                fetch_article(URL)
            self.assertIs(raised.exception, error)

    @patch("news_backend.integrations.kun_uz.article.build_opener")
    def test_redirect_validation(self, opener):
        fetch_article_url = "https://example.com/news/2026/09/11/example"
        # Use the actual handler supplied to the opener, without a live request.
        opener.return_value.open.side_effect = URLError("offline")
        with self.assertRaises(URLError):
            fetch_article(URL)
        handler = opener.call_args.args[0]
        with self.assertRaises(ValueError):
            handler.redirect_request(None, None, 302, "", {}, fetch_article_url)
