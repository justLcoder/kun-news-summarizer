from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from news_backend.api.app import create_app
from news_backend.db.models import Article, Summary
from tests.db.support import PostgresTestCase


PUBLIC_FIELDS = {"id", "title", "summary", "source", "source_url", "published_at"}


class NewsApiTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.client_context = TestClient(create_app(session_factory=self.factory))
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)

    def add_article(self, *, published_at, summarized=True, title="Title", suffix="x"):
        with self.factory.begin() as session:
            article = Article(
                source="kun_uz",
                source_url=f"https://kun.uz/news/2026/09/12/{suffix}",
                title=title,
                content=f"RAW-SECRET-{suffix}",
                published_at=published_at,
            )
            session.add(article)
            session.flush()
            if summarized:
                session.add(Summary(
                    article_id=article.id,
                    content=f"Public summary {suffix}",
                    provider="SECRET-PROVIDER",
                    model="SECRET-MODEL",
                    prompt_version="SECRET-PROMPT",
                    generated_at=datetime.now(timezone.utc),
                ))
            return article.id

    def test_empty_feed_and_validation(self):
        self.assertEqual(self.client.get("/api/v1/news").json(), {"items": []})
        for query in ("0", "101", "x", "1.5"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/api/v1/news?limit={query}").status_code, 422)
        for article_id in ("0", "-1", "9223372036854775808", "not-an-id"):
            with self.subTest(article_id=article_id):
                self.assertEqual(self.client.get(f"/api/v1/news/{article_id}").status_code, 422)

    def test_visibility_order_default_limit_and_no_mutation(self):
        base = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
        older = self.add_article(published_at=base, suffix="older")
        tied_first = self.add_article(published_at=base + timedelta(hours=1), suffix="tie-a")
        tied_second = self.add_article(published_at=base + timedelta(hours=1), suffix="tie-b")
        self.add_article(published_at=base + timedelta(hours=2), summarized=False, suffix="hidden")
        for number in range(18):
            self.add_article(published_at=base - timedelta(days=number + 1), suffix=f"extra-{number}")

        with self.factory() as session:
            before = (
                session.scalar(select(func.count()).select_from(Article)),
                session.scalar(select(func.count()).select_from(Summary)),
            )
        response = self.client.get("/api/v1/news")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 20)
        self.assertEqual([item["id"] for item in items[:3]], [tied_second, tied_first, older])
        self.assertEqual(len(self.client.get("/api/v1/news?limit=1").json()["items"]), 1)
        with self.factory() as session:
            after = (
                session.scalar(select(func.count()).select_from(Article)),
                session.scalar(select(func.count()).select_from(Summary)),
            )
        self.assertEqual(after, before)

    def test_detail_fields_privacy_visibility_and_utc_instant(self):
        source_time = datetime(2026, 9, 12, 15, 30, tzinfo=timezone(timedelta(hours=5)))
        visible = self.add_article(
            published_at=source_time, title="Public title", suffix="visible"
        )
        hidden = self.add_article(
            published_at=source_time, summarized=False, suffix="hidden"
        )

        response = self.client.get(f"/api/v1/news/{visible}")
        self.assertEqual(response.status_code, 200)
        item = response.json()
        self.assertEqual(set(item), PUBLIC_FIELDS)
        self.assertEqual(item["summary"], "Public summary visible")
        serialized = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        self.assertEqual(serialized, source_time.astimezone(timezone.utc))
        body = response.text
        for secret in ("RAW-SECRET", "SECRET-PROVIDER", "SECRET-MODEL", "SECRET-PROMPT"):
            self.assertNotIn(secret, body)
        self.assertNotIn("content", item)
        self.assertEqual(
            self.client.get(f"/api/v1/news/{hidden}").json(), {"detail": "News not found"}
        )
        self.assertEqual(
            self.client.get("/api/v1/news/9223372036854775807").json(),
            {"detail": "News not found"},
        )

    def test_sessions_close_after_success_404_and_query_failure(self):
        self.add_article(published_at=datetime.now(timezone.utc), suffix="visible")
        baseline = self.engine.pool.checkedout()
        self.assertEqual(self.client.get("/api/v1/news").status_code, 200)
        self.assertEqual(self.engine.pool.checkedout(), baseline)
        self.assertEqual(self.client.get("/api/v1/news/999").status_code, 404)
        self.assertEqual(self.engine.pool.checkedout(), baseline)
        with patch("news_backend.api.routes.latest_news", side_effect=RuntimeError("query failed")):
            with TestClient(
                create_app(session_factory=self.factory), raise_server_exceptions=False
            ) as client:
                self.assertEqual(client.get("/api/v1/news").status_code, 500)
        self.assertEqual(self.engine.pool.checkedout(), baseline)
