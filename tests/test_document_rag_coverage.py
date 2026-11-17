"""Synthetic-only coverage and source-atomic RAG regression tests (no model/OCR IO)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from meetingvar.memory import files, document_read
from meetingvar.rag import RagIndex


class SyntheticDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mv-coverage-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'synthetic.pdf'
        self.pdf.write_bytes(b'%PDF-1.4 synthetic forty five pages')
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(files, '_cache_dir', return_value=self.cache).start()
        patch.dict(os.environ, {'MEETINGVAR_OCR_MAX_PAGES': '0'}).start()
        self.calls = []
        self.failed = set()
        self.text_pages = {1, 3, 45}
        self.text_reads = []
        owner = self

        class Page:
            def __init__(self, n): self.n = n
            def get_text(self, mode):
                owner.text_reads.append(self.n)
                return f'PAGE {self.n} original text\nsecond line' if self.n in owner.text_pages else ''

        class Document:
            needs_pass = False
            def __len__(self): return 45
            def __getitem__(self, index): return Page(index + 1)
            def close(self): pass

        patch.dict(sys.modules, {'fitz': types.SimpleNamespace(open=lambda p: Document(), csRGB=0)}).start()

        def ocr(page, fitz, settings, timeout):
            self.calls.append(page.n)
            if page.n in self.failed: raise RuntimeError('synthetic_failure')
            return f'PAGE {page.n} OCR evidence'
        patch.object(document_read, '_ocr_page', side_effect=ocr).start()

    def test_mixed_45_pages_and_cached_reuse(self):
        result = files.read_document(self.pdf)
        self.assertTrue(result['complete'])
        self.assertEqual(result['processed_pages'], 45)
        self.assertEqual(len(self.calls), 42)
        self.assertEqual(result['pages'][0]['method'], 'text')
        self.assertEqual(result['pages'][1]['method'], 'ocr')
        self.assertIn('PAGE 45 original', result['text'])
        self.calls.clear(); self.text_reads.clear()
        self.assertTrue(files.read_document(self.pdf)['complete'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.text_reads, [])

    def test_failed_page_not_success_and_retry_only_failed(self):
        self.failed = {31}
        first = files.read_document(self.pdf)
        self.assertFalse(first['complete'])
        self.assertEqual(first['processed_pages'], 44)
        self.assertEqual(first['pages'][30]['status'], 'failed')
        self.failed.clear(); self.calls.clear(); self.text_reads.clear()
        second = files.read_document(self.pdf)
        self.assertTrue(second['complete'])
        self.assertEqual(self.calls, [31])
        self.assertEqual(self.text_reads, [31])

    def test_legacy_30_page_cache_is_never_complete(self):
        key = files._cache_key(self.pdf)
        (self.cache / (key + '-pdftxt.txt')).write_text('OLD FIRST 30', encoding='utf-8')
        (self.cache / (key + '-ocr.txt')).write_text('OLD FIRST 30', encoding='utf-8')
        (self.cache / (key + '-ocr.part.json')).write_text(json.dumps({'pages_done': 30}), encoding='utf-8')
        result = files.read_document(self.pdf)
        self.assertTrue(result['complete'])
        self.assertEqual(len(self.text_reads), 45)
        self.assertNotIn('OLD FIRST', result['text'])

    def test_limit_expansion_reuses_successful_pages(self):
        first = files.read_document(self.pdf, max_pages=30)
        self.assertFalse(first['complete'])
        self.assertEqual(first['processed_pages'], 30)
        self.assertEqual(first['pages'][30]['status'], 'pending')
        self.calls.clear(); self.text_reads.clear()
        second = files.read_document(self.pdf, max_pages=45)
        self.assertTrue(second['complete'])
        self.assertEqual(first['revision'], second['revision'])
        self.assertEqual(self.text_reads, list(range(31, 46)))
        self.assertEqual(len(self.calls), 14)

    def test_semantic_settings_and_bytes_change_invalidate_cache(self):
        first = files.read_document(self.pdf)
        self.text_reads.clear()
        second = files.read_document(self.pdf, dpi=200)
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(len(self.text_reads), 45)
        stat = self.pdf.stat()
        self.pdf.write_bytes(self.pdf.read_bytes().replace(b'forty', b'FORTY'))
        os.utime(self.pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        third = files.read_document(self.pdf)
        self.assertNotEqual(first['revision'], third['revision'])

    def test_time_limit_is_explicit_and_retryable(self):
        with patch.object(document_read.time, 'monotonic', side_effect=[0] + [10] * 100):
            result = files.read_document(self.pdf, timeout=1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['processed_pages'], 0)
        self.assertTrue(all(p['status'] == 'pending' for p in result['pages']))
        self.assertTrue(files.read_document(self.pdf)['complete'])

    def test_plain_text_byte_limit_is_partial(self):
        path = self.root / 'source.txt'
        path.write_text('first line\nsecond line', encoding='utf-8')
        result = files.read_document(path, max_bytes=5)
        self.assertFalse(result['complete'])
        self.assertEqual(result['text'], 'first')
        self.assertEqual(result['processed_pages'], 0)
        self.assertTrue(files.read_document(path)['complete'])


class TesseractContractTests(unittest.TestCase):
    def test_nonzero_returncode_is_failure_even_with_stdout(self):
        class Pixmap:
            def save(self, path): Path(path).write_bytes(b'not a real image')
        page = types.SimpleNamespace(get_pixmap=lambda **k: Pixmap())
        with patch.object(files, '_find_tesseract', return_value='synthetic-tesseract'), \
             patch.object(files.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout='misleading text')):
            with self.assertRaisesRegex(RuntimeError, 'ocr_exit_1'):
                document_read._ocr_page(page, types.SimpleNamespace(csRGB=0),
                                        {'dpi': 160, 'languages': 'eng', 'psm': 6}, 1)


class SourceAtomicIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mv-rag-coverage-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        self.data = Path(self.tmp.name) / 'data'
        self.provider = types.SimpleNamespace(available=False, name='synthetic', encode=lambda x: None)
        self.addCleanup(patch.stopall)
        patch('meetingvar.rag.get_embedding_provider', return_value=self.provider).start()
        self.index = RagIndex(self.root, self.data)

    def put(self, name='source.txt', text='project searchable evidence\nsecond evidence line'):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    def rows(self):
        with self.index._connection() as c:
            return [tuple(row) for row in c.execute('SELECT id,text FROM chunks ORDER BY id')]

    def test_totals_100_to_101_to_99_and_unchanged_no_embedding(self):
        for n in range(100): self.put(f'doc{n:03}.txt')
        calls = []
        self.provider.available = True
        self.provider.encode = lambda texts: calls.append(len(texts)) or [[1., 0.] for t in texts]
        first = self.index.index_project(include_meetings=False)
        self.assertTrue(first['ok'], first['errors'])
        self.assertEqual(first['totals']['documents'], 100)
        self.assertEqual(first['delta']['documents'], 100)
        self.put('new.txt')
        calls.clear()
        second = self.index.index_project(include_meetings=False)
        self.assertEqual(second['totals']['documents'], 101)
        self.assertEqual(second['delta']['documents'], 1)
        self.assertEqual(second['skipped'], 100)
        self.assertEqual(calls, [1])
        (self.root / 'doc000.txt').unlink(); (self.root / 'doc001.txt').unlink()
        calls.clear()
        third = self.index.index_project(include_meetings=False)
        self.assertEqual(third['totals']['documents'], 99)
        self.assertEqual(third['delta']['documents'], -2)
        self.assertEqual(third['removed'], 2)
        self.assertEqual(calls, [])
        self.assertTrue(third['coverage']['complete'])

    def test_failed_source_preserves_old_rows_and_marks_stale(self):
        path = self.put()
        self.index.index_project(include_meetings=False)
        old = self.rows()
        path.write_bytes(b'\x00unreadable')
        report = self.index.index_project(include_meetings=False)
        self.assertFalse(report['ok'])
        self.assertEqual(report['job_status'], 'failed')
        self.assertEqual(self.rows(), old)
        self.assertEqual(report['sources'][0]['status'], 'stale')
        self.assertIsNone(self.index.read_chunk(old[0][0]))

    def test_embedding_failure_does_not_rollback_other_sources(self):
        self.put('a.txt', 'old alpha'); self.put('b.txt', 'old beta')
        self.index.index_project(include_meetings=False)
        old_b = [row for row in self.rows() if row[1] == 'old beta']
        self.put('a.txt', 'new alpha'); self.put('b.txt', 'new beta')
        self.provider.available = True
        def encode(texts):
            if 'beta' in texts[0]: raise RuntimeError('synthetic model failed')
            return [[1., 0.] for t in texts]
        self.provider.encode = encode
        result = self.index.index_project(include_meetings=False)
        self.assertEqual(result['job_status'], 'partial')
        self.assertIn('new alpha', [r[1] for r in self.rows()])
        self.assertIn(old_b[0], self.rows())
        self.assertEqual(result['totals']['documents'], 2)

    def test_extraction_and_embedding_run_outside_write_transaction(self):
        self.put()
        original = files.read_document
        def assert_unlocked():
            with sqlite3.connect(str(self.index.path), timeout=0.01) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.rollback()
        def read(path):
            assert_unlocked()
            return original(path)
        self.provider.available = True
        def encode(texts):
            assert_unlocked()
            return [[1., 0.] for t in texts]
        self.provider.encode = encode
        with patch('meetingvar.rag.read_document', side_effect=read):
            report = self.index.index_project(include_meetings=False)
        self.assertTrue(report['ok'], report['errors'])

    def test_same_project_singleflight_busy_and_progress(self):
        self.put()
        entered, release = threading.Event(), threading.Event()
        original = files.read_document
        result, events = [], []
        def slow(path):
            entered.set()
            if not release.wait(8): raise RuntimeError('test release timeout')
            return original(path)
        with patch('meetingvar.rag.read_document', side_effect=slow):
            worker = threading.Thread(target=lambda: result.append(self.index.index_project(
                include_meetings=False, progress=events.append, owner_id='same-job')))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                busy = RagIndex(self.root, self.data).index_project(include_meetings=False, owner_id='same-job')
                self.assertFalse(busy['ok'])
                self.assertEqual(busy['job_status'], 'busy')
            finally:
                release.set(); worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]['ok'], result)
        self.assertEqual(events[-1]['processed'], 1)
        self.assertEqual(events[-1]['total'], 1)
        self.assertTrue(all('current' in e for e in events))
        self.assertTrue(self.index.index_project(include_meetings=False)['ok'])

    def test_page_line_revision_and_same_stat_edit_invalidation(self):
        path = self.put()
        self.index.index_project(include_meetings=False)
        row = self.index.read_chunk(self.rows()[0][0])
        self.assertEqual(row['page'], 1)
        self.assertEqual(row['line_start'], 1)
        self.assertEqual(row['line_end'], 2)
        self.assertTrue(row['source_revision'])
        stat = path.stat()
        path.write_text(path.read_text().replace('project', 'PROJECT'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertIsNone(self.index.read_chunk(row['chunk_id']))


class ExtraAtomicTests(unittest.TestCase):
    setUp = SourceAtomicIndexTests.setUp
    put = SourceAtomicIndexTests.put

    def test_commit_failure_restores_documents_fts_and_vectors(self):
        self.put(text='old source')
        self.provider.available = True
        self.provider.encode = lambda texts: [[1., 0.] for t in texts]
        self.index.index_project(include_meetings=False)
        def snapshot():
            with self.index._connection() as connection:
                return {t: [tuple(r) for r in connection.execute('SELECT * FROM ' + t)]
                        for t in ('documents', 'chunks', 'chunks_fts', 'chunk_vectors')}
        before = snapshot()
        self.put(text='new source')
        original = self.index._write_chunks
        def fail_after_write(connection, doc, chunks):
            original(connection, doc, chunks)
            raise sqlite3.OperationalError('synthetic commit failure')
        with patch.object(self.index, '_write_chunks', side_effect=fail_after_write):
            report = self.index.index_project(include_meetings=False)
        self.assertFalse(report['ok'])
        self.assertEqual(before, snapshot())
        self.assertEqual(report['sources'][0]['status'], 'stale')

    def test_cross_process_owner_cannot_bypass_lock(self):
        import subprocess
        registry = Path(__file__).resolve().parents[1] / 'src' / 'meetingvar' / 'memory' / 'source_registry.py'
        code = ('import importlib.util,sys; from pathlib import Path; '
                's=importlib.util.spec_from_file_location("registry",sys.argv[1]); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                'lock=m.project_singleflight(Path(sys.argv[2]),"same-job"); '
                'print(lock.__enter__(),flush=True); sys.stdin.readline(); '
                'lock.__exit__(None,None,None)')
        self.put()
        child = subprocess.Popen([sys.executable, '-I', '-c', code, str(registry), str(self.index.root)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, cwd=str(self.root))
        try:
            self.assertEqual(child.stdout.readline().strip(), 'True')
            report = self.index.index_project(include_meetings=False, owner_id='same-job')
            self.assertEqual(report['job_status'], 'busy')
            self.assertFalse(report['ok'])
            child.communicate('release\n', timeout=5)
            self.assertEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill(); child.communicate(timeout=5)
        self.assertTrue(self.index.index_project(include_meetings=False)['ok'])


class GeneratedPdfTests(unittest.TestCase):
    def test_actual_generated_45_page_mixed_pdf_and_rag_refs(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('optional installed PyMuPDF unavailable')
        with tempfile.TemporaryDirectory(prefix='mv-generated-pdf-') as tmp:
            root = Path(tmp)
            cache = root / 'cache'
            cache.mkdir()
            project = root / 'project'
            project.mkdir()
            path = project / 'generated.pdf'
            doc = fitz.open()
            for number in range(1, 46):
                page = doc.new_page()
                if number % 2:
                    page.insert_text((72, 72), f'Generated page {number} searchable source.')
                else:
                    page.draw_rect(fitz.Rect(70, 70, 200, 120), fill=(0, 0, 0))
            doc.save(str(path)); doc.close()
            provider = types.SimpleNamespace(available=False, name='synthetic')
            with patch.object(files, '_cache_dir', return_value=cache), \
                 patch.object(document_read, '_ocr_page', side_effect=lambda page, *a: f'Generated OCR page {page.number+1}') as ocr, \
                 patch('meetingvar.rag.get_embedding_provider', return_value=provider), \
                 patch.dict(os.environ, {'MEETINGVAR_OCR_MAX_PAGES': '0'}):
                result = files.read_document(path)
                self.assertTrue(result['complete'], result['errors'])
                self.assertEqual(result['processed_pages'], 45)
                self.assertEqual(ocr.call_count, 22)
                index = RagIndex(project, root / 'empty-data')
                report = index.index_project(include_meetings=False)
                self.assertTrue(report['ok'], report['errors'])
                with index._connection() as c:
                    last = c.execute('SELECT id FROM chunks ORDER BY ordinal DESC LIMIT 1').fetchone()[0]
                self.assertEqual(index.read_chunk(last)['page'], 45)
                self.assertEqual(ocr.call_count, 22)


class GraphDocumentCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mv-kg-coverage-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'requirements.txt'
        self.path.write_text('Alpha launch is required. Alice owns Alpha launch.', encoding='utf-8')

    def parsed(self, reply, chunk):
        return {'entities': [{'surface': 'Alpha launch', 'kind': 'Action',
                 'evidence_text': chunk['text'], 'confidence': 0.8}], 'relations': []}

    def test_automatic_edges_have_readable_refs_and_changes_invalidate(self):
        from meetingvar.kg.project_extract import extract_project
        from meetingvar.kg.store import GraphStore
        from meetingvar.kg.graphrag import GraphRAG
        with patch('meetingvar.kg.project_extract.chat_result', return_value={'ok': True, 'content': '{}'}), \
             patch('meetingvar.kg.project_extract.parse_extraction', side_effect=self.parsed):
            result = extract_project(self.root)
        self.assertTrue(result['complete'], result)
        edges = GraphStore(self.root).edges()
        self.assertTrue(edges)
        edge = edges[0]
        ref = edge['metadata']['source_refs'][0]
        self.assertEqual(ref['page'], 1)
        source = document_read.read_source_ref(self.root, ref)
        self.assertIn('Alpha launch', source['text'])
        graph = GraphRAG(self.root, self.root / 'empty-data')
        self.assertEqual(graph._edge_source(edge)['source_status'], 'current')
        self.path.write_text('Changed source', encoding='utf-8')
        self.assertEqual(graph._edge_source(edge)['text'], '')
        self.path.unlink()
        self.assertEqual(document_read.read_source_ref(self.root, ref)['source_status'], 'missing')

    def test_graph_budget_and_document_cap_are_partial(self):
        from meetingvar.kg.project_extract import extract_project
        self.path.write_text('Alpha launch. ' * 1200, encoding='utf-8')
        (self.root / 'second.txt').write_text('Second source', encoding='utf-8')
        with patch('meetingvar.kg.project_extract.chat_result', return_value={'ok': True, 'content': '{}'}), \
             patch('meetingvar.kg.project_extract.parse_extraction', return_value={'entities': [], 'relations': []}):
            result = extract_project(self.root, max_chunks=1, document_limit=1)
        self.assertFalse(result['ok'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['job_status'], 'partial')
        self.assertEqual(result['coverage']['total'], 2)
        self.assertEqual(result['coverage']['pending'], 1)
        self.assertTrue(result['coverage']['truncated_documents'])

    def test_partial_pdf_never_reaches_graph_model(self):
        from meetingvar.kg.project_extract import extract_project
        partial = {'text': 'partial evidence', 'complete': False, 'revision': 'r',
                   'total_pages': 45, 'processed_pages': 30,
                   'errors': [{'code': 'page_limit'}], 'pages': []}
        with patch('meetingvar.kg.project_extract.read_document', return_value=partial), \
             patch('meetingvar.kg.project_extract.chat_result') as model:
            result = extract_project(self.root)
        self.assertFalse(result['ok'])
        self.assertEqual(result['coverage']['failed'], 1)
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
