import unittest
from unittest.mock import patch

from news_backend.db.config import database_url
from news_backend.db.session import make_engine


class ConfigurationTests(unittest.TestCase):
    def test_missing(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "required"):
            database_url()

    def test_invalid_does_not_expose_credentials(self):
        for value in ("invalid-secret", "sqlite:///secret", "postgresql+psycopg://u:secret@host:bad/db"):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                database_url(value)
            self.assertNotIn("secret", str(caught.exception))

    def test_explicit_url_needs_no_environment_or_connection(self):
        with patch.dict("os.environ", {}, clear=True):
            engine = make_engine("postgresql+psycopg://user:secret@localhost/example")
            self.assertEqual(engine.url.database, "example")
            engine.dispose()
