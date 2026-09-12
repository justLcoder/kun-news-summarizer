"""Read-only Gemini editorial benchmark; run explicitly to spend API credits."""

import argparse
import os
from pathlib import Path

from google import genai
from google.genai import errors, types

from news_backend.db.session import make_engine, make_session_factory
from news_backend.experiments.editorial_examples import (
    INSTRUCTIONS,
    MAX_OUTPUT_TOKENS,
    build_prefix,
    load_references,
    select_targets,
)


def make_client():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise ValueError("GEMINI_API_KEY is required")

    return genai.Client(
        api_key=key,
        vertexai=False,
        enterprise=False,
        http_options=types.HttpOptions(
            timeout=60000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def parse_response(response):
    candidates = getattr(response, "candidates", None)

    if (
        not candidates
        or len(candidates) != 1
        or candidates[0].finish_reason != types.FinishReason.STOP
    ):
        raise ValueError("missing_or_incomplete_response")

    text = response.text

    if not isinstance(text, str) or not text.strip():
        raise ValueError("blank_output")

    return text.strip()


def _report(summary, response):
    model = getattr(response, "model_version", None) or "unavailable"

    print(
        f"GENERATED SUMMARY: {summary}\nMODEL: {model}",
        flush=True,
    )

    usage = getattr(response, "usage_metadata", None)

    for label, attribute in (
        ("input_tokens", "prompt_token_count"),
        ("cached_tokens", "cached_content_token_count"),
        ("output_tokens", "candidates_token_count"),
        ("thinking_tokens", "thoughts_token_count"),
        ("total_tokens", "total_token_count"),
    ):
        value = getattr(usage, attribute, None)

        print(
            f"{label}: {value if type(value) is int else 'unavailable'}",
            flush=True,
        )


def _report_api_error(exc: errors.APIError):
    print("\n--- GEMINI API ERROR DIAGNOSTIC ---", flush=True)
    print("ERROR TYPE:", type(exc), flush=True)
    print("ERROR CODE:", getattr(exc, "code", None), flush=True)
    print("ERROR MESSAGE:", getattr(exc, "message", None), flush=True)
    print("ERROR ATTRS:", getattr(exc, "__dict__", {}), flush=True)
    print("--- END ERROR DIAGNOSTIC ---\n", flush=True)


def run_experiment(
    factory,
    client,
    references,
    *,
    model: str,
    limit=5,
    article_ids=None,
):
    prefix = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=build_prefix(references),
            )
        ],
    )

    targets = select_targets(
        factory,
        references,
        limit,
        article_ids=article_ids,
    )

    config = types.GenerateContentConfig(
        system_instruction=INSTRUCTIONS,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        response_mime_type="text/plain",
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True,
        ),
    )

    for identifier, title, content in targets:
        print(
            f"ARTICLE ID: {identifier}\nTITLE: {title}",
            flush=True,
        )

        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    prefix,
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=(f"TITLE:\n{title}\n\n" f"ARTICLE:\n{content}")
                            )
                        ],
                    ),
                ],
                config=config,
            )

        except errors.APIError as exc:
            _report("[request failed]", None)
            _report_api_error(exc)
            raise

        try:
            summary = parse_response(response)

        except ValueError as exc:
            summary = f"[validation error: {exc}]"

        _report(summary, response)

    if not targets:
        print("No eligible articles.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--model",
        default="gemini-3.8-flash",
    )

    selection = parser.add_mutually_exclusive_group()

    selection.add_argument(
        "--limit",
        type=int,
        default=5,
    )

    selection.add_argument(
        "--article-ids",
        type=int,
        nargs="+",
    )

    parser.add_argument(
        "--references-dir",
        type=Path,
        default=Path(".local"),
    )

    args = parser.parse_args()

    if args.limit <= 0:
        parser.error("--limit must be positive")

    references = load_references(
        args.references_dir / "reference_articles_15.json",
        args.references_dir / "reference_annotations_15.json",
    )

    with make_client() as client:
        engine = make_engine()

        try:
            run_experiment(
                make_session_factory(engine),
                client,
                references,
                model=args.model,
                limit=args.limit,
                article_ids=args.article_ids,
            )

        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
