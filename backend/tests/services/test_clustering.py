"""Candidate retrieval, lexical narrowing, and clustering decision contracts."""

from datetime import datetime, timedelta, timezone
import unittest

from news_backend.db.models import Article, Story, StoryArticle
from news_backend.services.clustering import (
    CANDIDATE_WINDOW,
    DATABASE_CANDIDATE_LIMIT,
    LEXICAL_CANDIDATE_LIMIT,
    NARROWED_CANDIDATE_LIMIT,
    RECENT_FALLBACK_LIMIT,
    CandidateArticle,
    ClusteringAction,
    ClusteringDecision,
    StoryCandidate,
    narrow_story_candidates,
    retrieve_story_candidates,
)
from tests.db.support import PostgresTestCase


TARGET_TIME = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def target_article(title="Toshkentda Gʻazalkent yoʻlida portlash yuz berdi"):
    return Article(
        source="kun_uz",
        source_url="https://kun.uz/news/target",
        title=title,
        content="Target article content.",
        published_at=TARGET_TIME,
    )


def candidate(
    story_id,
    title,
    *,
    last_material_at=None,
    source="kun_uz",
):
    material_at = last_material_at or TARGET_TIME - timedelta(hours=1)
    return StoryCandidate(
        story_id=story_id,
        first_published_at=material_at - timedelta(hours=1),
        last_published_at=material_at,
        last_material_at=material_at,
        articles=(
            CandidateArticle(
                article_id=story_id * 10,
                title=title,
                source=source,
                published_at=material_at,
            ),
        ),
    )


class CandidateRetrievalTests(PostgresTestCase):
    def add_story(
        self,
        suffix,
        *,
        material_at,
        last_published_at=None,
        title=None,
    ):
        last_published_at = last_published_at or material_at
        first_published_at = min(material_at, last_published_at)
        with self.factory.begin() as session:
            article = Article(
                source="kun_uz",
                source_url=f"https://kun.uz/news/{suffix}",
                title=title or f"Candidate {suffix}",
                content="Candidate content.",
                published_at=last_published_at,
            )
            story = Story(
                first_published_at=first_published_at,
                last_published_at=last_published_at,
                last_material_at=material_at,
            )
            session.add_all([article, story])
            session.flush()
            session.add(StoryArticle(article_id=article.id, story_id=story.id))
            return story.id

    def test_24_hour_eligibility_boundary_and_material_activity(self):
        inside = self.add_story(
            "inside",
            material_at=TARGET_TIME - CANDIDATE_WINDOW + timedelta(microseconds=1),
        )
        boundary = self.add_story(
            "boundary",
            material_at=TARGET_TIME - CANDIDATE_WINDOW,
        )
        self.add_story(
            "outside",
            material_at=TARGET_TIME - CANDIDATE_WINDOW - timedelta(microseconds=1),
        )
        self.add_story(
            "stale-material",
            material_at=TARGET_TIME - timedelta(hours=30),
            last_published_at=TARGET_TIME - timedelta(hours=2),
        )

        result = retrieve_story_candidates(self.factory, article=target_article())

        self.assertEqual([item.story_id for item in result], [inside, boundary])
        self.assertTrue(all(item.articles for item in result))

    def test_database_candidate_cap_and_recent_order(self):
        story_ids = [
            self.add_story(
                f"cap-{minutes}",
                material_at=TARGET_TIME - timedelta(minutes=minutes),
            )
            for minutes in range(DATABASE_CANDIDATE_LIMIT + 5)
        ]

        result = retrieve_story_candidates(self.factory, article=target_article())

        self.assertEqual(len(result), DATABASE_CANDIDATE_LIMIT)
        self.assertEqual(
            [item.story_id for item in result],
            story_ids[:DATABASE_CANDIDATE_LIMIT],
        )


