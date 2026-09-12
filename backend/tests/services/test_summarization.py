from news_backend.summarization import ModelsUnavailable
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import httpx
from openai import AuthenticationError, PermissionDeniedError, BadRequestError, RateLimitError, APIConnectionError, InternalServerError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from news_backend.db.models import Article, Summary
from news_backend.integrations.openai.summarization import GeneratedSummary, SummaryValidationError
from news_backend.services.summarization import summarize_articles
from tests.db.support import PostgresTestCase


def generated(content='Qisqa xabar.'):
    return GeneratedSummary(content, 'openai', 'resolved-model', 'uz-news-v1', datetime.now(timezone.utc))


class SummarizationTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.generate = Mock(return_value=generated())

    def add_article(self, content='Source'):
        with self.factory.begin() as session:
            article = Article(source='kun_uz', source_url='url-' + content, title='Title', content=content,
                              published_at=datetime.now(timezone.utc))
            session.add(article); session.flush()
            return article.id

    def run_summary(self, **kwargs):
        return summarize_articles(self.factory, generate=self.generate, **kwargs)

    def test_empty(self):
        result = self.run_summary()
        self.assertEqual(result.selected, 0)
        self.generate.assert_not_called()

    def test_order_limit_persistence_and_existing_exclusion(self):
        first = self.add_article('First'); second = self.add_article('Second')
        result = self.run_summary(limit=1)
        self.assertEqual((result.selected, result.stored, result.failed), (1, 1, 0))
        self.generate.assert_called_once_with(title='Title', content='First')
        with self.factory() as session:
            summary = session.get(Summary, first)
            self.assertEqual((summary.content, summary.provider, summary.model, summary.prompt_version),
                             ('Qisqa xabar.', 'openai', 'resolved-model', 'uz-news-v1'))
            self.assertIsNotNone(summary.generated_at.utcoffset())
        self.generate.reset_mock(); self.run_summary()
        self.generate.assert_called_once_with(title='Title', content='Second')
        self.generate.reset_mock(); self.assertEqual(self.run_summary().selected, 0)
        self.generate.assert_not_called()

    def test_failures_continue_and_counts(self):
        ids = [self.add_article(str(i)) for i in range(4)]
        request = httpx.Request('POST', 'https://example.test')
        self.generate.side_effect = [SummaryValidationError('blank'), ModelsUnavailable('temporary'),
            ModelsUnavailable('temporary'), generated()]
        with self.assertLogs('news_backend.services.summarization', level='WARNING'):
            result = self.run_summary()
        self.assertEqual((result.selected, result.stored, result.skipped, result.failed), (4, 1, 0, 3))
        self.assertEqual([f.article_id for f in result.failures], ids[:3])
        self.assertEqual([f.reason for f in result.failures], ['invalid_generation', 'models_unavailable', 'models_unavailable'])

    def test_fatal_errors_propagate(self):
        self.add_article()
        request = httpx.Request('POST', 'https://example.test')
        for cls, code in ((AuthenticationError, 401), (PermissionDeniedError, 403), (BadRequestError, 400), (RateLimitError, 429)):
            error = cls('fatal', response=httpx.Response(code, request=request), body=None)
            self.generate.side_effect = error
            with self.subTest(cls=cls), self.assertRaises(cls):
                self.run_summary()
        self.generate.side_effect = TypeError('bug')
        with self.assertRaises(TypeError): self.run_summary()
        with self.assertRaises(ValueError): self.run_summary(limit=0)

    def test_duplicate_race_and_connection_scope(self):
        first = self.add_article('First'); self.add_article('Second')
        def generate(content, **kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 1)
            if content == 'First':
                with self.factory.begin() as session:
                    session.add(Summary(article_id=first, **generated('Competing summary').__dict__))
            return generated()
        self.generate.side_effect = generate
        result = self.run_summary()
        self.assertEqual((result.selected, result.stored, result.skipped, result.failed), (2, 1, 1, 0))
        with self.factory() as session:
            self.assertEqual(session.get(Summary, first).content, 'Competing summary')

    def test_unexpected_integrity_failure_propagates(self):
        self.add_article()
        self.generate.return_value = generated(' ')
        with self.assertRaises(IntegrityError): self.run_summary()

    def test_lock_contention_and_release_on_failure(self):
        from sqlalchemy import text
        from news_backend.services.summarization import SUMMARIZATION_LOCK_KEY, SummarizationAlreadyRunning
        self.add_article()
        with self.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as guard:
            guard.execute(text('SELECT pg_advisory_lock(:key)'), {'key': SUMMARIZATION_LOCK_KEY})
            try:
                with self.assertRaises(SummarizationAlreadyRunning): self.run_summary()
                self.generate.assert_not_called()
            finally:
                guard.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': SUMMARIZATION_LOCK_KEY})
        self.generate.side_effect = TypeError('bug')
        with self.assertRaises(TypeError): self.run_summary()
        # A fresh physical connection must be able to acquire the released lock.
        from news_backend.db.session import make_engine
        other = make_engine(self.engine.url)
        try:
            with other.connect().execution_options(isolation_level='AUTOCOMMIT') as guard:
                self.assertTrue(guard.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': SUMMARIZATION_LOCK_KEY}))
                guard.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': SUMMARIZATION_LOCK_KEY})
        finally:
            other.dispose()

    def test_gemini_fallback_persistence_and_unavailable(self):
        from google.genai.errors import ServerError
        from news_backend.integrations.gemini.router import GeminiModelRouter
        from news_backend.summarization import GeneratedSummary
        first = self.add_article('First'); second = self.add_article('Second')
        router = GeminiModelRouter(client=Mock(), models=['a', 'b'], prompt=Mock())
        success = GeneratedSummary('Summary', 'gemini', 'actual-b', 'editorial-version', datetime.now(timezone.utc))
        with patch('news_backend.integrations.gemini.router.generate_summary', side_effect=[ServerError(503, {}), success, ServerError(503, {})]):
            result = summarize_articles(self.factory, generate=router.generate_summary)
        self.assertEqual((result.selected, result.stored, result.failed), (2, 1, 1))
        with self.factory() as session:
            row = session.get(Summary, first)
            self.assertEqual((row.provider, row.model, row.prompt_version), ('gemini', 'actual-b', 'editorial-version'))
            self.assertIsNone(session.get(Summary, second))
        with patch('news_backend.integrations.gemini.router.generate_summary') as generate:
            result = summarize_articles(self.factory, generate=router.generate_summary)
            generate.assert_not_called()
            self.assertEqual((result.selected, result.failed), (1, 1))

    def test_openai_transients_continue_at_provider_boundary(self):
        from functools import partial
        from news_backend.integrations.openai.summarization import generate_summary
        from tests.integrations.openai.test_summarization import response
        self.add_article('First'); second = self.add_article('Second')
        request = httpx.Request('POST', 'https://example.test')
        for error in (APIConnectionError(request=request), InternalServerError('temporary', response=httpx.Response(500, request=request), body=None)):
            with self.subTest(error=type(error)):
                client = Mock(); client.responses.create.side_effect = [error, response()]
                result = summarize_articles(self.factory, generate=partial(generate_summary, client=client, model='m'))
                self.assertEqual((result.selected, result.stored, result.failed), (2, 1, 1))
                with self.factory.begin() as session:
                    self.assertIsNotNone(session.get(Summary, second))
                    session.delete(session.get(Summary, second))
