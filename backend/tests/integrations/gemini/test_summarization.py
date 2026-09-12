import unittest
from unittest.mock import Mock, patch
from google.genai import types
from news_backend.integrations.gemini.config import make_client, summary_models
from news_backend.integrations.gemini.summarization import generate_summary
from news_backend.prompts.editorial import EditorialPrompt
from news_backend.summarization import SummaryValidationError


class GenerationTests(unittest.TestCase):
    def test_contract_and_validation(self):
        prompt = EditorialPrompt('Instructions', 'Full prefix', 'version')
        reply = types.GenerateContentResponse(model_version='actual', candidates=[types.Candidate(
            finish_reason='STOP', content=types.Content(parts=[types.Part(text='Summary')]))])
        client = Mock(); client.models.generate_content.return_value = reply
        result = generate_summary(title='Title', content='Body', client=client, model='requested', prompt=prompt)
        self.assertEqual((result.provider, result.model, result.prompt_version), ('gemini', 'actual', 'version'))
        self.assertIsNotNone(result.generated_at.utcoffset())
        call = client.models.generate_content.call_args.kwargs
        self.assertEqual(call['model'], 'requested')
        self.assertEqual([c.parts[0].text for c in call['contents']], ['Full prefix', 'TITLE:\nTitle\n\nARTICLE:\nBody'])
        self.assertEqual(call['config'].system_instruction, 'Instructions')
        self.assertIsNone(call['config'].tools)
        self.assertEqual(call['config'].max_output_tokens, 2048)
        for change in ('blank', 'blocked', 'model', 'missing'):
            bad = reply.model_copy(deep=True)
            if change == 'blank': bad.candidates[0].content.parts[0].text = ' '
            if change == 'blocked': bad.candidates[0].finish_reason = 'SAFETY'
            if change == 'model': bad.model_version = None
            if change == 'missing': bad.candidates = None
            client.models.generate_content.return_value = bad
            with self.assertRaises(SummaryValidationError): generate_summary(title='T', content='B', client=client, model='m', prompt=prompt)

    def test_config(self):
        with patch.dict('os.environ', {'GEMINI_SUMMARY_MODELS': ' lite , flash ', 'GEMINI_API_KEY': 'fake'}, clear=True), patch('news_backend.integrations.gemini.config.genai.Client') as client:
            self.assertEqual(summary_models(), ('lite', 'flash'))
            make_client()
            self.assertEqual(client.call_args.kwargs['http_options'].retry_options.attempts, 1)
        for value in ('', 'a,,b', 'a,a'):
            with patch.dict('os.environ', {'GEMINI_SUMMARY_MODELS': value}, clear=True):
                with self.assertRaises(ValueError): summary_models()
                with self.assertRaises(ValueError): make_client()

    def test_bounds(self):
        client = Mock(); prompt = EditorialPrompt('I', 'P', 'v')
        with self.assertRaisesRegex(SummaryValidationError, 'oversized_input'):
            generate_summary(title='T', content='x' * 40001, client=client, model='m', prompt=prompt)
        client.models.generate_content.assert_not_called()
        for size in (2000, 2001):
            client.models.generate_content.return_value = types.GenerateContentResponse(model_version='m', candidates=[types.Candidate(
                finish_reason='STOP', content=types.Content(parts=[types.Part(text='x' * size)]))])
            if size == 2000:
                self.assertEqual(len(generate_summary(title='T', content='x' * 40000, client=client, model='m', prompt=prompt).content), size)
            else:
                with self.assertRaisesRegex(SummaryValidationError, 'oversized_output'):
                    generate_summary(title='T', content='B', client=client, model='m', prompt=prompt)

    def test_composition(self):
        import json
        import tempfile
        from pathlib import Path
        from news_backend.integrations.gemini.config import make_router
        from tests.experiments.test_editorial_examples import references
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.json', Path(directory)/'b.json'
            a.write_text(json.dumps(references())); b.write_text(json.dumps(references()))
            env = {'GEMINI_REFERENCE_ARTICLES': str(a), 'GEMINI_REFERENCE_ANNOTATIONS': str(b), 'GEMINI_SUMMARY_MODELS': ' a, b '}
            client = Mock()
            with patch.dict('os.environ', env, clear=True):
                router = make_router(client=client)
                self.assertIs(router.client, client)
                self.assertEqual(router.models, ('a', 'b'))
                self.assertIn('Full source 101', router.prompt.prefix)
                client.close.assert_not_called()
                client.models.generate_content.assert_not_called()
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'GEMINI_REFERENCE_ARTICLES'):
                    make_router(client=client)
