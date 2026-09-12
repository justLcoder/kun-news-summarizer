"""Explicit production Gemini configuration; caller owns client lifetime."""
import os
from google import genai
from google.genai import types


def summary_models():
    models = tuple(m.strip() for m in os.environ.get('GEMINI_SUMMARY_MODELS', '').split(','))
    if not all(models) or len(set(models)) != len(models):
        raise ValueError('GEMINI_SUMMARY_MODELS requires unique nonblank ordered model IDs')
    return models


def make_client():
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        raise ValueError('GEMINI_API_KEY is required')
    return genai.Client(api_key=key, vertexai=False, enterprise=False,
                        http_options=types.HttpOptions(timeout=60000,
                            retry_options=types.HttpRetryOptions(attempts=1)))


def make_router(*, client):
    """Load configured references/models once; caller creates and closes client."""
    from news_backend.prompts.editorial import load_prompt
    from .router import GeminiModelRouter

    paths = []
    for name in ('GEMINI_REFERENCE_ARTICLES', 'GEMINI_REFERENCE_ANNOTATIONS'):
        path = os.environ.get(name, '').strip()
        if not path:
            raise ValueError(f'{name} is required')
        paths.append(path)
    models = summary_models()
    prompt = load_prompt(*paths)
    return GeminiModelRouter(client=client, models=models, prompt=prompt)
