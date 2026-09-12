import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from google.genai import types
from sqlalchemy import select

from news_backend.db.models import Article, Summary
from news_backend.experiments import editorial_examples as original
from news_backend.experiments import editorial_examples_gemini as gemini
from tests.db.support import PostgresTestCase
from tests.experiments.test_editorial_examples import references


def response(**kwargs):
    return types.GenerateContentResponse(
        model_version='gemini-returned-version',
        candidates=[types.Candidate(finish_reason='STOP', content=types.Content(
            role='model', parts=[types.Part(text='Private thought', thought=True),
                                 types.Part(text='Synthetic summary.')]))],
        **kwargs,
    )


class GeminiTests(unittest.TestCase):
    def test_request_and_metrics(self):
        client = Mock()
        client.models.generate_content.return_value = response(usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12000, cached_content_token_count=10000,
            candidates_token_count=50, thoughts_token_count=100, total_token_count=12150))
        refs = references()
        with patch.object(gemini, 'select_targets', return_value=[(31, 'Title A', 'Body A'), (30, 'Title B', 'Body B')]) as select_targets, redirect_stdout(io.StringIO()) as output:
            gemini.run_experiment(None, client, refs, model='gemini-3.8-flash', article_ids=[31, 30])
        select_targets.assert_called_once_with(None, refs, 5, article_ids=[31, 30])
        calls = [call.kwargs for call in client.models.generate_content.call_args_list]
        self.assertEqual(calls[0]['contents'][0], calls[1]['contents'][0])
        for call, title, body in zip(calls, ['Title A', 'Title B'], ['Body A', 'Body B']):
            self.assertEqual(set(call), {'model', 'contents', 'config'})
            self.assertEqual(call['model'], 'gemini-3.8-flash')
            self.assertEqual([c.model_dump(exclude_none=True) for c in call['contents']], [
                {'role': 'user', 'parts': [{'text': original.build_prefix(refs)}]},
                {'role': 'user', 'parts': [{'text': f'TITLE:\n{title}\n\nARTICLE:\n{body}'}]},
            ])
            self.assertEqual(call['config'].model_dump(exclude_none=True, exclude_unset=True), {
                'system_instruction': original.INSTRUCTIONS, 'max_output_tokens': 2048,
                'response_mime_type': 'text/plain', 'automatic_function_calling': {'disable': True},
            })
        for line in ('MODEL: gemini-returned-version', 'GENERATED SUMMARY: Synthetic summary.',
                     'input_tokens: 12000', 'cached_tokens: 10000', 'output_tokens: 50',
                     'thinking_tokens: 100', 'total_tokens: 12150'):
            self.assertIn(line, output.getvalue())
        self.assertNotIn('Private thought', output.getvalue())
        self.assertLess(output.getvalue().index('ARTICLE ID: 31'), output.getvalue().index('ARTICLE ID: 30'))

    def test_validation_and_missing_metrics(self):
        invalid = [types.GenerateContentResponse(), response()]
        invalid[1].candidates[0].finish_reason = types.FinishReason.MAX_TOKENS
        blank = response(); blank.candidates[0].content.parts = [types.Part(text='  ')]
        invalid.append(blank)
        for reply in invalid:
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                gemini.parse_response(reply)
        client = Mock(); client.models.generate_content.side_effect = [blank, response()]
        with patch.object(gemini, 'select_targets', return_value=[(1, 'A', 'A'), (2, 'B', 'B')]), redirect_stdout(io.StringIO()) as output:
            gemini.run_experiment(None, client, references(), model='custom-model')
        self.assertIn('[validation error: blank_output]', output.getvalue())
        self.assertIn('GENERATED SUMMARY: Synthetic summary.', output.getvalue())
        self.assertEqual(client.models.generate_content.call_args.kwargs['model'], 'custom-model')
        for label in ('input_tokens', 'cached_tokens', 'output_tokens', 'thinking_tokens', 'total_tokens'):
            self.assertIn(f'{label}: unavailable', output.getvalue())
        with redirect_stdout(io.StringIO()) as output:
            gemini._report('text', response(usage_metadata=types.GenerateContentResponseUsageMetadata(cached_content_token_count=0)))
        self.assertIn('cached_tokens: 0', output.getvalue())

    def test_client_configuration_and_cli(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(gemini.genai, 'Client') as client:
            with self.assertRaisesRegex(ValueError, 'GEMINI_API_KEY'):
                gemini.make_client()
            client.assert_not_called()
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'synthetic-key'}), patch.object(gemini.genai, 'Client') as client:
            gemini.make_client()
            self.assertEqual(client.call_args.kwargs['api_key'], 'synthetic-key')
            self.assertEqual(client.call_args.kwargs['http_options'].timeout, 60000)
            self.assertEqual(client.call_args.kwargs['http_options'].retry_options.attempts, 1)
        for argv, model in (([], 'gemini-3.8-flash'), (['--model', 'custom-model', '--article-ids', '31', '30'], 'custom-model')):
            with patch('sys.argv', ['runner', *argv]), patch.object(gemini, 'make_client'), patch.object(gemini, 'make_engine'), patch.object(gemini, 'make_session_factory'), patch.object(gemini, 'load_references'), patch.object(gemini, 'run_experiment') as run:
                gemini.main()
                self.assertEqual(run.call_args.kwargs['model'], model)
                self.assertEqual(run.call_args.kwargs['article_ids'], [31, 30] if argv else None)

    def test_production_and_openai_do_not_import_google(self):
        code = """import sys
class BlockGoogle:
    def find_spec(self, fullname, *args):
        if fullname == 'google' or fullname.startswith('google.'):
            raise AssertionError('Unexpected Google dependency')
sys.meta_path.insert(0, BlockGoogle())
import news_backend.integrations.openai.summarization
import news_backend.services.summarization
import news_backend.experiments.editorial_examples
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('load_references', 'build_prefix', 'select_targets'):
            self.assertIs(getattr(gemini, name), getattr(original, name))


class GeminiSelectionTests(PostgresTestCase):
    def test_order_validation_and_no_writes(self):
        refs = references()
        with self.factory.begin() as session:
            for url in ('https://kun.uz/a', 'https://kun.uz/b', refs[0]['source_url']):
                session.add(Article(source='kun_uz', source_url=url, title=url, content='Body',
                                    published_at=datetime.now(timezone.utc)))
        def snapshot():
            with self.factory() as session:
                return (session.execute(select(Article.__table__)).all(),
                        session.execute(select(Summary.__table__)).all())
        before = snapshot()
        client = Mock()
        def generate(**kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return response()
        client.models.generate_content.side_effect = generate
        with redirect_stdout(io.StringIO()) as output:
            gemini.run_experiment(self.factory, client, refs, model='gemini-3.8-flash', article_ids=[2, 1])
        self.assertLess(output.getvalue().index('ARTICLE ID: 2'), output.getvalue().index('ARTICLE ID: 1'))
        self.assertEqual(snapshot(), before)
        client.reset_mock()
        for ids in ([3], [999], [1, 1]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                gemini.run_experiment(self.factory, client, refs, model='gemini-3.8-flash', article_ids=ids)
        client.models.generate_content.assert_not_called()
        with patch.object(gemini, 'select_targets', return_value=[]), redirect_stdout(io.StringIO()):
            gemini.run_experiment(self.factory, client, refs, model='gemini-3.8-flash')
        client.models.generate_content.assert_not_called()
