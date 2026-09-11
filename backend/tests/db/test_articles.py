"""Run against the opt-in postgres-test Compose service, never development data."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from news_backend.db.config import database_url
from news_backend.db.models import Article
from news_backend.db.session import make_engine, make_session_factory


class PersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get("TEST_DATABASE_URL")
        if not value:
            raise RuntimeError("Set TEST_DATABASE_URL and start docker compose --profile test up -d --wait postgres-test")
        url = database_url(value)
        # Deliberately restrict destructive test setup to the documented local service.
        if (url.host not in {"localhost", "127.0.0.1"} or url.port != 5433
                or url.database != "news_test" or url.username != "news_test" or url.query):
            raise RuntimeError("Tests require the isolated local news_test service on port 5433")
        cls.engine = make_engine(url)
        cls.factory = make_session_factory(cls.engine)
        cls.addClassCleanup(cls.engine.dispose)
        cls.config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with cls.engine.begin() as connection:
            cls.config.attributes["connection"] = connection
            command.downgrade(cls.config, "base")
            if inspect(connection).has_table("articles"):
                raise AssertionError("Expected an empty article schema")
            command.upgrade(cls.config, "head")
            command.check(cls.config)
        cls.config.attributes.clear()

    def setUp(self):
        with self.engine.begin() as connection:
            connection.execute(text("TRUNCATE articles RESTART IDENTITY"))

    def article(self, **overrides):
        values = dict(source="kun_uz", source_url="https://kun.uz/news/2026/09/11/example",
                      title="O‘zbekiston yangiliklari", content="Birinchi xabar.\n\nYakuniy jumla.",
                      published_at=datetime(2026, 9, 11, 10, tzinfo=timezone(timedelta(hours=5))))
        values.update(overrides)
        return Article(**values)

    def test_migration_head(self):
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "0001")
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
