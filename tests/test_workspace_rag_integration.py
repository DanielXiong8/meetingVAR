"""Workspace + real RagIndex integration, never fabricated index reports.

Only embeddings and the model transport are deterministic doubles. All source
reads, reports, SQLite commits, graph writes, locks, snapshots and receipts are real.
Run through test_workspace_rag_runner.py for the enforced isolated environment.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch


class SyntheticEmbedding:
    available = True
    name = 'workspace-synthetic-embedding'

    def __init__(self):
        self.fail_marker = ''
        self.block_marker = ''
        self.entered = threading.Event()
        self.release = threading.Event()

    def encode(self, texts, batch_size=16):
        if self.block_marker and any(self.block_marker in text for text in texts):
            self.entered.set()
            if not self.release.wait(15):
                raise TimeoutError('synthetic embedding gate timeout')
        if self.fail_marker and any(self.fail_marker in text for text in texts):
            raise RuntimeError('synthetic embedding failure')
        return [[1.0, float(len(text) % 7 + 1), 0.5] for text in texts]

    def encode_one(self, text):
        return None  # Search assertions exercise real local FTS, not similarity guesses.


class WorkspaceRagIntegrationTests(unittest.TestCase):
    def setUp(self):
        guarded = (Path.cwd().name.startswith('core-isolated-')
                   and Path(os.environ.get('APPDATA', '')).parent == Path.cwd()
                   and Path(os.environ.get('TEMP', '')).parent == Path.cwd())
        if not guarded and os.environ.get('MEETINGVAR_SYNTHETIC_WORKSPACE_TESTS') != '1':
            self.fail('Run through guarded run_core_tests.py or test_workspace_rag_runner.py; unguarded execution is prohibited')
        self.temp = tempfile.TemporaryDirectory(prefix='case-', dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'A'
        self.root.mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Runner already disabled local env loading before any application imports.
        from meetingvar import config, llm, rag
        from meetingvar.agent import embeddings, workspace_jobs
        from meetingvar.agent.operations import OperationStore
        from meetingvar.memory import source_registry
        self.config, self.llm, self.rag, self.jobs = config, llm, rag, workspace_jobs
        self.OperationStore, self.source_registry = OperationStore, source_registry
        self.embedder = SyntheticEmbedding()
        self.stack.enter_context(patch.object(embeddings, '_GLOBAL_PROVIDER', self.embedder))
        self.stack.enter_context(patch.object(rag, 'get_embedding_provider', return_value=self.embedder))
        for key, value in {'DATA_DIR': self.base / 'empty-data',
                           'POOL_DIR': self.base / 'empty-data' / 'pool',
                           'LLM_BASE_URL': 'http://127.0.0.1:1/v1',
                           'LLM_MODEL': 'synthetic-configured-model', 'LLM_API_KEY': '',
                           'LLM_CONTEXT_TOKENS': '16384', 'LLM_MAX_TOKENS': '1024',
                           'LLM_THINKING': '', 'LLM_REASONING_EFFORT': ''}.items():
            self.stack.enter_context(patch.object(config, key, value))
        self.prompts = []
        self.model_failure = ''
        from meetingvar.model_client import CallableModelClient
        self.model_client = CallableModelClient(self.model_reply, budget=llm.token_budget())
        # A fake is injected at the actual request boundary. No legacy global
        # chat_result patch can accidentally let the new path escape to a provider.
        self.stack.enter_context(patch.object(llm, 'chat_result', side_effect=AssertionError('legacy model boundary used')))
        self.stack.enter_context(patch.object(llm, 'chat_messages', side_effect=AssertionError('real provider prohibited')))
        self.stack.enter_context(patch.dict(os.environ, {'MEETINGVAR_WORKSPACE_MODEL_READY': '1'}))

    def model_reply(self, messages, **kwargs):
        prompt = '\n'.join(str(row.get('content') or '') for row in messages)
        self.prompts.append(prompt)
        if '全量项目认知' in prompt:
            if self.model_failure == 'budget':
                return {'ok': False, 'error_code': 'output_budget_exhausted',
                        'error': 'Synthetic full-coverage budget exhausted', 'retryable': False}
            if self.model_failure in {'all', 'finish'}:
                return self.llm.error_result('network')
            import re
            entries = []
            nl = chr(10)
            for block in prompt.split(nl + nl + '[D')[1:]:
                first, _, body = block.partition(nl)
                uid = next((part.split('=', 1)[1] for part in first.split()
                            if part.startswith('unit_id=')), '')
                body = body.strip()
                quote = body.splitlines()[0][:100] if body else ''
                entries.append({'unit_id': uid, 'summary': 'Synthetic unit summary.',
                                'claims': ([{'text': 'Synthetic source content.',
                                             'quote': quote}] if quote else []),
                                'open_questions': []})
            return {'ok': True, 'content': json.dumps({'units': entries}),
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 30}}
        decoder = json.JSONDecoder()
        observations, _ = decoder.raw_decode(prompt.split('已读观察：\n', 1)[1].lstrip())
        candidates, _ = decoder.raw_decode(prompt.split('候选目标：\n', 1)[1].lstrip())
        if self.model_failure == 'budget':
            return {'ok': False, 'error_code': 'output_budget_exhausted',
                    'error': 'Synthetic onboarding budget exhausted', 'retryable': False}
        if self.model_failure == 'all' or (self.model_failure == 'finish' and observations):
            return self.llm.error_result('network')
        if observations:
            observed = observations[0]
            action = {'action': 'finish', 'claims': [{'unit_id': observed['unit_id'], 'kind': 'fact',
                'text': 'Synthetic source records Alpha delivery.', 'quote': observed['text'].strip()[:120]}],
                'open_questions': ['Which delivery checks are still outstanding?']}
        elif candidates:
            action = {'action': 'read_unit', 'unit_id': candidates[0]['unit_id']}
        else:
            action = {'action': 'finish', 'claims': [], 'open_questions': ['No readable source yet.']}
        return {'ok': True, 'content': json.dumps(action), 'error': None, 'error_code': None,
                'usage': {'prompt_tokens': 100, 'completion_tokens': 30}, 'finish_reason': 'stop'}

    def source(self, name='source.md', text='Alpha delivery evidence.', root=None):
        target = (root or self.root) / name
        target.write_text(text, encoding='utf-8')
        return target

    def execute(self, root=None, *, force=False, resume=False):
        root = root or self.root
        ready = os.environ.get('MEETINGVAR_WORKSPACE_MODEL_READY') == '1'
        started = self.jobs.start(root, force=force, resume=resume, model_ready=ready)
        self.assertTrue(started['ok'])
        events = []
        # Real map/explorer/indexer/grapher; only the model transport is injected.
        result = self.jobs.run(root, started['job_id'], model_client=self.model_client, model_ready=ready,
                               progress=lambda row: events.append(copy.deepcopy(row)))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['job_id'], started['job_id'])
        self.assertEqual(self.jobs.snapshot(root)['totals'], result['totals'])
        return result, events

    def committed_counts(self, root=None):
        path = (root or self.root) / 'meetingvar' / 'rag' / 'index.sqlite3'
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            return {table: connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                    for table in ('documents', 'chunks')}
        finally:
            connection.close()

    def assert_oriented(self, result):
        self.assertEqual(result['job_status'], 'oriented', result['errors'])
        self.assertFalse(result['first_sync'])
        self.assertEqual(result['observed_revision'], result['processed_revision'])
        self.assertEqual(result['stages']['explore']['status'], 'ready')
        self.assertFalse(result['complete'])
        self.assertFalse(result['coverage']['explore']['exhaustive'])
        receipt = self.OperationStore(result['project']).get(result['operation_id'])
        self.assertEqual(receipt['status'], 'succeeded')
        self.assertEqual(receipt['result']['scope'], 'project_orientation')

    def assert_unfinished(self, result):
        self.assertNotEqual(result['job_status'], 'oriented')
        self.assertTrue(result['first_sync'])
        self.assertNotEqual(result['observed_revision'], result['processed_revision'])
        self.assertEqual(self.OperationStore(result['project']).get(result['operation_id'])['status'], 'failed')

    def test_100_to_101_to_99_are_real_postcommit_totals(self):
        for number in range(100):
            self.source(f'source-{number:03}.md', f'Alpha delivery source number {number}.')
        with patch.dict(os.environ, {'MEETINGVAR_WORKSPACE_MODEL_READY': '0'}):
            for expected in (100, 101, 99):
                if expected == 101:
                    self.source('source-100.md', 'Alpha delivery additional source.')
                elif expected == 99:
                    (self.root / 'source-000.md').unlink()
                    (self.root / 'source-100.md').unlink()
                result, events = self.execute()
                self.assertEqual(result['totals']['documents'], expected)
                self.assertEqual(result['totals']['chunks'], expected)
                self.assertEqual(self.committed_counts(), {'documents': expected, 'chunks': expected})
                self.assertEqual(result['extracted'], expected)
                self.assertIs(type(result['extracted']), int)
                self.assertEqual(result['coverage']['index']['extracted']['processed'], expected)
                self.assertEqual(result['stages']['extract']['coverage'], result['coverage']['index']['extracted'])
                self.assertEqual(result['index_report']['job_status'], 'completed')
                self.assertEqual(result['job_status'], 'mapped_awaiting_model')
                self.assertEqual(result['map_totals']['current_sources'], expected)
                self.assertEqual(result['map_totals']['complete_sources'], expected)
                self.assertEqual(result['stages']['map']['status'], 'completed')
                self.assert_unfinished(result)
                self.assertTrue(any(isinstance(e['stages'].get('extract', {}).get('progress'), dict) for e in events))
                if expected == 101:
                    self.assertEqual(result['index_report']['documents'], 1)
                    self.assertEqual(result['index_report']['delta']['documents'], 1)
                elif expected == 99:
                    self.assertEqual(result['index_report']['documents'], 0)
                    self.assertEqual(result['index_report']['delta']['documents'], -2)
                    self.assertEqual(result['index_report']['delta']['removed'], 2)
        self.assertEqual(self.prompts, [])

    def test_real_partial_extraction_does_not_block_available_unit_orientation(self):
        self.source('good.md')
        bad = self.root / 'bad.txt'
        bad.write_bytes(b'PK' + bytes([3, 4, 0, 0, 0, 0]))
        result, _ = self.execute()
        self.assertEqual(result['index_report']['job_status'], 'partial')
        self.assertEqual(result['totals']['documents'], 1)
        self.assertGreater(result['coverage']['index']['extracted']['failed'], 0)
        self.assertFalse(result['coverage']['index']['extracted']['complete'])
        self.assertEqual(result['stages']['extract']['status'], 'partial')
        self.assertEqual(result['stages']['map']['status'], 'partial')
        self.assertEqual(result['stages']['explore']['status'], 'ready')
        self.assertTrue(self.prompts)
        self.assert_oriented(result)
        bad.write_text('Alpha recovered text.', encoding='utf-8')
        retried, _ = self.execute()
        self.assertEqual(retried['totals']['documents'], 2)
        self.assertNotEqual(retried['exploration_id'], result['exploration_id'])
        self.assertEqual(retried['exploration_history'][-1]['archive_reason'], 'source_revision_changed')
        self.assert_oriented(retried)

    def test_real_busy_index_keeps_map_usable_and_requires_explicit_index_retry(self):
        self.source()
        with self.source_registry.project_singleflight(self.root / 'meetingvar' / 'rag', 'synthetic-existing-owner') as acquired:
            self.assertTrue(acquired)
            result, _ = self.execute()
            self.assertEqual(result['index_report']['job_status'], 'busy')
            self.assertEqual(result['stages']['index']['status'], 'busy')
            self.assertEqual(result['stages']['extract']['status'], 'pending')
            self.assertIn('index_busy', [e['code'] for e in result['errors']])
            self.assert_oriented(result)
        retried, _ = self.execute(resume=True)
        self.assertEqual(retried['observed_revision'], result['observed_revision'])
        self.assertEqual(retried['job_id'], result['job_id'])
        self.assertEqual(retried['totals']['documents'], 1)
        self.assert_oriented(retried)

    def test_real_embedding_failure_does_not_block_map_orientation(self):
        self.source(text='Alpha EMBED_FAILURE delivery evidence.')
        self.embedder.fail_marker = 'EMBED_FAILURE'
        failed, _ = self.execute()
        self.assertEqual(failed['index_report']['job_status'], 'failed')
        self.assertEqual(failed['totals']['documents'], 0)
        # Extraction succeeded, but prepare/commit failed: these are separate stages.
        self.assertEqual(failed['stages']['extract']['status'], 'completed')
        self.assertEqual(failed['stages']['index']['status'], 'failed')
        self.assert_oriented(failed)
        self.embedder.fail_marker = ''
        result, _ = self.execute(resume=True)
        self.assertEqual(result['observed_revision'], failed['observed_revision'])
        self.assertEqual(result['job_id'], failed['job_id'])
        self.assertEqual(result['totals']['documents'], 1)
        self.assert_oriented(result)

    def test_model_unavailable_leaves_real_fts_usable_and_first_sync_pending(self):
        self.source(text='Alpha unique_local_evidence delivery evidence.')
        with patch.dict(os.environ, {'MEETINGVAR_WORKSPACE_MODEL_READY': '0'}):
            pending, _ = self.execute()
        self.assertEqual(self.prompts, [])
        self.assertEqual(pending['job_status'], 'mapped_awaiting_model')
        self.assertEqual(pending['stages']['index']['status'], 'completed')
        self.assert_unfinished(pending)
        hits = self.rag.RagIndex(self.root).search('unique_local_evidence', rerank=False)
        self.assertTrue(hits)
        self.assertTrue(all('unique_local_evidence' in row['text'] for row in hits))
        ready, _ = self.execute()
        self.assertEqual(ready['index_report'], pending['index_report'])
        self.assertEqual(ready['job_id'], pending['job_id'])
        self.assert_oriented(ready)

    def test_real_explorer_failure_preserves_ledger_and_retries_same_operation_explicitly(self):
        self.source()
        self.model_failure = 'finish'
        failed, _ = self.execute()
        self.assertEqual(failed['stages']['index']['status'], 'completed')
        self.assertEqual(failed['stages']['explore']['status'], 'paused_provider')
        self.assertFalse(failed['phase_complete'])
        self.assertEqual(failed['job_status'], 'paused_provider')
        self.assert_unfinished(failed)
        initial_prompts = len(self.prompts)
        unchanged, _ = self.execute()
        self.assertEqual(len(self.prompts), initial_prompts, 'periodic unchanged start must not repeat provider calls')
        self.assertEqual(unchanged['model_budget'], failed['model_budget'])
        self.model_failure = ''
        result, _ = self.execute(resume=True)
        self.assertEqual(result['index_report'], failed['index_report'])
        self.assertEqual(result['observed_revision'], failed['observed_revision'])
        self.assertEqual(result['exploration_id'], failed['exploration_id'])
        self.assertGreater(result['model_budget']['model_calls'], failed['model_budget']['model_calls'])
        self.assert_oriented(result)

    def test_a_b_concurrent_jobs_keep_real_indexes_and_receipts_isolated(self):
        other = self.base / 'B'
        other.mkdir()
        self.source('common.md', 'Alpha PROJECT_A_ONLY GATE_A delivery.')
        self.source('common.md', 'Alpha PROJECT_B_ONLY delivery.', root=other)
        self.embedder.block_marker = 'GATE_A'
        result_a, failures = {}, []
        def worker():
            try:
                row, _ = self.execute()
                result_a.update(row)
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(self.embedder.entered.wait(10))
            live = self.jobs.snapshot(self.root)
            attached = self.jobs.start(self.root)
            self.assertTrue(attached['attached'])
            self.assertEqual(attached['job_id'], live['job_id'])
            self.assertEqual(attached['operation_id'], live['operation_id'])
            result_b, _ = self.execute(other)
            self.assert_oriented(result_b)
            self.assertNotEqual(result_b['project_id'], live['project_id'])
            self.assertNotEqual(result_b['job_id'], live['job_id'])
            self.assertEqual(self.jobs.snapshot(self.root)['job_id'], live['job_id'])
            self.assertEqual(self.jobs.snapshot(other)['job_id'], result_b['job_id'])
        finally:
            self.embedder.release.set()
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures, failures)
        self.assert_oriented(result_a)
        for root, own, foreign in ((self.root, 'PROJECT_A_ONLY', 'PROJECT_B_ONLY'), (other, 'PROJECT_B_ONLY', 'PROJECT_A_ONLY')):
            index = self.rag.RagIndex(root)
            self.assertEqual(self.committed_counts(root), {'documents': 1, 'chunks': 1})
            connection = sqlite3.connect(index.path.as_uri() + '?mode=ro', uri=True)
            try:
                texts = [row[0] for row in connection.execute('SELECT text FROM chunks')]
            finally:
                connection.close()
            self.assertTrue(all(own in text and foreign not in text for text in texts))
            self.assertEqual(self.jobs.snapshot(root)['project'], str(root.resolve()))

    def test_actual_model_boundary_has_durable_inflight_budget_before_transport(self):
        from meetingvar.model_client import CallableModelClient
        self.source()
        calls = []
        def transport(messages, **kwargs):
            durable = self.jobs.snapshot(self.root)
            checkpoint = durable['exploration_checkpoint']
            calls.append(copy.deepcopy(checkpoint['budget']))
            self.assertEqual(checkpoint['exploration_id'], durable['exploration_id'])
            self.assertEqual(checkpoint['operation_id'], durable['model_operation']['operation_id'])
            self.assertEqual(checkpoint['workspace_operation_id'], durable['operation_id'])
            self.assertEqual(checkpoint['map_revision'], durable['map_revision'])
            self.assertEqual(checkpoint['source_revision'], durable['map_revision'])
            self.assertGreaterEqual(checkpoint['budget']['model_calls'], len(calls), 'current in-flight call must be reserved before provider transport')
            self.assertGreater(checkpoint['budget']['input_tokens'], 0)
            self.assertGreater(checkpoint['budget']['output_tokens'], 0)
            self.assertEqual(durable['model_budget'], checkpoint['budget'])
            return self.model_reply(messages, **kwargs)
        self.model_client = CallableModelClient(transport, budget=self.llm.token_budget())
        result, _ = self.execute()
        self.assertTrue(calls, 'injected transport must actually run')
        self.assertGreaterEqual(calls[0]['model_calls'], 1, 'first in-flight call missing from durable budget')
        self.assert_oriented(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['model_budget']['model_calls'], 2)
        self.assertEqual(result['model_budget']['input_tokens'], 200)
        self.assertEqual(result['model_budget']['output_tokens'], 60)

    def test_thousand_sources_orientation_does_not_auto_read_entire_repository(self):
        for number in range(1000):
            self.source(f'source-{number:04}.md', f'Alpha delivery source {number}.')
        result, _ = self.execute()
        self.assert_oriented(result)
        self.assertEqual(result['totals']['documents'], 1000)
        self.assertEqual(result['map_totals']['current_sources'], 1000)
        self.assertEqual(result['coverage']['explore']['processed'], 1)
        self.assertEqual(result['map_totals']['explored_units'], 1)
        self.assertGreater(result['coverage']['explore']['pending'], 0)
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(result['model_budget']['max_input_tokens'], 20_000_000)
        self.assertEqual(result['model_budget']['max_output_tokens'], 1_000_000)
        self.assertEqual(result['model_budget']['max_seconds'], 21600)
        for _ in range(3):
            same, _ = self.execute()
            self.assertEqual(same['exploration_id'], result['exploration_id'])
        self.assertGreaterEqual(len(self.prompts), 1)

    def test_question_exploration_is_independent_of_paused_onboarding(self):
        from meetingvar.agent.policy import AgentBudget
        from meetingvar.memory.repository_explorer import explore
        self.source()
        self.model_failure = 'budget'
        failed, _ = self.execute()
        self.assert_unfinished(failed)
        self.assertEqual(failed['job_status'], 'paused_budget')
        before = copy.deepcopy(self.jobs.snapshot(self.root))
        self.model_failure = ''
        question = explore(self.root, query='Alpha delivery', scope='question', limit=4,
                           model_client=self.model_client, model_budget=AgentBudget(max_seconds=60))
        self.assertTrue(question['phase_complete'], question)
        self.assertFalse(question['complete'])
        self.assertEqual(self.jobs.snapshot(self.root)['model_budget'], before['model_budget'])
        self.assertEqual(self.jobs.snapshot(self.root)['exploration_id'], before['exploration_id'])

    def test_completed_revision_does_not_reindex_real_database(self):
        self.source()
        result, _ = self.execute()
        self.assert_oriented(result)
        path = self.root / 'meetingvar' / 'rag' / 'index.sqlite3'
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        before = connection.execute('SELECT id,content_hash,updated_at FROM documents').fetchall()
        connection.close()
        count = len(self.prompts)
        again, _ = self.execute()
        self.assert_oriented(again)
        self.assertEqual(again['index_report'], result['index_report'])
        self.assertEqual(len(self.prompts), count)
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            self.assertEqual(connection.execute('SELECT id,content_hash,updated_at FROM documents').fetchall(), before)
        finally:
            connection.close()

    def test_changes_during_real_commit_are_automatically_processed(self):
        self.source(text='Alpha GATE_CHANGE delivery.')
        self.embedder.block_marker = 'GATE_CHANGE'
        result, failures = {}, []
        def worker():
            try:
                row, _ = self.execute()
                result.update(row)
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(self.embedder.entered.wait(10))
            self.source('arrived.md', 'Alpha arrived during index build.')
        finally:
            self.embedder.release.set()
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures, failures)
        self.assert_oriented(result)
        self.assertEqual(result['totals']['documents'], 2)
        self.assertEqual(result['extracted'], 2)
        self.assertEqual(self.committed_counts(), {'documents': 2, 'chunks': 2})


if __name__ == '__main__':
    raise SystemExit('Use test_workspace_rag_runner.py to enforce temp cwd, APPDATA, network and credential fences.')
