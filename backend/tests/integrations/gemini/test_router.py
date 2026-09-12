import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
import httpx
from google.genai.errors import ClientError, ServerError
from news_backend.integrations.gemini.errors import classify_error
from news_backend.integrations.gemini.router import GeminiModelRouter, next_daily_reset
from news_backend.summarization import ModelsUnavailable, SummaryValidationError

NOW = datetime(2026, 3, 8, 9, tzinfo=timezone.utc)


def error(code, details=None, headers=None):
    return N(code=code, details={'error': {'details': details or []}}, response=N(headers=headers or {}))


def quota(identifier, **kwargs):
    return {'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
            'violations': [{'quotaId': identifier, **kwargs}]}


class ClassifierTests(unittest.TestCase):
    def test_quotas_and_retry_hints(self):
        retry = {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '55.5s'}
        for identifier, kind in [('GenerateRequestsPerMinutePerModel', 'minute_quota'),
                                 ('GenerateRequestsPerDayPerModel', 'daily_quota'), ('unknown', 'quota_unknown')]:
            result = classify_error(error(429, [quota(identifier), retry], {'Retry-After': '60'}), now=NOW)
            self.assertEqual((result.kind, result.scope, result.retry_after, result.fallback), (kind, 'model', 60, True))
        self.assertEqual(classify_error(error(429), now=NOW).kind, 'quota_unknown')
        self.assertFalse(classify_error(error(429, [quota('PerDay', quotaValue='0')]), now=NOW).fallback)
        self.assertEqual(classify_error(error(429, [quota('PerMinute', quotaDimensions={'project': 'p'})]), now=NOW).scope, 'model')
        self.assertEqual(classify_error(error(429, [quota('PerMinute', quotaDimensions={'scope': 'project'})]), now=NOW).scope, 'provider')
        both = classify_error(error(429, [quota('PerMinute'), quota('PerDay')]), now=NOW)
        self.assertEqual(both.kind, 'daily_quota')

    def test_statuses_transport_and_malformed_details(self):
        for code in (400, 401, 403, 404, 418, 501):
            self.assertFalse(classify_error(error(code), now=NOW).fallback)
        for code in (500, 502, 503, 504):
            self.assertTrue(classify_error(error(code), now=NOW).fallback)
        for exc, kind in [(httpx.ReadTimeout('timeout'), 'timeout'), (httpx.ConnectError('offline'), 'connection')]:
            self.assertEqual(classify_error(exc, now=NOW).kind, kind)
        for details in (None, 'bad', [], {'error': None}, {'details': [None, 4]}):
            self.assertEqual(classify_error(N(code=429, details=details), now=NOW).kind, 'quota_unknown')
        for hint in ('bad', '-4', 'nan'):
            self.assertIsNone(classify_error(error(429, headers={'Retry-After': hint}), now=NOW).retry_after)
        self.assertEqual(classify_error(error(429, headers={'Retry-After': 'Sun, 08 Mar 2026 09:01:00 GMT'}), now=NOW).retry_after, 60)

    def test_sdk_public_error_compatibility(self):
        exc = ClientError(429, {'error': {'status': 'RESOURCE_EXHAUSTED', 'details': [quota('PerMinute')]}},
                          httpx.Response(429, headers={'Retry-After': '55'}))
        self.assertEqual(exc.status, 'RESOURCE_EXHAUSTED')
        self.assertEqual(classify_error(exc, now=NOW).retry_after, 55)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.router = GeminiModelRouter(client=Mock(), models=['preferred', 'fallback'], prompt=Mock(), now=lambda: self.now)
        self.patch = patch('news_backend.integrations.gemini.router.generate_summary')
        self.generate = self.patch.start(); self.addCleanup(self.patch.stop)
        self.success = N(model='actual-fallback')

    def run_router(self):
        return self.router.generate_summary(title='Title', content='Body')

    def test_fallback_expiry_and_recovery(self):
        self.generate.side_effect = [ServerError(503, {}), self.success]
        self.assertIs(self.run_router(), self.success)
        self.assertEqual([c.kwargs['model'] for c in self.generate.call_args_list], ['preferred', 'fallback'])
        self.generate.reset_mock(); self.generate.side_effect = None; self.generate.return_value = self.success
        self.run_router(); self.assertEqual(self.generate.call_args.kwargs['model'], 'fallback')
        self.now += timedelta(seconds=30)
        self.run_router(); self.assertEqual(self.generate.call_args.kwargs['model'], 'preferred')

    def test_all_unavailable_bounded_calls_and_restart(self):
        self.generate.side_effect = ServerError(503, {})
        with self.assertRaises(ModelsUnavailable): self.run_router()
        self.assertEqual(self.generate.call_count, 2)
        with self.assertRaises(ModelsUnavailable): self.run_router()
        self.assertEqual(self.generate.call_count, 2)
        fresh = GeminiModelRouter(client=Mock(), models=['preferred'], prompt=Mock(), now=lambda: self.now)
        with self.assertRaises(ModelsUnavailable): fresh.generate_summary(title='T', content='B')
        self.assertEqual(self.generate.call_count, 3)

    def test_daily_reset_and_provider_scope(self):
        self.generate.side_effect = [ClientError(429, {'details': [quota('PerDay')]}), self.success]
        self.run_router()
        self.assertEqual(self.router.unavailable_until['preferred'], next_daily_reset(NOW))
        self.assertEqual(next_daily_reset(NOW), datetime(2026, 3, 9, 7, tzinfo=timezone.utc))
        autumn = datetime(2026, 11, 1, 7, tzinfo=timezone.utc)
        self.assertEqual(next_daily_reset(autumn), datetime(2026, 11, 2, 8, tzinfo=timezone.utc))
        self.router.unavailable_until.clear(); self.generate.reset_mock()
        self.generate.side_effect = ClientError(429, {'details': [quota('PerMinute', quotaDimensions={'scope': 'project'})]})
        with self.assertRaises(ModelsUnavailable): self.run_router()
        self.assertEqual(self.generate.call_count, 1)

    def test_fatal_and_validation_do_not_rotate(self):
        for exc in (ClientError(400, {}), ClientError(401, {}), ClientError(403, {}), ClientError(404, {}),
                    SummaryValidationError('blocked'), TypeError('bug')):
            self.generate.reset_mock(); self.generate.side_effect = exc
            with self.assertRaises(type(exc)): self.run_router()
            self.assertEqual(self.generate.call_count, 1)
            self.assertEqual(self.router.unavailable_until, {})

    def test_invalid_configuration_and_clock(self):
        for models in ([], ['a', 'a'], ['']):
            with self.assertRaises(ValueError): GeminiModelRouter(client=None, models=models, prompt=None)
        self.now = datetime(2026, 1, 1)
        with self.assertRaises(ValueError): self.run_router()
