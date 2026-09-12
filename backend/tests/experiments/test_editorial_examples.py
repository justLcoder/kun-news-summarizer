import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
from news_backend.experiments.editorial_examples import load_references, build_prefix, parse_response, run_experiment
from news_backend.integrations.openai.summarization import SummaryValidationError


def references():
    return [dict(id=i, title=f'Title {i}', content=f'Full source {i}\nSecond paragraph',
                 source_url=f'https://kun.uz/reference/{i}', published_at='2026-09-12',
                 central_story=f'Story {i}', must_keep=[f'Keep {i}'], should_omit=[f'Omit {i}'],
                 ideal_summary=f'Summary {i}') for i in range(101, 116)]


def response():
    return N(status='completed', model='gpt-5-mini', output=[N(type='message', role='assistant',
        status='completed', content=[N(type='output_text', text='Test summary.')])],
        usage=N(input_tokens=12000, input_tokens_details=N(cached_tokens=10000), output_tokens=50))


class EditorialTests(unittest.TestCase):
    def load(self, articles, annotations):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.json', Path(directory)/'b.json'
            a.write_text(json.dumps(articles)); b.write_text(json.dumps(annotations))
            return load_references(a, b)

    def test_join_and_order(self):
        rows = references()
        articles = [{k: r[k] for k in ('id', 'title', 'content', 'source_url', 'published_at')} for r in rows]
        annotations = [{k: r[k] for k in ('id', 'central_story', 'must_keep', 'should_omit', 'ideal_summary')} for r in rows]
        joined = self.load(articles[::-1], annotations[5:]+annotations[:5])
        self.assertEqual(joined, rows)
        self.assertEqual(build_prefix(joined), build_prefix(joined[::-1]))
        prefix = build_prefix(joined)
        for row in rows:
            for field in ('title', 'content', 'central_story', 'must_keep', 'should_omit', 'ideal_summary'):
                self.assertIn(json.dumps(row[field], ensure_ascii=False), prefix)
        for label in ('TITLE', 'FULL ARTICLE', 'CENTRAL STORY', 'MUST KEEP', 'SHOULD OMIT', 'IDEAL SUMMARY'):
            self.assertEqual(prefix.count(label + ':'), 15)

    def test_bad_ids_and_fields(self):
        rows = references()
        for other in (rows[:-1], rows[:-1]+[rows[0]], rows[:-1]+[dict(rows[-1], id=999)],
                      rows[:-1]+[dict(rows[-1], id=None)], rows[:-1]+[dict(rows[-1], must_keep='wrong')]):
            with self.subTest(other=other[-1]['id']), self.assertRaises(ValueError):
                self.load(rows, other)

    def test_request_prefix_usage_and_no_truncation(self):
        client = Mock(); client.responses.create.return_value = response()
        with patch('news_backend.experiments.editorial_examples.select_targets', return_value=[(1,'Target A','Body A'),(2,'Target B','Body B')]), redirect_stdout(io.StringIO()) as output:
            run_experiment(Mock(), client, references())
        calls = [c.kwargs for c in client.responses.create.call_args_list]
        self.assertEqual(calls[0]['input'][0], calls[1]['input'][0])
        self.assertEqual(calls[0]['instructions'], calls[1]['instructions'])
        self.assertEqual(calls[0]['input'][1]['content'], 'TITLE:\nTarget A\n\nARTICLE:\nBody A')
        self.assertEqual(calls[0]['truncation'], 'disabled')
        self.assertEqual(calls[0]['prompt_cache_key'], 'uz-news-editorial-examples-v1')
        self.assertEqual(calls[0]['model'], 'gpt-5-mini')
        self.assertFalse(calls[0]['store']); self.assertNotIn('tools', calls[0])
        for text in ('ARTICLE ID: 1', 'TITLE: Target A', 'GENERATED SUMMARY: Test summary.', 'cached_tokens: 10000', 'input_tokens: 12000', 'output_tokens: 50'):
            self.assertIn(text, output.getvalue())

    def test_invalid_response(self):
        refused = response(); refused.output[0].content = [N(type='refusal')]
        incomplete = response(); incomplete.status = 'incomplete'
        blank = response(); blank.output = []
        for value in (refused, incomplete, blank, N(status='completed', output=None), N()):
            with self.subTest(value=value), self.assertRaises(SummaryValidationError): parse_response(value)

    def test_production_imports_without_private_files(self):
        code = """from unittest.mock import patch
from pathlib import Path
original = Path.read_text
def guarded(path, *args, **kwargs):
    if '.local' in path.parts or path.name.startswith('reference_'):
        raise AssertionError('No reference reads')
    return original(path, *args, **kwargs)
with patch.object(Path, 'read_text', guarded):
    import news_backend.integrations.openai.summarization
    import news_backend.services.summarization
    import news_backend.experiments.editorial_examples
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', code], cwd=directory, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
