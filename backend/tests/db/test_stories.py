"""PostgreSQL persistence tests for cross-source story membership."""

from datetime import datetime, timedelta, timezone

from alembic import command
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from news_backend.db.models import Article, Story, StoryArticle
from tests.db.support import PostgresTestCase


class StoryPersistenceTests(PostgresTestCase):
    def story(self, **overrides):
        values = {
            "first_published_at": datetime(
                2026, 9, 15, 9, tzinfo=timezone(timedelta(hours=5))
            ),
            "last_published_at": datetime(
                2026, 9, 15, 11, tzinfo=timezone(timedelta(hours=5))
            ),
            "last_material_at": datetime(
                2026, 9, 15, 10, tzinfo=timezone(timedelta(hours=5))
            ),
        }
        values.update(overrides)
        return Story(**values)

    def article(self, suffix: str, *, source: str = "kun_uz"):
        return Article(
            source=source,
            source_url=f"https://example.test/{suffix}",
            title=f"Article {suffix}",
            content="Article content.",
            published_at=datetime(2026, 9, 15, 6, tzinfo=timezone.utc),
        )

    def insert_story(self) -> int:
        with self.factory.begin() as session:
            story = self.story()
            session.add(story)
            session.flush()
            return story.id

    def insert_article(self, suffix: str, *, source: str = "kun_uz") -> int:
        with self.factory.begin() as session:
            article = self.article(suffix, source=source)
            session.add(article)
            session.flush()
            return article.id

    def test_story_insert_and_timezone_round_trip(self):
        updated_at = datetime(2026, 9, 15, 12, tzinfo=timezone(timedelta(hours=5)))
        with self.factory.begin() as session:
            story = self.story(updated_at=updated_at)
            session.add(story)
            session.flush()
            story_id = story.id

        with self.factory() as session:
            stored = session.get(Story, story_id)
            self.assertEqual(
                stored.first_published_at,
                datetime(2026, 9, 15, 4, tzinfo=timezone.utc),
            )
            self.assertEqual(
                stored.last_published_at,
                datetime(2026, 9, 15, 6, tzinfo=timezone.utc),
            )
            self.assertEqual(
                stored.last_material_at,
                datetime(2026, 9, 15, 5, tzinfo=timezone.utc),
            )
            self.assertEqual(
                stored.updated_at,
                datetime(2026, 9, 15, 7, tzinfo=timezone.utc),
            )
            self.assertIsNotNone(stored.created_at.utcoffset())

    def test_naive_story_timestamps_rejected(self):
        cases = (
            {"first_published_at": datetime(2026, 9, 15, 9)},
            {"last_published_at": datetime(2026, 9, 15, 11)},
            {"last_material_at": datetime(2026, 9, 15, 10)},
            {"updated_at": datetime(2026, 9, 15, 12)},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaisesRegex(
                ValueError, "timezone-aware"
            ):
                self.story(**values)

    def test_publication_bounds_enforced_by_database(self):
        with self.factory() as session:
            session.add(
                self.story(
                    first_published_at=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
                    last_published_at=datetime(2026, 9, 15, 11, tzinfo=timezone.utc),
                )
            )
            with self.assertRaises(IntegrityError) as caught:
                session.commit()
            self.assertEqual(
                caught.exception.orig.diag.constraint_name,
                "ck_stories_publication_bounds",
            )

    def test_multiple_stories_may_have_overlapping_times(self):
        with self.factory.begin() as session:
            session.add_all([self.story(), self.story()])
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(Story)).all()), 2)

    def test_article_can_be_attached_and_added_at_is_populated(self):
        story_id = self.insert_story()
        article_id = self.insert_article("member")
        with self.factory.begin() as session:
            session.add(StoryArticle(article_id=article_id, story_id=story_id))

        with self.factory() as session:
            membership = session.get(StoryArticle, article_id)
            self.assertEqual(membership.story_id, story_id)
            self.assertIsNotNone(membership.added_at.utcoffset())

    def test_story_accepts_multiple_articles_including_same_source(self):
        story_id = self.insert_story()
        first_id = self.insert_article("first")
        second_id = self.insert_article("second", source="kun_uz")
        with self.factory.begin() as session:
            session.add_all(
                [
                    StoryArticle(article_id=first_id, story_id=story_id),
                    StoryArticle(article_id=second_id, story_id=story_id),
                ]
            )

        with self.factory() as session:
            memberships = session.scalars(
                select(StoryArticle)
                .where(StoryArticle.story_id == story_id)
                .order_by(StoryArticle.article_id)
            ).all()
            self.assertEqual(
                [membership.article_id for membership in memberships],
                [first_id, second_id],
            )

    def test_article_cannot_belong_to_two_stories(self):
        first_story_id = self.insert_story()
        second_story_id = self.insert_story()
        article_id = self.insert_article("single-story")
        with self.factory.begin() as session:
            session.add(
                StoryArticle(article_id=article_id, story_id=first_story_id)
            )

        with self.factory() as session:
            session.add(
                StoryArticle(article_id=article_id, story_id=second_story_id)
            )
            with self.assertRaises(IntegrityError) as caught:
                session.commit()
            self.assertEqual(
                caught.exception.orig.diag.constraint_name,
                "pk_story_articles",
            )

    def test_deleting_article_cascades_membership(self):
        story_id = self.insert_story()
        article_id = self.insert_article("delete-article")
        with self.factory.begin() as session:
            session.add(StoryArticle(article_id=article_id, story_id=story_id))
        with self.factory.begin() as session:
            session.execute(delete(Article).where(Article.id == article_id))

        with self.factory() as session:
            self.assertIsNone(session.get(StoryArticle, article_id))
            self.assertIsNotNone(session.get(Story, story_id))

    def test_deleting_story_cascades_membership_but_preserves_article(self):
        story_id = self.insert_story()
        article_id = self.insert_article("delete-story")
        with self.factory.begin() as session:
            session.add(StoryArticle(article_id=article_id, story_id=story_id))
        with self.factory.begin() as session:
            session.execute(delete(Story).where(Story.id == story_id))

        with self.factory() as session:
            self.assertIsNone(session.get(StoryArticle, article_id))
            self.assertIsNotNone(session.get(Article, article_id))

    def test_named_constraints_and_indexes(self):
        inspector = inspect(self.engine)
        self.assertEqual(
            inspector.get_pk_constraint("stories")["name"], "pk_stories"
        )
        self.assertEqual(
            {constraint["name"] for constraint in inspector.get_check_constraints("stories")},
            {"ck_stories_publication_bounds"},
        )
        self.assertEqual(
            inspector.get_pk_constraint("story_articles")["name"],
            "pk_story_articles",
        )
        foreign_keys = {
            constraint["name"]: constraint
            for constraint in inspector.get_foreign_keys("story_articles")
        }
        self.assertEqual(
            set(foreign_keys),
            {"fk_story_articles_article_id", "fk_story_articles_story_id"},
        )
        self.assertEqual(
            foreign_keys["fk_story_articles_article_id"]["options"]["ondelete"],
            "CASCADE",
        )
        self.assertEqual(
            foreign_keys["fk_story_articles_story_id"]["options"]["ondelete"],
            "CASCADE",
        )
        story_indexes = {index["name"] for index in inspector.get_indexes("stories")}
        membership_indexes = {
            index["name"] for index in inspector.get_indexes("story_articles")
        }
        self.assertIn("ix_stories_last_published_at_id", story_indexes)
        self.assertIn("ix_stories_last_material_at_id", story_indexes)
        self.assertIn("ix_story_articles_story_id", membership_indexes)
        with self.engine.connect() as connection:
            index_definition = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND indexname = 'ix_stories_last_published_at_id'"
                )
            )
        self.assertIn("(last_published_at DESC, id DESC)", index_definition)

    def test_last_material_migration_backfills_existing_stories(self):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                command.downgrade(self.config, "0003")
                story_id = connection.scalar(
                    text(
                        "INSERT INTO stories"
                        "(first_published_at, last_published_at) "
                        "VALUES ('2026-09-15 05:00:00+00', '2026-09-15 08:00:00+00') "
                        "RETURNING id"
                    )
                )
                command.upgrade(self.config, "head")
                backfilled = connection.scalar(
                    text(
                        "SELECT last_material_at FROM stories WHERE id = :story_id"
                    ),
                    {"story_id": story_id},
                )
                self.assertEqual(
                    backfilled,
                    datetime(2026, 9, 15, 8, tzinfo=timezone.utc),
                )
                columns = {
                    column["name"]: column
                    for column in inspect(connection).get_columns("stories")
                }
                self.assertFalse(columns["last_material_at"]["nullable"])

                command.downgrade(self.config, "0003")
                self.assertNotIn(
                    "last_material_at",
                    {
                        column["name"]
                        for column in inspect(connection).get_columns("stories")
                    },
                )
                self.assertEqual(
                    connection.scalar(
                        text("SELECT last_published_at FROM stories WHERE id = :story_id"),
                        {"story_id": story_id},
                    ),
                    datetime(2026, 9, 15, 8, tzinfo=timezone.utc),
                )
            finally:
                command.upgrade(self.config, "head")
                self.config.attributes.clear()

    def test_migration_preserves_existing_rows_and_downgrades_only_story_schema(self):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                command.downgrade(self.config, "0002")
                article_id = connection.scalar(
                    text(
                        "INSERT INTO articles"
                        "(source, source_url, title, content, published_at) "
                        "VALUES ('kun_uz', 'preserved', 'Title', 'Original', now()) "
                        "RETURNING id"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO summaries"
                        "(article_id, content, provider, model, prompt_version, generated_at) "
                        "VALUES (:article_id, 'Summary', 'openai', 'model', 'version', now())"
                    ),
                    {"article_id": article_id},
                )

                command.upgrade(self.config, "head")
                self.assertTrue(inspect(connection).has_table("stories"))
                self.assertTrue(inspect(connection).has_table("story_articles"))
                self.assertEqual(
                    connection.scalar(
                        text("SELECT content FROM articles WHERE id = :article_id"),
                        {"article_id": article_id},
                    ),
                    "Original",
                )
                self.assertEqual(
                    connection.scalar(
                        text("SELECT content FROM summaries WHERE article_id = :article_id"),
                        {"article_id": article_id},
                    ),
                    "Summary",
                )

                command.downgrade(self.config, "0002")
                self.assertFalse(inspect(connection).has_table("stories"))
                self.assertFalse(inspect(connection).has_table("story_articles"))
                self.assertEqual(
                    connection.scalar(
                        text("SELECT content FROM articles WHERE id = :article_id"),
                        {"article_id": article_id},
                    ),
                    "Original",
                )
                self.assertEqual(
                    connection.scalar(
                        text("SELECT content FROM summaries WHERE article_id = :article_id"),
                        {"article_id": article_id},
                    ),
                    "Summary",
                )
            finally:
                command.upgrade(self.config, "head")
                self.config.attributes.clear()
