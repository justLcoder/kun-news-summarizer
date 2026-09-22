"""Sequential, process-local Gemini fallback without sleeps or probes."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging
import httpx
from google.genai.errors import APIError
from news_backend.summarization import ModelsUnavailable
from news_backend.services.clustering import ClusteringAction, ClusteringDecision
from .clustering import generate_clustering_decision
from .errors import classify_error
from .summarization import generate_summary

logger = logging.getLogger(__name__)


def next_daily_reset(now):
    local = now.astimezone(ZoneInfo('America/Los_Angeles'))
    tomorrow = local.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=local.tzinfo).astimezone(timezone.utc)


class GeminiModelRouter:
    def __init__(self, *, client, models, prompt, now=lambda: datetime.now(timezone.utc)):
        if isinstance(models, str):
            raise ValueError('Models must be a sequence of model IDs')
        self.models = tuple(m.strip() if isinstance(m, str) else m for m in models)
        if (not self.models or any(not isinstance(m, str) or not m.strip() for m in self.models)
                or len(set(self.models)) != len(self.models)):
            raise ValueError('Models must be ordered, nonblank, and unique')
        self.client, self.prompt, self.now = client, prompt, now
        self.unavailable_until = {}
        self.provider_unavailable_until = None

    def _now(self):
        value = self.now()
        if value.utcoffset() != timedelta(0):
            raise ValueError('now() must return a timezone-aware UTC datetime')
        return value

    def _route(self, attempt, *, operation):
        for model in self.models:
            now = self._now()
            if self.provider_unavailable_until and now < self.provider_unavailable_until:
                break
            deadline = self.unavailable_until.get(model)
            if deadline and now < deadline:
                logger.debug('Skipping Gemini model=%s unavailable_until=%s', model, deadline)
                continue
            try:
                result = attempt(model)
            except (APIError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                now = self._now()
                failure = classify_error(exc, now=now)
                if not failure.fallback:
                    logger.error('Gemini aborted model=%s kind=%s status=%s', model, failure.kind, failure.status_code)
                    raise
                seconds = failure.retry_after if failure.retry_after is not None else (60 if 'quota' in failure.kind else 30)
                deadline = now + timedelta(seconds=max(1, seconds))
                if failure.kind == 'daily_quota':
                    deadline = next_daily_reset(now)
                    if failure.retry_after is not None:
                        deadline = max(deadline, now + timedelta(seconds=failure.retry_after))
                if failure.scope == 'provider':
                    self.provider_unavailable_until = deadline
                else:
                    self.unavailable_until[model] = deadline
                logger.warning('Gemini cooldown model=%s kind=%s status=%s scope=%s unavailable_until=%s',
                               model, failure.kind, failure.status_code, failure.scope, deadline)
                continue
            self.unavailable_until.pop(model, None)
            logger.debug(
                'Gemini succeeded operation=%s requested_model=%s actual_model=%s',
                operation,
                model,
                getattr(result, 'model', model),
            )
            return result
        raise ModelsUnavailable('models_unavailable')

    def generate_summary(self, *, title, content):
        return self._route(
            lambda model: generate_summary(
                title=title,
                content=content,
                client=self.client,
                model=model,
                prompt=self.prompt,
            ),
            operation='summary',
        )

    def classify_story(self, *, article, candidates):
        if not candidates:
            return ClusteringDecision(
                action=ClusteringAction.NEW_STORY,
                story_id=None,
                candidate_story_ids=(),
            )
        return self._route(
            lambda model: generate_clustering_decision(
                article,
                tuple(candidates),
                client=self.client,
                model=model,
            ),
            operation='clustering',
        )
