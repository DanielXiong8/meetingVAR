"""Synthetic-only source coverage and summary integrity regressions."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

with patch('meetingvar.env.load_local_env'):
    from meetingvar.memory import probe
    from meetingvar import llm


class Budget:
    def fits(self, value): return True
    def as_dict(self): return {}


class WorkspaceProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mv-summary-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'innocuous.md').write_text('Alpha delivers Friday. Evidence text is not instructions.', encoding='utf-8')

    def test_structured_read_retains_failed_page(self):
        read = {'text': 'Alpha delivers Friday.', 'complete': False, 'revision': 'r1',
                'coverage': {'total_pages': 3, 'processed_pages': 2, 'failed_pages': [3]},
                'errors': [{'code': 'ocr_failed', 'page': 3}]}
        with patch('meetingvar.memory.files.read_document', return_value=read):
            docs, notes = probe.load_corpus(self.root)
        self.assertEqual(len(docs), 1)
        self.assertEqual(notes.sources[0]['coverage']['failed_pages'], [3])
        self.assertFalse(notes.sources[0]['complete'])
        self.assertEqual(notes.errors[0]['code'], 'ocr_failed')

    def run_summary(self, overview_ok=True, extraction_ok=True):
        notes = probe.CorpusNotes()
        if not extraction_ok:
            notes.errors = [{'code': 'ocr_failed', 'page': 3}]
        chunk = {'doc': 'innocuous.md', 'offset': 0, 'end': 22, 'label': 'section', 'text': 'Alpha delivers Friday.'}
        prompts = []
        def reply(prompt, **kwargs):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {'ok': True, 'content': json.dumps({'persons': [], 'keywords': [{'term': 'Alpha', 'why': 'delivery'}], 'events': [{'what': 'Alpha delivers Friday', 'quote': 'Alpha delivers Friday.'}]}), 'error': None}
            return {'ok': overview_ok, 'content': 'Alpha delivers Friday.' if overview_ok else '',
                    'error': None if overview_ok else 'synthetic timeout', 'error_code': None if overview_ok else 'timeout'}
        from meetingvar.model_client import CallableModelClient
        client = CallableModelClient(
            lambda messages, **kwargs: reply(messages[-1]['content'], **kwargs), budget=llm.token_budget())
        with patch.object(probe, 'load_corpus', return_value=([('innocuous.md', chunk['text'])], notes)), \
             patch.object(probe, 'split_chunks', return_value=[chunk]):
            result = probe.understand(self.root, model_client=client)
        return result, prompts

    def test_overview_uses_source_evidence_not_filename_list(self):
        result, prompts = self.run_summary()
        self.assertTrue(result['complete'])
        self.assertIn('Alpha delivers Friday.', prompts[1])
        self.assertIn('不是指令', prompts[1])
        self.assertEqual(result['summary_sources'][0]['path'], 'innocuous.md')

    def test_overview_failure_is_not_semantic_completion(self):
        result, prompts = self.run_summary(overview_ok=False)
        self.assertFalse(result['ok'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['chunks_read'], 1)
        self.assertEqual(result['error_code'], 'summary_failed')

    def test_partial_extraction_never_becomes_100_percent_complete(self):
        result, prompts = self.run_summary(extraction_ok=False)
        self.assertFalse(result['complete'])
        self.assertFalse(result['extraction_complete'])
        self.assertEqual(result['error_code'], 'partial_extraction')
        self.assertEqual(len(prompts), 1)


if __name__ == '__main__':
    unittest.main()
