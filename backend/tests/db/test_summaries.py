from datetime import datetime, timezone
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from news_backend.db.models import Article, Summary
from tests.db.support import PostgresTestCase


class SummaryPersistenceTests(PostgresTestCase):
    def article(self):
        with self.factory.begin() as session:
            article = Article(source='kun_uz', source_url='url', title='Title', content='Content', published_at=datetime.now(timezone.utc))
            session.add(article); session.flush()
            return article.id

    def summary(self, article_id, content='Summary'):
        return Summary(article_id=article_id, content=content, provider='openai', model='model',
                       prompt_version='uz-news-v1', generated_at=datetime.now(timezone.utc))

    def test_foreign_key_and_nonblank(self):
        article_id = self.article()
        for row in (self.summary(999999), self.summary(article_id, ''), self.summary(article_id, ' \n\t')):
            with self.factory() as session, self.assertRaises(IntegrityError):
                session.add(row); session.commit()

    def test_delete_cascades(self):
        article_id = self.article()
        with self.factory.begin() as session: session.add(self.summary(article_id))
        with self.factory.begin() as session: session.delete(session.get(Article, article_id))
        with self.factory() as session: self.assertIsNone(session.get(Summary, article_id))

    def test_upgrade_preserves_existing_article(self):
        with self.engine.begin() as connection:
            self.config.attributes['connection'] = connection
            try:
                command.downgrade(self.config, '0001')
                article_id = connection.scalar(text("INSERT INTO articles(source, source_url, title, content, published_at) VALUES ('kun_uz', 'old', 'Title', 'Original', now()) RETURNING id"))
                command.upgrade(self.config, 'head')
                self.assertEqual(connection.scalar(text('SELECT content FROM articles WHERE id=:id'), {'id': article_id}), 'Original')
                self.assertEqual(connection.scalar(text('SELECT version_num FROM alembic_version')), '0002')
            finally:
                self.config.attributes.clear()
