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


class StreamedBodyTests(unittest.TestCase):
    def test_whole_body_stream_and_invalid_destination(self):
        html = (FIXTURES / 'article_streamed_body.html').read_text()
        result = parse_article(html, source_url=URL)
        self.assertEqual(result.title, 'Mahalliy yangiliklar')
        self.assertEqual(result.content, 'Bugungi voqealar.\n\nBirinchi voqea.\n\nYakuniy voqea.')
        for invalid in (html.replace('id="P:8"', 'id="missing"'),
                        html.replace('<article>', '<aside>').replace('</article>', '</aside>'),
                        html.replace('$RS("S:8","P:8")', '')):
            with self.assertRaises(ArticleParseError): parse_article(invalid, source_url=URL)


class StreamedEmptySiblingTests(unittest.TestCase):
    def setUp(self):
        self.html = (FIXTURES / 'article_streamed_empty_sibling.html').read_text()

    def test_empty_sibling_preserves_exact_article_text(self):
        result = parse_article(self.html, source_url=URL)
        self.assertEqual(result.title, 'Synthetic news')
        self.assertEqual(result.content, 'Lead.\n\nOpening.\n\nFinal paragraph.')

    def test_empty_destination_inside_reconstructed_body_rejected(self):
        # Source remains outside article; destination moves into it with S:body.
        html = self.html.replace('<template id="P:empty"></template>', '')
        html = html.replace('<p>Opening.</p>', '<p>Opening.</p><template id="P:empty"></template>')
        with self.assertRaisesRegex(ArticleParseError, 'Empty article fragment'):
            parse_article(html, source_url=URL)

    def test_invalid_empty_fragment_structures(self):
        cases = {
            'duplicate template': self.html.replace('</article>', '<template id="P:empty"></template></article>'),
            'duplicate non-template ID': self.html.replace('</body>', '<span id="P:empty"></span></body>'),
            'duplicate source': self.html.replace('</body>', '<div hidden id="S:empty"></div></body>'),
            'conflicting mapping': self.html.replace('</body>', '<script>$RS("S:other","P:empty")</script></body>'),
            'missing mapping': self.html.replace('$RS("S:empty","P:empty")', ''),
            'missing source': self.html.replace('<div hidden id="S:empty"></div>', ''),
            'wrong element': self.html.replace('<div hidden id="S:empty"></div>', '<span hidden id="S:empty"></span>'),
            'not hidden': self.html.replace('<div hidden id="S:empty">', '<div id="S:empty">'),
            'textless body': self.html.replace('<p>Opening.</p>', '').replace('<p>Final paragraph.</p>', '<img src="synthetic">'),
        }
        for name, html in cases.items():
            with self.subTest(name=name), self.assertRaises(ArticleParseError):
                parse_article(html, source_url=URL)
