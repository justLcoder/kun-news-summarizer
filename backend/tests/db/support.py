"""Run against the opt-in postgres-test Compose service, never development data."""

import os
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from news_backend.db.config import database_url
from news_backend.db.session import make_engine, make_session_factory


class PostgresTestCase(unittest.TestCase):
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
            connection.execute(text("TRUNCATE summaries, articles RESTART IDENTITY"))

