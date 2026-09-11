"""Explicit OpenAI client configuration."""
import os
from openai import OpenAI

REQUEST_TIMEOUT = 60.0


def summary_model() -> str:
    model = os.environ.get('OPENAI_SUMMARY_MODEL', '').strip()
    if not model:
        raise ValueError('OPENAI_SUMMARY_MODEL is required')
    return model


def make_client() -> OpenAI:
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise ValueError('OPENAI_API_KEY is required')
    return OpenAI(api_key=key, timeout=REQUEST_TIMEOUT, max_retries=0)