class LexicalNarrowingTests(unittest.TestCase):
    def test_same_event_wording_ranks_above_unrelated_and_normalizes_text(self):
        candidates = [
            candidate(1, "G‘AZALKENT yo‘lida portlash sodir bo‘ldi!"),
            candidate(2, "O‘zbekistonda yangi xabar ma’lum bo‘ldi"),
            candidate(3, "Paxta eksporti bo‘yicha yangi kelishuv tuzildi"),
        ]

        result = narrow_story_candidates(target_article(), candidates)

        self.assertEqual(result[0].story_id, 1)
        self.assertIn(2, [item.story_id for item in result[1:]])
        self.assertIn(3, [item.story_id for item in result[1:]])

    def test_ties_are_deterministic_by_material_recency_then_story_id(self):
        same_time = TARGET_TIME - timedelta(hours=1)
        candidates = [
            candidate(1, "Toshkentda portlash", last_material_at=same_time),
            candidate(3, "Toshkentda portlash", last_material_at=same_time),
            candidate(
                2,
                "Toshkentda portlash",
                last_material_at=same_time + timedelta(minutes=1),
            ),
        ]

        first = narrow_story_candidates(target_article(), candidates)
        second = narrow_story_candidates(target_article(), list(reversed(candidates)))

        self.assertEqual([item.story_id for item in first], [2, 3, 1])
        self.assertEqual(first, second)

    def test_result_is_capped_at_fifteen(self):
        candidates = [
            candidate(
                identifier,
                f"Toshkentda portlash tafsiloti {identifier}",
                last_material_at=TARGET_TIME - timedelta(minutes=identifier),
            )
            for identifier in range(1, 31)
        ]

        result = narrow_story_candidates(target_article(), candidates)

        self.assertEqual(LEXICAL_CANDIDATE_LIMIT, 12)
        self.assertEqual(RECENT_FALLBACK_LIMIT, 3)
        self.assertEqual(NARROWED_CANDIDATE_LIMIT, 15)
        self.assertEqual(len(result), NARROWED_CANDIDATE_LIMIT)

    def test_three_recent_fallbacks_survive_without_overlap(self):
        candidates = [
            candidate(
                1,
                "Gʻazalkent yoʻlida portlash tafsilotlari",
                last_material_at=TARGET_TIME - timedelta(hours=10),
            ),
            candidate(2, "Bugʻdoy hosili", last_material_at=TARGET_TIME),
            candidate(
                3,
                "Maktab imtihonlari",
                last_material_at=TARGET_TIME - timedelta(minutes=1),
            ),
            candidate(
                4,
                "Yangi avtomobil",
                last_material_at=TARGET_TIME - timedelta(minutes=2),
            ),
            candidate(
                5,
                "Shaxmat turniri",
                last_material_at=TARGET_TIME - timedelta(minutes=3),
            ),
        ]

        result = narrow_story_candidates(target_article(), candidates)

        self.assertEqual([item.story_id for item in result], [1, 2, 3, 4])


class ClusteringDecisionTests(unittest.TestCase):
    def test_all_valid_outcomes(self):
        candidates = {7, 9}
        expected = (
            (ClusteringAction.NEW_STORY, None),
            (ClusteringAction.MATCH_NO_CHANGE, 7),
            (ClusteringAction.MATCH_UPDATE, 9),
            (ClusteringAction.MATCH_CORRECTION, 7),
            (ClusteringAction.UNCERTAIN, None),
        )
        for action, story_id in expected:
            with self.subTest(action=action):
                decision = ClusteringDecision(
                    action=action,
                    story_id=story_id,
                    candidate_story_ids=candidates,
                )
                self.assertEqual((decision.action, decision.story_id), (action, story_id))

    def test_matching_decisions_require_a_supplied_candidate_id(self):
        for story_id in (None, 8, True):
            with self.subTest(story_id=story_id), self.assertRaises(ValueError):
                ClusteringDecision(
                    action=ClusteringAction.MATCH_UPDATE,
                    story_id=story_id,
                    candidate_story_ids={7, 9},
                )

    def test_new_and_uncertain_reject_story_ids(self):
        for action in (ClusteringAction.NEW_STORY, ClusteringAction.UNCERTAIN):
            with self.subTest(action=action), self.assertRaises(ValueError):
                ClusteringDecision(
                    action=action,
                    story_id=7,
                    candidate_story_ids={7},
                )

    def test_unknown_action_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown clustering action"):
            ClusteringDecision(
                action="MATCH_WHATEVER",
                story_id=7,
                candidate_story_ids={7},
            )


if __name__ == "__main__":
    unittest.main()
