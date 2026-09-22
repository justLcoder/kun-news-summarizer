"""Bounded Story candidates and provider-independent clustering decisions."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import math
import re
import unicodedata

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from news_backend.db.models import Article, Story, StoryArticle


CANDIDATE_WINDOW = timedelta(hours=24)
DATABASE_CANDIDATE_LIMIT = 50
NARROWED_CANDIDATE_LIMIT = 15
LEXICAL_CANDIDATE_LIMIT = 12
RECENT_FALLBACK_LIMIT = 3


@dataclass(frozen=True)
class CandidateArticle:
    article_id: int
    title: str
    source: str
    published_at: datetime


@dataclass(frozen=True)
class StoryCandidate:
    story_id: int
    first_published_at: datetime
    last_published_at: datetime
    last_material_at: datetime
    articles: tuple[CandidateArticle, ...]


def retrieve_story_candidates(
    session_factory: sessionmaker[Session],
    *,
    article: Article,
) -> tuple[StoryCandidate, ...]:
    """Return at most 50 materially recent Stories with member evidence."""
    published_at = article.published_at
    if not isinstance(published_at, datetime) or published_at.utcoffset() is None:
        raise ValueError("article.published_at must be a timezone-aware datetime")
    cutoff = published_at - CANDIDATE_WINDOW
    has_member = exists(
        select(StoryArticle.article_id).where(StoryArticle.story_id == Story.id)
    )

    with session_factory() as session:
        story_rows = session.execute(
            select(
                Story.id,
                Story.first_published_at,
                Story.last_published_at,
                Story.last_material_at,
            )
            .where(
                Story.last_material_at >= cutoff,
                Story.first_published_at <= published_at,
                has_member,
            )
            .order_by(Story.last_material_at.desc(), Story.id.desc())
            .limit(DATABASE_CANDIDATE_LIMIT)
        ).all()
        if not story_rows:
            return ()

        story_ids = [row.id for row in story_rows]
        member_rows = session.execute(
            select(
                StoryArticle.story_id,
                Article.id,
                Article.title,
                Article.source,
                Article.published_at,
            )
            .join(Article, Article.id == StoryArticle.article_id)
            .where(StoryArticle.story_id.in_(story_ids))
            .order_by(
                StoryArticle.story_id,
                Article.published_at,
                Article.id,
            )
        ).all()

    articles_by_story: dict[int, list[CandidateArticle]] = {
        story_id: [] for story_id in story_ids
    }
    for row in member_rows:
        articles_by_story[row.story_id].append(
            CandidateArticle(
                article_id=row.id,
                title=row.title,
                source=row.source,
                published_at=row.published_at,
            )
        )
    return tuple(
        StoryCandidate(
            story_id=row.id,
            first_published_at=row.first_published_at,
            last_published_at=row.last_published_at,
            last_material_at=row.last_material_at,
            articles=tuple(articles_by_story[row.id]),
        )
        for row in story_rows
    )


_APOSTROPHE_TRANSLATION = str.maketrans(
    {"ʻ": "'", "ʼ": "'", "’": "'", "‘": "'", "`": "'", "´": "'"}
)
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_GENERIC_TITLE_TOKENS = frozenset(
    {
        "bir",
        "bilan",
        "bo'ldi",
        "bo'yicha",
        "bu",
        "dedi",
        "deya",
        "haqida",
        "ham",
        "keyin",
        "ma'lum",
        "o'zbekiston",
        "o'zbekistonda",
        "qildi",
        "so'ng",
        "uchun",
        "va",
        "xabar",
        "xabariga",
        "yangi",
    }
)


def _title_tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).translate(
        _APOSTROPHE_TRANSLATION
    ).casefold()
    return frozenset(
        token
        for token in _TOKEN_PATTERN.findall(normalized)
        if len(token) >= 2 and token not in _GENERIC_TITLE_TOKENS
    )


def narrow_story_candidates(
    article: Article,
    candidates: Sequence[StoryCandidate],
) -> tuple[StoryCandidate, ...]:
    """Rank title overlap, then preserve three recent recall candidates."""
    if not candidates:
        return ()
    target_tokens = _title_tokens(article.title)
    candidate_tokens = {
        candidate.story_id: frozenset().union(
            *(_title_tokens(item.title) for item in candidate.articles)
        )
        for candidate in candidates
    }
    document_frequency = Counter(
        token for tokens in candidate_tokens.values() for token in tokens
    )
    candidate_count = len(candidates)

    scored = []
    for candidate in candidates:
        overlap = target_tokens & candidate_tokens[candidate.story_id]
        if not overlap:
            continue
        rarity = sum(
            math.log((candidate_count + 1) / document_frequency[token])
            for token in overlap
        )
        coverage = len(overlap) / max(1, len(target_tokens))
        scored.append((rarity, coverage, len(overlap), candidate))
    scored.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
            item[3].last_material_at,
            item[3].story_id,
        ),
        reverse=True,
    )

    selected = [item[3] for item in scored[:LEXICAL_CANDIDATE_LIMIT]]
    selected_ids = {candidate.story_id for candidate in selected}
    recent = sorted(
        candidates,
        key=lambda candidate: (candidate.last_material_at, candidate.story_id),
        reverse=True,
    )
    fallback_count = 0
    for candidate in recent:
        if candidate.story_id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.story_id)
        fallback_count += 1
        if (
            fallback_count == RECENT_FALLBACK_LIMIT
            or len(selected) == NARROWED_CANDIDATE_LIMIT
        ):
            break
    return tuple(selected[:NARROWED_CANDIDATE_LIMIT])


class ClusteringAction(str, Enum):
    NEW_STORY = "NEW_STORY"
    MATCH_NO_CHANGE = "MATCH_NO_CHANGE"
    MATCH_UPDATE = "MATCH_UPDATE"
    MATCH_CORRECTION = "MATCH_CORRECTION"
    UNCERTAIN = "UNCERTAIN"


_MATCH_ACTIONS = frozenset(
    {
        ClusteringAction.MATCH_NO_CHANGE,
        ClusteringAction.MATCH_UPDATE,
        ClusteringAction.MATCH_CORRECTION,
    }
)


@dataclass(frozen=True, init=False)
class ClusteringDecision:
    action: ClusteringAction
    story_id: int | None

    def __init__(
        self,
        *,
        action: ClusteringAction | str,
        story_id: int | None,
        candidate_story_ids: Iterable[int],
    ) -> None:
        try:
            validated_action = ClusteringAction(action)
        except (TypeError, ValueError):
            raise ValueError("Unknown clustering action") from None
        try:
            candidates = frozenset(candidate_story_ids)
        except TypeError:
            raise ValueError("Candidate Story IDs must be an iterable") from None
        if any(
            type(candidate_id) is not int or candidate_id <= 0
            for candidate_id in candidates
        ):
            raise ValueError("Candidate Story IDs must be positive integers")
        if validated_action in _MATCH_ACTIONS:
            if type(story_id) is not int or story_id not in candidates:
                raise ValueError("Matching decisions require a supplied candidate Story ID")
        elif story_id is not None:
            raise ValueError("NEW_STORY and UNCERTAIN cannot reference a Story")
        object.__setattr__(self, "action", validated_action)
        object.__setattr__(self, "story_id", story_id)
