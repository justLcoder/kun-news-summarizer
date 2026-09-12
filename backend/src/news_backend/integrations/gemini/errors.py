"""Interpret public SDK error attributes; no routing state or English-message matching."""
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import math
import re
import httpx


@dataclass(frozen=True)
class GeminiFailure:
    kind: str
    status_code: int | None
    scope: str = 'model'
    fallback: bool = False
    retry_after: float | None = None


def _retry_delay(details, response, now):
    delays = []
    for detail in details:
        if detail.get('@type') == 'type.googleapis.com/google.rpc.RetryInfo':
            value = detail.get('retryDelay')
            if isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d{1,9})?s', value):
                delays.append(float(value[:-1]))
    headers = getattr(response, 'headers', {}) or {}
    value = headers.get('Retry-After') or headers.get('retry-after')
    if isinstance(value, str):
        try:
            delay = float(value) if value.isdigit() else (parsedate_to_datetime(value) - now).total_seconds()
            delays.append(delay)
        except (ValueError, TypeError, OverflowError):
            pass
    # Ignore implausibly large/malformed hints instead of overflowing datetime.
    valid = [d for d in delays if math.isfinite(d) and 0 <= d <= 365 * 86400]
    return max(valid) if valid else None


def classify_error(error, *, now: datetime) -> GeminiFailure:
    if isinstance(error, httpx.TimeoutException):
        return GeminiFailure('timeout', None, fallback=True)
    if isinstance(error, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return GeminiFailure('connection', None, fallback=True)
    code = getattr(error, 'code', None)
    body = getattr(error, 'details', {})
    if isinstance(body, dict):
        body = body.get('error', body)
    raw = body.get('details', []) if isinstance(body, dict) else []
    details = [d for d in raw if isinstance(d, dict)] if isinstance(raw, list) else []
    scope = 'model'
    quota_ids = []
    zero_quota = False
    for detail in details:
        if detail.get('@type') == 'type.googleapis.com/google.rpc.QuotaFailure':
            violations = detail.get('violations', [])
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                identifier = violation.get('quotaId', '')
                if isinstance(identifier, str):
                    quota_ids.append(identifier.lower())
                zero_quota |= violation.get('quotaValue') in (0, '0')
                dimensions = violation.get('quotaDimensions', {})
                # A project identifier alone does not mean the quota spans all models.
                if isinstance(dimensions, dict) and dimensions.get('scope') in ('project', 'provider') and not dimensions.get('model'):
                    scope = 'provider'
        if detail.get('@type') == 'type.googleapis.com/google.rpc.ErrorInfo':
            metadata = detail.get('metadata', {})
            if isinstance(metadata, dict) and metadata.get('scope') in ('project', 'provider') and not metadata.get('model'):
                scope = 'provider'
    delay = _retry_delay(details, getattr(error, 'response', None), now)
    if code == 429:
        if zero_quota:
            return GeminiFailure('quota_configuration', code, scope)
        kind = ('daily_quota' if any('perday' in q for q in quota_ids) else
                'minute_quota' if any('perminute' in q for q in quota_ids) else 'quota_unknown')
        return GeminiFailure(kind, code, scope, True, delay)
    if code == 408:
        return GeminiFailure('timeout', code, scope, True, delay)
    if code in (500, 502, 503, 504):
        return GeminiFailure('unavailable' if code == 503 else 'server', code, scope, True, delay)
    return GeminiFailure({400: 'bad_request', 401: 'authentication', 403: 'permission',
                          404: 'model_configuration'}.get(code, 'unexpected'), code)
