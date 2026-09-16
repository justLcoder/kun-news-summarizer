"""Run against the opt-in postgres-test Compose service, never development data."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from news_backend.db.models import Article
from news_backend.db.session import make_engine, make_session_factory


from tests.db.support import PostgresTestCase


class PersistenceTests(PostgresTestCase):
    def article(self, **overrides):
        values = dict(source="kun_uz", source_url="https://kun.uz/news/2026/09/11/example",
                      title="O‘zbekiston yangiliklari", content="Birinchi xabar.\n\nYakuniy jumla.",
                      published_at=datetime(2026, 9, 11, 10, tzinfo=timezone(timedelta(hours=5))))
        values.update(overrides)
        return Article(**values)

    def test_migration_head(self):
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "0003")
            self.assertEqual(inspect(connection).get_unique_constraints("articles")[0]["name"], "uq_articles_source_url")

    def test_commit_and_fresh_session_unicode_timezone(self):
        with self.factory.begin() as session:
            session.add(self.article())
        fresh_engine = make_engine(self.engine.url)
        try:
            with make_session_factory(fresh_engine)() as session:
                article = session.scalar(select(Article))
                self.assertIsInstance(article.id, int)
                self.assertEqual(article.source, "kun_uz")
                self.assertEqual(article.title, "O‘zbekiston yangiliklari")
                self.assertEqual(article.content, "Birinchi xabar.\n\nYakuniy jumla.")
                self.assertEqual(article.published_at, datetime(2026, 9, 11, 5, tzinfo=timezone.utc))
                self.assertIsNotNone(article.published_at.utcoffset())
                self.assertIsNotNone(article.created_at.utcoffset())
        finally:
            fresh_engine.dispose()

    def test_duplicate_rollback_and_same_title_different_url(self):
        with self.factory() as session:
            session.add(self.article())
            session.commit()
            session.add(self.article())
            with self.assertRaises(IntegrityError) as caught:
                session.commit()
            self.assertEqual(caught.exception.orig.diag.constraint_name, "uq_articles_source_url")
            session.rollback()
            session.add(self.article(source_url="https://kun.uz/news/2026/09/11/other"))
            session.commit()
            self.assertEqual(len(session.scalars(select(Article)).all()), 2)

    def test_required_and_nonblank_fields(self):
        cases = [{key: None} for key in ("source", "source_url", "title", "content", "published_at")]
        cases += [{key: value} for key in ("title", "content") for value in ("", " \t\n")]
        for values in cases:
            with self.subTest(values=values), self.factory() as session:
                session.add(self.article(**values))
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

    def test_naive_publication_datetime_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.article(published_at=datetime(2026, 9, 11))
