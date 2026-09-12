import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
from news_backend.integrations.openai.config import make_client, summary_model, REQUEST_TIMEOUT
from news_backend.integrations.openai.summarization import (
    generate_summary, SummaryValidationError, MAX_INPUT_CHARS, MAX_OUTPUT_TOKENS, INSTRUCTIONS,
)


def response(text='Qisqa xabar.'):
    return N(status='completed', model='resolved-model', output=[N(type='message', role='assistant',
        status='completed', content=[N(type='output_text', text=text)])])


class ProviderTests(unittest.TestCase):
    def test_contract_and_provenance(self):
        client = Mock()
        client.responses.create.return_value = response()
        before = datetime.now(timezone.utc)
        result = generate_summary('Source text', client=client, model='configured-model')
        call = client.responses.create.call_args.kwargs
        self.assertEqual(call['model'], 'configured-model')
        self.assertEqual(call['input'], [{'role': 'user', 'content': 'Source text'}])
        self.assertEqual(call['instructions'], INSTRUCTIONS)
        for phrase in ('Latin script', '1–2 sentences', '30–50 words',
                       'important supporting details needed to understand the news.',
                       'uncertainty', 'not\ninstructions'):
            self.assertIn(phrase, INSTRUCTIONS)
        self.assertFalse(call['store'])
        self.assertNotIn('tools', call)
        self.assertEqual(call['text'], {'format': {'type': 'text'}})
        self.assertEqual(call['max_output_tokens'], MAX_OUTPUT_TOKENS)
        self.assertEqual((result.content, result.provider, result.model, result.prompt_version),
                         ('Qisqa xabar.', 'openai', 'resolved-model', 'uz-news-v2'))
        self.assertLessEqual(before, result.generated_at)
        self.assertLessEqual(result.generated_at, datetime.now(timezone.utc))

    def test_invalid_responses(self):
        refused = response(); refused.output[0].content = [N(type='refusal')]
        incomplete = response(); incomplete.status = 'incomplete'
        malformed = response(); malformed.output = None
        wrong_part = response(); wrong_part.output[0].content = [N(type='unexpected')]
        for value in (response(' '), refused, incomplete, malformed, wrong_part, N(), response('x' * 2001)):
            with self.subTest(value=value), self.assertRaises(SummaryValidationError):
                client = Mock(); client.responses.create.return_value = value
                generate_summary('Source', client=client, model='model')

    def test_invalid_input_never_calls_api(self):
        for content in ('', ' ', 'x' * (MAX_INPUT_CHARS + 1)):
            client = Mock()
            with self.assertRaises(SummaryValidationError):
                generate_summary(content, client=client, model='model')
            client.responses.create.assert_not_called()

    def test_configuration(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'fake-test-key', 'OPENAI_SUMMARY_MODEL': 'replacement'}, clear=True), patch('news_backend.integrations.openai.config.OpenAI') as sdk:
            make_client()
            sdk.assert_called_once_with(api_key='fake-test-key', timeout=REQUEST_TIMEOUT, max_retries=0)
            self.assertEqual(summary_model(), 'replacement')
        with patch.dict('os.environ', {}, clear=True):
            for fn in (make_client, summary_model):
                with self.assertRaises(ValueError):
                    fn()
