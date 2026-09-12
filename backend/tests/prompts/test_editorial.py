import json
import tempfile
import unittest
from pathlib import Path
from news_backend.prompts import editorial
from news_backend.experiments import editorial_examples as benchmark
from tests.experiments.test_editorial_examples import references


class PromptTests(unittest.TestCase):
    def test_equivalence_and_version(self):
        rows = references()
        self.assertEqual(editorial.INSTRUCTIONS, benchmark.INSTRUCTIONS)
        self.assertEqual(editorial.build_prefix(rows), benchmark.build_prefix(rows))
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'articles.json', Path(directory)/'annotations.json'
            a.write_text(json.dumps(rows)); b.write_text(json.dumps(rows))
            first = editorial.load_prompt(a, b)
            b.write_text(json.dumps(rows[::-1]))
            self.assertEqual(first, editorial.load_prompt(a, b))
            rows[0]['ideal_summary'] = 'Changed'
            b.write_text(json.dumps(rows))
            self.assertNotEqual(first.version, editorial.load_prompt(a, b).version)
            b.write_text(json.dumps(rows[:-1]))
            with self.assertRaises(ValueError): editorial.load_prompt(a, b)
