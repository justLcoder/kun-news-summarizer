import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import Mock
from sqlalchemy import select, func
from news_backend.db.models import Article, Summary
from news_backend.experiments.editorial_examples import select_targets, run_experiment
from tests.db.support import PostgresTestCase
from tests.experiments.test_editorial_examples import references, response


class SelectionTests(PostgresTestCase):
    def test_url_exclusion_changed_ids_order_limit_no_writes(self):
        refs = references()
        with self.factory.begin() as session:
            for url in (refs[0]['source_url'], 'https://kun.uz/new-a', 'https://kun.uz/new-b'):
                session.add(Article(source='kun_uz', source_url=url, title=url, content='Body',
                                    published_at=datetime(2026,9,12,tzinfo=timezone.utc)))
        targets = select_targets(self.factory, refs, limit=1)
        self.assertEqual([r.id for r in targets], [3])
        self.assertEqual([r.id for r in select_targets(self.factory, refs)], [3,2])
        client = Mock()
        def generate(**kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return response()
        client.responses.create.side_effect = generate
        with redirect_stdout(io.StringIO()): run_experiment(self.factory, client, refs)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Summary)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(Article)), 3)

    def test_explicit_ids_and_reference_url_identity(self):
        refs = references()
        refs[0]['id'] = 1  # ID collision must not exclude a different URL.
        with self.factory.begin() as session:
            for url in ('new-one', 'new-two', refs[0]['source_url']):
                session.add(Article(source='kun_uz', source_url=url, title=url, content='Body',
                                    published_at=datetime(2026,9,12,tzinfo=timezone.utc)))
        self.assertEqual([r[0] for r in select_targets(self.factory, refs, article_ids=[2,1])], [2,1])
        self.assertEqual([r.id for r in select_targets(self.factory, refs)], [2,1])
        for ids in ([1,1], [0], [-1], [999], [3]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                select_targets(self.factory, refs, article_ids=ids)
