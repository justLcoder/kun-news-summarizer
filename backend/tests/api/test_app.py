import importlib
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import text

from news_backend.api.app import create_app
from news_backend.db.session import make_engine, make_session_factory


class ApplicationTests(unittest.TestCase):
    def test_module_import_creates_no_engine_or_provider(self):
        import news_backend.api.app as app_module

        with (
            patch("news_backend.db.session.make_engine") as engine,
            patch("news_backend.integrations.openai.config.make_client") as openai_client,
            patch("news_backend.integrations.gemini.config.make_client") as gemini_client,
        ):
            importlib.reload(app_module)
        engine.assert_not_called()
        openai_client.assert_not_called()
        gemini_client.assert_not_called()

    def test_health_has_no_database_dependency(self):
        factory = Mock(side_effect=AssertionError("health opened a session"))
        with TestClient(create_app(session_factory=factory)) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
        factory.assert_not_called()

    def test_default_lifespan_owns_and_disposes_lazy_engine(self):
        import news_backend.api.app as app_module

        engine = Mock()
        factory = Mock()
        with (
            patch.object(app_module, "make_engine", return_value=engine) as create_engine,
            patch.object(app_module, "make_session_factory", return_value=factory),
            TestClient(app_module.create_app()) as client,
        ):
            self.assertEqual(client.get("/health").status_code, 200)
            engine.connect.assert_not_called()
        create_engine.assert_called_once_with()
        engine.dispose.assert_called_once_with()

    def test_missing_database_url_fails_startup(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
                with TestClient(create_app()):
                    pass

    def test_valid_unreachable_database_still_serves_health(self):
        engine = make_engine("postgresql+psycopg://nobody:bad@127.0.0.1:1/missing")
        try:
            with TestClient(create_app(session_factory=make_session_factory(engine))) as client:
                self.assertEqual(client.get("/health").json(), {"status": "ok"})
        finally:
            engine.dispose()

    def test_injected_engine_remains_caller_owned(self):
        engine = make_engine(
            "postgresql+psycopg://news_test:local_test_password@localhost:5433/news_test"
        )
        try:
            with TestClient(create_app(session_factory=make_session_factory(engine))) as client:
                self.assertEqual(client.get("/health").status_code, 200)
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text("SELECT 1")), 1)
        finally:
            engine.dispose()
