"""Gemini Story classifier contract tests with no live API calls."""

from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock

from google.genai import types

from news_backend.integrations.gemini.clustering import (
    CLUSTERING_PROMPT_VERSION,
    INSTRUCTIONS,
    MAX_CANDIDATE_ARTICLE_CHARS,
    MAX_CLASSIFIER_INPUT_CHARS,
    MAX_OUTPUT_TOKENS,
    MAX_TARGET_ARTICLE_CHARS,
    ClusteringValidationError,
    generate_clustering_decision,
)
from news_backend.services.clustering import (
    ArticleEvidence,
    ClusteringAction,
    StoryEvidence,
)


NOW = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)


def article(*, content="Target full content", title="Target title"):
    return ArticleEvidence(
        article_id=99,
        title=title,
        source="daryo_uz",
        published_at=NOW,
        content=content,
    )


def candidates():
    return (
        StoryEvidence(
            story_id=7,
            first_published_at=NOW - timedelta(hours=3),
            last_published_at=NOW - timedelta(hours=1),
            last_material_at=NOW - timedelta(hours=1),
            articles=(
                ArticleEvidence(
                    article_id=70,
                    title="Earlier member title",
                    source="kun_uz",
                    published_at=NOW - timedelta(hours=3),
                    content="Earlier normalized member content",
                ),
                ArticleEvidence(
                    article_id=71,
                    title="Latest member title",
                    source="daryo_uz",
                    published_at=NOW - timedelta(hours=1),
                    content="Latest normalized member content",
                ),
            ),
        ),
    )


def response(payload, *, finish_reason="STOP", model="actual-model"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return types.GenerateContentResponse(
        model_version=model,
        candidates=[
            types.Candidate(
                finish_reason=finish_reason,
                content=types.Content(parts=[types.Part(text=text)]),
            )
        ],
    )


class GeminiClusteringTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()

    def generate(self, payload):
        self.client.models.generate_content.return_value = response(payload)
        return generate_clustering_decision(
            article(), candidates(), client=self.client, model="requested-model"
        )

    def test_all_valid_outcomes(self):
        expected = (
            ("MATCH_NO_CHANGE", 7),
            ("MATCH_UPDATE", 7),
            ("MATCH_CORRECTION", 7),
            ("NEW_STORY", None),
            ("UNCERTAIN", None),
        )
        for action, story_id in expected:
            with self.subTest(action=action):
                decision = self.generate({"action": action, "story_id": story_id})
                self.assertEqual(decision.action, ClusteringAction(action))
                self.assertEqual(decision.story_id, story_id)

    def test_request_contains_raw_evidence_and_structured_contract(self):
        self.generate({"action": "MATCH_UPDATE", "story_id": 7})

        request = self.client.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "requested-model")
        self.assertEqual(request["config"].system_instruction, INSTRUCTIONS)
        self.assertEqual(request["config"].max_output_tokens, MAX_OUTPUT_TOKENS)
        self.assertEqual(request["config"].response_mime_type, "application/json")
        self.assertIsNone(request["config"].tools)
        self.assertTrue(request["config"].automatic_function_calling.disable)
        schema = request["config"].response_schema
        self.assertFalse(schema.additional_properties)
        self.assertEqual(set(schema.required), {"action", "story_id"})

        request_text = request["contents"][0].parts[0].text
        self.assertIn(CLUSTERING_PROMPT_VERSION, request_text)
        evidence = json.loads(request_text.split("\n", 3)[-1])
        self.assertEqual(evidence["new_article"]["content"], "Target full content")
        self.assertEqual(evidence["new_article"]["title"], "Target title")
        self.assertEqual(evidence["new_article"]["source"], "daryo_uz")
        self.assertEqual(evidence["candidate_stories"][0]["story_id"], 7)
        members = evidence["candidate_stories"][0]["member_articles"]
        self.assertEqual([member["article_id"] for member in members], [70, 71])
        self.assertEqual(
            [member["content"] for member in members],
            ["Earlier normalized member content", "Latest normalized member content"],
        )
        self.assertIn("False merges are more harmful", INSTRUCTIONS)
        self.assertIn("Match event identity, not broad topic similarity", INSTRUCTIONS)

    def test_domain_validation_rejects_invalid_decisions(self):
        invalid = (
            {"action": "MATCH_UPDATE", "story_id": 999},
            {"action": "MATCH_UPDATE", "story_id": None},
            {"action": "NEW_STORY", "story_id": 7},
            {"action": "UNCERTAIN", "story_id": 7},
            {"action": "UNKNOWN", "story_id": None},
            {"story_id": None},
            {"action": "NEW_STORY", "story_id": None, "reason": "extra"},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(
                ClusteringValidationError
            ):
                self.generate(payload)

    def test_malformed_blank_and_incomplete_responses_are_rejected(self):
        invalid_responses = (
            response("not json"),
            response(" "),
            response({"action": "NEW_STORY", "story_id": None}, finish_reason="SAFETY"),
            response({"action": "NEW_STORY", "story_id": None}, model=""),
            types.GenerateContentResponse(model_version="model", candidates=None),
        )
        for invalid_response in invalid_responses:
            with self.subTest(response=invalid_response):
                self.client.models.generate_content.return_value = invalid_response
                with self.assertRaises(ClusteringValidationError):
                    generate_clustering_decision(
                        article(),
                        candidates(),
                        client=self.client,
                        model="requested-model",
                    )

    def test_input_bounds_reject_without_api_call(self):
        with self.assertRaisesRegex(ClusteringValidationError, "target article"):
            generate_clustering_decision(
                article(content="x" * (MAX_TARGET_ARTICLE_CHARS + 1)),
                candidates(),
                client=self.client,
                model="model",
            )

        oversized_member = ArticleEvidence(
            article_id=70,
            title="Member",
            source="kun_uz",
            published_at=NOW,
            content="x" * (MAX_CANDIDATE_ARTICLE_CHARS + 1),
            content_truncated=True,
        )
        oversized_candidate = StoryEvidence(
            story_id=7,
            first_published_at=NOW,
            last_published_at=NOW,
            last_material_at=NOW,
            articles=(oversized_member,),
        )
        with self.assertRaisesRegex(ClusteringValidationError, "excerpt"):
            generate_clustering_decision(
                article(),
                (oversized_candidate,),
                client=self.client,
                model="model",
            )

        huge_title_candidates = tuple(
            StoryEvidence(
                story_id=identifier,
                first_published_at=NOW,
                last_published_at=NOW,
                last_material_at=NOW,
                articles=(
                    ArticleEvidence(
                        article_id=identifier,
                        title="t" * (MAX_CLASSIFIER_INPUT_CHARS // 10),
                        source="kun_uz",
                        published_at=NOW,
                        content="content",
                    ),
                ),
            )
            for identifier in range(1, 12)
        )
        with self.assertRaisesRegex(ClusteringValidationError, "classifier input"):
            generate_clustering_decision(
                article(), huge_title_candidates, client=self.client, model="model"
            )

        self.client.models.generate_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
