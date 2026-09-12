"""One Gemini generation attempt, without routing or persistence."""
from datetime import datetime, timezone
from google.genai import types
from news_backend.summarization import GeneratedSummary, SummaryValidationError


MAX_INPUT_CHARS = 40_000
MAX_OUTPUT_TOKENS = 2048
MAX_SUMMARY_CHARS = 2000

def generate_summary(*, title, content, client, model, prompt):
    if not isinstance(title, str) or not title.strip() or not isinstance(content, str) or not content.strip():
        raise SummaryValidationError('blank_input')
    if len(content) > MAX_INPUT_CHARS:
        raise SummaryValidationError('oversized_input')
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role='user', parts=[types.Part.from_text(text=prompt.prefix)]),
                  types.Content(role='user', parts=[types.Part.from_text(text=f'TITLE:\n{title}\n\nARTICLE:\n{content}')])],
        config=types.GenerateContentConfig(system_instruction=prompt.instructions,
            max_output_tokens=MAX_OUTPUT_TOKENS, response_mime_type='text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
    )
    candidates = response.candidates
    if not candidates or len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
        raise SummaryValidationError('missing_or_incomplete_response')
    candidate = candidates[0]
    parts = candidate.content.parts if candidate.content else None
    if not parts or any(not part.thought and part.text is None for part in parts):
        raise SummaryValidationError('malformed_response')
    text = response.text
    if not isinstance(text, str) or not text.strip():
        raise SummaryValidationError('blank_output')
    if len(text.strip()) > MAX_SUMMARY_CHARS:
        raise SummaryValidationError('oversized_output')
    model_version = response.model_version
    if not isinstance(model_version, str) or not model_version.strip():
        raise SummaryValidationError('missing_model_provenance')
    return GeneratedSummary(text.strip(), 'gemini', model_version, prompt.version, datetime.now(timezone.utc))
