from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import URLError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from news_backend.db.models import Article
from news_backend.integrations.daryo_uz.models import (
    DiscoveredArticle as DaryoArticle,
    FetchedArticle as DaryoFetchedArticle,
)
from news_backend.integrations.kun_uz.models import RssArticle, FetchedArticle
from news_backend.services.ingestion import ingest_daryo_uz, ingest_kun_uz
from tests.db.support import PostgresTestCase


class IngestionTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.rss_patch = patch('news_backend.services.ingestion.fetch_recent_articles')
        self.fetch_patch = patch('news_backend.services.ingestion.fetch_article')
        self.rss = self.rss_patch.start()
        self.fetch = self.fetch_patch.start()
        self.addCleanup(self.rss_patch.stop)
        self.addCleanup(self.fetch_patch.stop)
        self.fetch.side_effect = lambda url: FetchedArticle(url, 'Fetched title', 'Fetched content')

    def item(self, suffix='one'):
        return RssArticle('RSS title', 'https://kun.uz/news/2026/09/11/' + suffix,
                          datetime(2026, 9, 11, tzinfo=timezone.utc))

    def insert(self, item, title='Competitor'):
        with self.factory.begin() as session:
            session.add(Article(source='kun_uz', source_url=item.source_url, title=title,
                                content='Original content', published_at=item.published_at))

    def counts(self, result, expected):
        self.assertEqual((result.discovered, result.skipped, result.stored, result.failed), expected)
        self.assertEqual(result.discovered, result.skipped + result.stored + result.failed)

    def test_empty(self):
        self.rss.return_value = []
        factory = Mock(side_effect=AssertionError('No database expected'))
        self.counts(ingest_kun_uz(factory), (0, 0, 0, 0))
        factory.assert_not_called()
        self.fetch.assert_not_called()

    def test_existing(self):
        item = self.item()
        self.insert(item)
        self.rss.return_value = [item]
        self.counts(ingest_kun_uz(self.factory), (1, 1, 0, 0))
        self.fetch.assert_not_called()

    def test_mapping_repeated_and_second_run(self):
        item = self.item()
        self.rss.return_value = [item, item]
        self.counts(ingest_kun_uz(self.factory), (2, 1, 1, 0))
        self.fetch.assert_called_once_with(item.source_url)
        with self.factory() as session:
            row = session.scalar(select(Article))
            self.assertEqual((row.source, row.source_url, row.title, row.content, row.published_at),
                             ('kun_uz', item.source_url, 'Fetched title', 'Fetched content', item.published_at))
        self.fetch.reset_mock()
        self.counts(ingest_kun_uz(self.factory), (2, 2, 0, 0))
        self.fetch.assert_not_called()

    def test_failures_continue_without_retrying_repeats(self):
        first, second, third = self.item(), self.item('two'), self.item('three')
        self.rss.return_value = [first, first, second, third]
        self.fetch.side_effect = [URLError('offline'), ValueError('invalid HTML'),
                                 FetchedArticle(third.source_url, 'Title', 'Content')]
        with self.assertLogs('news_backend.services.ingestion', level='WARNING'):
            result = ingest_kun_uz(self.factory)
        self.counts(result, (4, 1, 1, 2))
        self.assertEqual([(f.source_url, f.reason) for f in result.failures],
                         [(first.source_url, 'network_error'), (second.source_url, 'invalid_article')])
        self.assertEqual(self.fetch.call_count, 3)

    def test_rss_failure_opens_no_session(self):
        self.rss.side_effect = URLError('offline')
        factory = Mock()
        with self.assertRaises(URLError):
            ingest_kun_uz(factory)
        factory.assert_not_called()
        self.fetch.assert_not_called()

    def test_initial_lookup_failure(self):
        self.rss.return_value = [self.item()]
        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=Mock(
            scalars=Mock(side_effect=OperationalError('query', {}, Exception('offline')))))
        factory.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaises(OperationalError):
            ingest_kun_uz(factory)
        self.fetch.assert_not_called()

    def test_integrity_failure_preserves_earlier_commit(self):
        first, second = self.item(), self.item('two')
        self.rss.return_value = [first, second]
        self.fetch.side_effect = [FetchedArticle(first.source_url, 'Good', 'Content'),
                                 FetchedArticle(second.source_url, '', 'Content')]
        with self.assertRaises(IntegrityError):
            ingest_kun_uz(self.factory)
        with self.factory() as session:
            self.assertEqual(list(session.scalars(select(Article.source_url))), [first.source_url])

    def test_real_duplicate_race_preserves_competitor_and_continues(self):
        first, second = self.item(), self.item('two')
        self.rss.return_value = [first, second]
        def fetch(url):
            if url == first.source_url:
                self.insert(first)
            return FetchedArticle(url, 'Fetched title', 'Fetched content')
        self.fetch.side_effect = fetch
        self.counts(ingest_kun_uz(self.factory), (2, 1, 1, 0))
        with self.factory() as session:
            rows = {row.source_url: row for row in session.scalars(select(Article))}
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[first.source_url].title, 'Competitor')
            self.assertEqual(rows[first.source_url].content, 'Original content')

    def test_contract_mismatch_and_programming_errors_propagate(self):
        self.rss.return_value = [self.item()]
        self.fetch.side_effect = None
        self.fetch.return_value = FetchedArticle('different', 'Title', 'Content')
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            ingest_kun_uz(self.factory)
        self.fetch.side_effect = TypeError('bug')
        with self.assertRaises(TypeError):
            ingest_kun_uz(self.factory)

    def test_network_has_no_checked_out_connection(self):
        item = self.item()
        def rss():
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return [item]
        def fetch(url):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return FetchedArticle(url, 'Title', 'Content')
        self.rss.side_effect = rss
        self.fetch.side_effect = fetch
        self.counts(ingest_kun_uz(self.factory), (1, 0, 1, 0))


class DaryoIngestionTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.discovery_patch = patch(
            "news_backend.services.ingestion.fetch_recent_daryo_articles"
        )
        self.fetch_patch = patch(
            "news_backend.services.ingestion.fetch_daryo_article"
        )
        self.discovery = self.discovery_patch.start()
        self.fetch = self.fetch_patch.start()
        self.addCleanup(self.discovery_patch.stop)
        self.addCleanup(self.fetch_patch.stop)
        self.fetch.side_effect = lambda url: DaryoFetchedArticle(
            url, "Fetched Daryo title", "Fetched Daryo content"
        )

    def item(self, suffix="one"):
        return DaryoArticle(
            "Discovered title",
            f"https://daryo.uz/2026/09/16/{suffix}/",
            datetime(2026, 9, 16, 9, 30, tzinfo=timezone.utc),
        )

    def insert(self, item, title="Competitor"):
        with self.factory.begin() as session:
            session.add(
                Article(
                    source="daryo_uz",
                    source_url=item.source_url,
                    title=title,
                    content="Original content",
                    published_at=item.published_at,
                )
            )

    def counts(self, result, expected):
        self.assertEqual(
            (result.discovered, result.skipped, result.stored, result.failed), expected
        )
        self.assertEqual(
            result.discovered, result.skipped + result.stored + result.failed
        )

    def test_persists_daryo_fields(self):
        item = self.item()
        self.discovery.return_value = [item]
        self.counts(ingest_daryo_uz(self.factory), (1, 0, 1, 0))
        with self.factory() as session:
            row = session.scalar(select(Article))
            self.assertEqual(
                (
                    row.source,
                    row.source_url,
                    row.title,
                    row.content,
                    row.published_at,
                ),
                (
                    "daryo_uz",
                    item.source_url,
                    "Fetched Daryo title",
                    "Fetched Daryo content",
                    item.published_at,
                ),
            )

    def test_known_and_in_run_duplicates_skip_detail_fetch(self):
        known, new = self.item("known"), self.item("new")
        self.insert(known)
        self.discovery.return_value = [known, new, new]
        self.counts(ingest_daryo_uz(self.factory), (3, 2, 1, 0))
        self.fetch.assert_called_once_with(new.source_url)

    def test_one_failure_does_not_stop_later_article(self):
        first, second = self.item("first"), self.item("second")
        self.discovery.return_value = [first, second]
        self.fetch.side_effect = [
            ValueError("bad Daryo payload"),
            DaryoFetchedArticle(second.source_url, "Good", "Complete content"),
        ]
        with self.assertLogs("news_backend.services.ingestion", level="WARNING"):
            result = ingest_daryo_uz(self.factory)
        self.counts(result, (2, 0, 1, 1))
        self.assertEqual(result.failures[0].reason, "invalid_article")

    def test_unique_url_race_is_skipped_and_competitor_preserved(self):
        first, second = self.item("first"), self.item("second")
        self.discovery.return_value = [first, second]

        def fetch(url):
            if url == first.source_url:
                self.insert(first)
            return DaryoFetchedArticle(url, "Fetched", "Fetched content")

        self.fetch.side_effect = fetch
        self.counts(ingest_daryo_uz(self.factory), (2, 1, 1, 0))
        with self.factory() as session:
            rows = {row.source_url: row for row in session.scalars(select(Article))}
            self.assertEqual(rows[first.source_url].title, "Competitor")
            self.assertEqual(rows[first.source_url].content, "Original content")

    def test_network_calls_hold_no_database_connection(self):
        item = self.item()

        def discover():
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return [item]

        def fetch(url):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return DaryoFetchedArticle(url, "Title", "Content")

        self.discovery.side_effect = discover
        self.fetch.side_effect = fetch
        self.counts(ingest_daryo_uz(self.factory), (1, 0, 1, 0))
